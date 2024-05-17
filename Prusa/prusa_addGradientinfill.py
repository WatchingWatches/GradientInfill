#!/usr/bin/env python3
"""
Gradient Infill for 3D prints.
Prusa slicer

License: MIT
Author: Stefan Hermann - CNC Kitchen
Fork Author: Benedikt Jansson
Version: 1.0
"""
import re
import sys
from collections import namedtuple
from enum import Enum
from typing import List, Tuple
import traceback

__version__ = '1.0'

""" Status comments:
- fine: no changes needed
- changed: adapted to different slicer

Only accepts G1/G0 commands and relative extrusion
Please read the README.md for slicer settings guide

    
Features:
    first line gets removed with slicer information
        solves issue with incorrect gcode preview in Prusa gcode viewer
        
    run the script directly after slicing with input dialog for script variables
    
    warns you if no changes were made to the file
    
    gives error message inside of the terminal
    
Future Features:
    #TODO
    give warnings if G2/G3 are used and not realative extrusion
    automatically search for infill type

    
Experiment with the different input values and djust carefully.
For me 350 MAX_FLOW was too much 250 worked better.
Nowdays the linewidth is often higher then the nozzle size, which wasn't the standart 5 years ago.

RECOMMENDATION:
After running the script carefully look at the gcode preview and compare with the original gcode.
I use prusa gcode prieviewer and a notepad++ plugin to compare the files.

First test it with small files. Large files can take several minutes to compute.
"""

class InfillType(Enum):
    """Enum for infill type."""

    SMALL_SEGMENTS = 1  # infill with small segments like honeycomb or gyroid
    LINEAR = 2  # linear infill like rectilinear or triangles


Point2D = namedtuple('Point2D', 'x y')
Segment = namedtuple('Segment', 'point1 point2')

# EDIT this section for your creation parameters
# if the filenames have the same name the original file will be overwritten
# names only used if run_in_slicer = False
INPUT_FILE_NAME = "test.gcode"
OUTPUT_FILE_NAME = "prusa_script_result.gcode"

run_in_slicer = True
dialog_in_slicer = False # use different parameters inside of the slicer via dialog
remove_slicer_info = True # remove first line with slicer information for realistic gcode preview

BOTTOM_LAYERS = 4 #182 for the other alternative
INFILL_TYPE = InfillType.SMALL_SEGMENTS

# the following values will be used as default values if run_in_slicer = True
MAX_FLOW = 250.0  # maximum extrusion flow
MIN_FLOW = 60.0  # minimum extrusion flow
GRADIENT_THICKNESS = 6.0  # thickness of the gradient (max to min) in mm
GRADIENT_DISCRETIZATION = 4.0  # only applicable for linear infills; number of segments within the
# gradient(segmentLength=gradientThickness / gradientDiscretization); use sensible values to not overload the printer

# End edit


class Section(Enum):
    """Enum for section type."""

    NOTHING = 0
    INNER_WALL = 1
    INFILL = 2

# fine
def dist(segment: Segment, point: Point2D) -> float:
    """Calculate the distance from a point to a line with finite length.

    Args:
        segment (Segment): line used for distance calculation
        point (Point2D): point used for distance calculation

    Returns:
        float: distance between ``segment`` and ``point``
    """
    px = segment.point2.x - segment.point1.x
    py = segment.point2.y - segment.point1.y
    norm = px * px + py * py
    u = ((point.x - segment.point1.x) * px + (point.y - segment.point1.y) * py) / float(norm)
    if u > 1:
        u = 1
    elif u < 0:
        u = 0
    x = segment.point1.x + u * px
    y = segment.point1.y + u * py
    dx = x - point.x
    dy = y - point.y

    return (dx * dx + dy * dy) ** 0.5

# fine
def get_points_distance(point1: Point2D, point2: Point2D) -> float:
    """Calculate the euclidean distance between two points.

    Args:
        point1 (Point2D): first point
        point2 (Point2D): second point

    Returns:
        float: euclidean distance between the points
    """
    return ((point1.x - point2.x) ** 2 + (point1.y - point2.y) ** 2) ** 0.5

# fine
def min_distance_from_segment(segment: Segment, segments: List[Segment]) -> float:
    """Calculate the minimum distance from the midpoint of ``segment`` to the nearest segment in ``segments``.

    Args:
        segment (Segment): segment to use for midpoint calculation
        segments (List[Segment]): segments list

    Returns:
        float: the smallest distance from the midpoint of ``segment`` to the nearest segment in the list
    """
    middlePoint = Point2D((segment.point1.x + segment.point2.x) / 2, (segment.point1.y + segment.point2.y) / 2)

    return min(dist(s, middlePoint) for s in segments)

# use re.compile to make it quicker
prog_searchX = re.compile(r"X(\d*\.?\d*)")
prog_searchY = re.compile(r"Y(\d*\.?\d*)")

def getXY(currentLine: str) -> Point2D:
    """Create a ``Point2D`` object from a gcode line.

    Args:
        currentLine (str): gcode line

    Raises:
        SyntaxError: when the regular expressions cannot find the relevant coordinates in the gcode

    Returns:
        Point2D: the parsed coordinates
    """
    #searchX = re.search(r"X(\d*\.?\d*)", currentLine)
    #searchY = re.search(r"Y(\d*\.?\d*)", currentLine)
    
    searchX = prog_searchX.search(currentLine)
    searchY = prog_searchY.search(currentLine)
    
    if searchX and searchY:
        elementX = searchX.group(1)
        elementY = searchY.group(1)
    else:
        raise SyntaxError(f'Gcode file parsing error for line {currentLine}')
        
    
    return Point2D(float(elementX), float(elementY))
    


def mapRange(a: Tuple[float, float], b: Tuple[float, float], s: float) -> float:
    """Calculate a multiplier for the extrusion value from the distance to the perimeter.

    Args:
        a (Tuple[float, float]): a tuple containing:
            - a1 (float): the minimum distance to the perimeter (always zero at the moment)
            - a2 (float): the maximum distance to the perimeter where the interpolation is performed
        b (Tuple[float, float]): a tuple containing:
            - b1 (float): the maximum flow as a fraction
            - b2 (float): the minimum flow as a fraction
        s (float): the euclidean distance from the middle of a segment to the nearest perimeter

    Returns:
        float: a multiplier for the modified extrusion value
    """
    (a1, a2), (b1, b2) = a, b

    return b1 + ((s - a1) * (b2 - b1) / (a2 - a1))


def get_extrusion_command(x: float, y: float, extrusion: float) -> str:
    """Format a gcode string from the X, Y coordinates and extrusion value.

    Args:
        x (float): X coordinate
        y (float): Y coordinate
        extrusion (float): Extrusion value

    Returns:
        str: Gcode line
    """
    return "G1 X{} Y{} E{}\n".format(round(x, 3), round(y, 3), round(extrusion, 5))

# changed
def is_begin_layer_line(line: str) -> bool:
    """Check if current line is the start of a layer section.

    Args:
        line (str): Gcode line

    Returns:
        bool: True if the line is the start of a layer section
    """
    # use ; simple layer change instead or insert this in layer change custom gcode
    return line.startswith(";LAYER_CHANGE")


def is_begin_inner_wall_line(line: str) -> bool:
    """Check if current line is the start of an inner wall section.

    Args:
        line (str): Gcode line

    Returns:
        bool: True if the line is the start of an inner wall section
    """
    # use ;TYPE:Perimeter instead
    return line.startswith(";TYPE:Perimeter")

# changed
def is_end_inner_wall_line(line: str) -> bool:
    """Check if current line is the start of an outer wall section.

    Args:
        line (str): Gcode line

    Returns:
        bool: True if the line is the start of an outer wall section
    """
    # ;TYPE:External perimeter
    return line.startswith(";TYPE:External perimeter")

# fine
def is_extrusion_line(line: str) -> bool:
    """Check if current line is a standard printing segment.

    Args:
        line (str): Gcode line

    Returns:
        bool: True if the line is a standard printing segment
    """
    return "G1" in line and " X" in line and "Y" in line and "E" in line

# changed
def is_begin_infill_segment_line(line: str) -> bool:
    """Check if current line is the start of an infill.

    Args:
        line (str): Gcode line

    Returns:
        bool: True if the line is the start of an infill section
    """
    return line.startswith(";TYPE:Internal infill")


lines = []
# change to use search patterns instead of finding elements in string
def process_gcode(
    input_file_name: str,
    output_file_name: str,
    infill_type: InfillType,
    max_flow: float,
    min_flow: float,
    gradient_thickness: float,
    gradient_discretization: float,
    bottom_layers: int = 0,
) -> None:
    """Parse input Gcode file and modify infill portions with an extrusion width gradient."""
    #global edit, currentLine, currentSection
    prog_move = re.compile(r'^G[0-1].*X.*Y')
    prog_extrusion = re.compile(r'^G1.*X.*Y.*E')
    prog_type = re.compile(r'^;TYPE:')
    
    edit = 0
    layer_count = 1
    currentSection = Section.NOTHING
    lastPosition = Point2D(-10000, -10000)
    gradientDiscretizationLength = gradient_thickness / gradient_discretization

    with open(input_file_name, "r") as gcodeFile:
        if remove_slicer_info:
                first_line = True # delete first line due to incorrect gcode preview
        else:
            first_line = False

        for currentLine in gcodeFile:
            if first_line:
                first_line = False
                continue

            writtenToFile = 0
            
            if is_begin_layer_line(currentLine):
                perimeterSegments = []
                layer_count += 1

            if layer_count  > bottom_layers:    
                # search if it indicates a type
                if prog_type.search(currentLine):
                    if is_begin_inner_wall_line(currentLine):
                        currentSection = Section.INNER_WALL
                        
                    elif is_end_inner_wall_line(currentLine):
                        currentSection = Section.INNER_WALL
                        
                        continue

                    elif is_begin_infill_segment_line(currentLine):
                        currentSection = Section.INFILL
                        lines.append(currentLine)
                        continue
                    # irrelevent type, this was in the cura version searching for ; at the end
                    else:
                        currentSection = Section.NOTHING

                if currentSection == Section.INNER_WALL:
                    writtenToFile = 1 # delete outer perimeter
                            
                if currentSection == Section.INNER_WALL and is_extrusion_line(currentLine):
                    perimeterSegments.append(Segment(getXY(currentLine), lastPosition))

                
                if currentSection == Section.INFILL:
                    if "F" in currentLine and "G1" in currentLine:
                        # python3.6+ f-string variant:
                        # outputFile.write("G1 F{ re.search(r"F(\d*\.?\d*)", currentLine).group(1)) }\n"
                        searchSpeed = re.search(r"F(\d*\.?\d*)", currentLine)
                        if searchSpeed:
                            lines.append("G1 F{}\n".format(searchSpeed.group(1)))
                        else:
                            raise SyntaxError(f'Gcode file parsing error for line {currentLine}')
                    if prog_extrusion.search(currentLine):
                        currentPosition = getXY(currentLine)
                        splitLine = currentLine.split(" ")

                        if infill_type == InfillType.LINEAR:
                            # find extrusion length
                            for element in splitLine:
                                if "E" in element:
                                    extrusionLength = float(element[1:])
                            segmentLength = get_points_distance(lastPosition, currentPosition)
                            segmentSteps = segmentLength / gradientDiscretizationLength
                            extrusionLengthPerSegment = extrusionLength / segmentSteps
                            segmentDirection = Point2D(
                                (currentPosition.x - lastPosition.x) / segmentLength * gradientDiscretizationLength,
                                (currentPosition.y - lastPosition.y) / segmentLength * gradientDiscretizationLength,
                            )
                            if segmentSteps >= 2:
                                for step in range(int(segmentSteps)):
                                    segmentEnd = Point2D(
                                        lastPosition.x + segmentDirection.x, lastPosition.y + segmentDirection.y
                                    )
                                    shortestDistance = min_distance_from_segment(
                                        Segment(lastPosition, segmentEnd), perimeterSegments
                                    )
                                    if shortestDistance < gradient_thickness:
                                        segmentExtrusion = extrusionLengthPerSegment * mapRange(
                                            (0, gradient_thickness), (max_flow / 100, min_flow / 100), shortestDistance
                                        )
                                    else:
                                        segmentExtrusion = extrusionLengthPerSegment * min_flow / 100

                                    lines.append(get_extrusion_command(segmentEnd.x, segmentEnd.y, segmentExtrusion))

                                    lastPosition = segmentEnd
                                # MissingSegment
                                segmentLengthRatio = get_points_distance(lastPosition, currentPosition) / segmentLength

                                lines.append(
                                    get_extrusion_command(
                                        currentPosition.x,
                                        currentPosition.y,
                                        segmentLengthRatio * extrusionLength * max_flow / 100,
                                    )
                                )
                            else:
                                outPutLine = ""
                                for element in splitLine:
                                    if "E" in element:
                                        outPutLine = outPutLine + "E" + str(round(extrusionLength * max_flow / 100, 5))
                                    else:
                                        outPutLine = outPutLine + element + " "
                                outPutLine = outPutLine + "\n"
                                lines.append(outPutLine)
                            writtenToFile = 1

                        # gyroid or honeycomb
                        if infill_type == InfillType.SMALL_SEGMENTS:
                            shortestDistance = min_distance_from_segment(
                                Segment(lastPosition, currentPosition), perimeterSegments
                            )

                            outPutLine = ""
                            if shortestDistance < gradient_thickness:
                                for element in splitLine:
                                    if "E" in element:
                                        newE = float(element[1:]) * mapRange(
                                            (0, gradient_thickness), (max_flow / 100, min_flow / 100), shortestDistance
                                        )
                                        outPutLine = outPutLine + "E" + str(round(newE, 5))
                                    else:
                                        outPutLine = outPutLine + element + " "
                                outPutLine = outPutLine + "\n"
                                lines.append(outPutLine)
                                writtenToFile = 1
                                
                    # infill type resetted broke the script
                    # this was probably used as a "safety" feature
                    #if ";" in currentLine:
                    #    currentSection = Section.NOTHING

                # line with move
                if prog_move.search(currentLine):
                    lastPosition = getXY(currentLine)

            # write uneditedLine
            if writtenToFile == 0:
                lines.append(currentLine)
            else:
                edit += 1
        
        with open(output_file_name, "w") as outputFile:
            for line in lines:
                outputFile.write("%s"  % line)
        
        # check if the script did anything
        if edit == 0:
            print('No changes were made to the file! Press enter and check the script')
            if run_in_slicer:
                input()


#if __name__ == '__main__':
#    process_gcode(
#        INPUT_FILE_NAME, OUTPUT_FILE_NAME, INFILL_TYPE, MAX_FLOW, MIN_FLOW, GRADIENT_THICKNESS, GRADIENT_DISCRETIZATION
#    )

# use try method to get error message from script
try:
    if run_in_slicer:
        file_path = sys.argv[1] # the path of the gcode given by the slicer
        
        
        if dialog_in_slicer:
            # repeat process up to 3 times if inserted values are incorrect
            for _ in range(3):
                print('script called:', sys.argv[0],'\n')
                print('Use default values (declared in the script)? [y] to proceed')
                default = str(input())
                if default == 'y':
                    break
                
                print('Input MAX_FLOW and press enter (default 350)')
                MAX_FLOW = int(input())
                
                print('Input MIN_FLOW and press enter (default 50)')
                MIN_FLOW = int(input())
                
                print('Input GRADIENT_THICKNESS and press enter (default 6.0)')
                GRADIENT_THICKNESS = float(input())
                
                print('Input GRADIENT_DISCRETIZATION and press enter (default 4.0)')
                GRADIENT_DISCRETIZATION = float(input())
                
                print('Input INFILL_TYPE choose [0] for SMALL_SEGMENTS and [1] for LINEAR:')
                choose_infill_type = int(input())
                if choose_infill_type == 0:
                    INFILL_TYPE = InfillType.SMALL_SEGMENTS
                else:
                    INFILL_TYPE = InfillType.LINEAR
                    
                print('Are all values correct? [y] to proceed')
                correct = str(input())
                
                if correct == 'y':
                    break
            
        # changed out path
        process_gcode(
            file_path, file_path, INFILL_TYPE, MAX_FLOW, MIN_FLOW, GRADIENT_THICKNESS, GRADIENT_DISCRETIZATION, BOTTOM_LAYERS
        )
        
    else:
        process_gcode(
            INPUT_FILE_NAME, OUTPUT_FILE_NAME, INFILL_TYPE, MAX_FLOW, MIN_FLOW, GRADIENT_THICKNESS, GRADIENT_DISCRETIZATION, BOTTOM_LAYERS
        )
        
except Exception:
    traceback.print_exc()
    if run_in_slicer:
        print('Press enter to close window')
        print('If you need help open an issue on my Github at:https://github.com/WatchingWatches')
        print('Please share all of the settings you were using and the error message')
        input()
    