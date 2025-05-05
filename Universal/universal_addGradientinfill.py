#!/usr/bin/env python3
"""
Gradient Infill for 3D prints.
Orca, Bambu, Prusa slicer

License: MIT
Author: Stefan Hermann - CNC Kitchen
Fork Author: Benedikt Jansson - WatchingWatches
Version: 1.0
"""
import re
import sys
from collections import namedtuple
from enum import Enum
from typing import List, Tuple
import traceback
import time
from math import pi, degrees, acos


__version__ = '1.0'

"""

Only accepts G1/G0 commands and relative extrusion
Please read the README.md for slicer settings guide

"""

class InfillType(Enum):
    """Enum for infill type."""

    SMALL_SEGMENTS = 1  # infill with small segments like honeycomb or gyroid
    LINEAR = 2  # linear infill like rectilinear or triangles

class Slicer(Enum):
    """Enum for slicer"""

    SEARCH = 0 # for search slicer feature
    ORCA = 1 # if you use a bambulab printer choose Bambu
    BAMBU = 2
    PRUSA = 3
    CURA = 4 # untested


Point2D = namedtuple('Point2D', 'x y')
Segment = namedtuple('Segment', 'point1 point2')

# EDIT this section for your creation parameters
# if the filenames have the same name the original file will be overwritten
# names only used if run in IDE
INPUT_FILE_NAME: str = r"Universal\bunny_orca.gcode"
OUTPUT_FILE_NAME: str = r"C:\Users\bjans\Downloads\gradinfilltest_res.gcode"

# Warning there is just one file as output from the slicer, which means you can't compare it to the original
dialog_in_slicer: bool = True # use different parameters inside of the slicer via dialog else the following values are used
REMOVE_SLICER_INFO: bool = False # remove first line with slicer information for realistic gcode preview only for prusa, orca slicer
D_F: float = 1.75  # diameter of the filament in mm
# this setting is only relevant for SMALL_SEGMENTS infill when disabled the infill outside of the GRADIENT_THICKNESS isn't changed
THIN_INNER_CORE: bool = True 
# automatically search for the slicer with: Slicer.SEARCH
Slicer_Type: Slicer = Slicer.SEARCH # if manually assigned and you use a bambulab printer with Orca slicer choose Bambu!

MAX_FLOW: float = 220.0  # maximum extrusion flow
MIN_FLOW: float = 70.0  # minimum extrusion flow
GRADIENT_THICKNESS: float = 12.0  # thickness of the gradient (max to min) in mm
GRADIENT_DISCRETIZATION: float = 4.0  # only applicable for linear infills; number of segments within the
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
    try:
        u = ((point.x - segment.point1.x) * px + (point.y - segment.point1.y) * py) / float(norm)
    except ZeroDivisionError:
        # error when norm = 0 machine accuracy
        return 0
    
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

# changed
def min_distance_from_segment(segment: Segment, segments: List[Segment], return_seg: bool = False) -> float:
    """Calculate the minimum distance from the midpoint of ``segment`` to the nearest segment in ``segments``.

    Args:
        segment (Segment): segment to use for midpoint calculation
        segments (List[Segment]): segments list

    Returns:
        float: the smallest distance from the midpoint of ``segment`` to the nearest segment in the list
    """
    middlePoint = Point2D((segment.point1.x + segment.point2.x) / 2, (segment.point1.y + segment.point2.y) / 2)
    min_segment = min(segments, key=lambda s: dist(s, middlePoint))
    min_value = dist(min_segment, middlePoint)
    if return_seg:
        return min_value, min_segment
    else:
        return min_value

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
    if Slicer_Type == Slicer.ORCA or Slicer_Type == Slicer.PRUSA:
        return line.startswith(";LAYER_CHANGE")
    elif Slicer_Type == Slicer.BAMBU:
        return line.startswith("; CHANGE_LAYER")
    elif Slicer_Type == Slicer.CURA:
        return line.startswith(";LAYER:")


# changed
def is_begin_inner_wall_line(line: str) -> bool:
    """Check if current line is the start of an inner wall section.

    Args:
        line (str): Gcode line

    Returns:
        bool: True if the line is the start of an inner wall section
    """
    if Slicer_Type == Slicer.ORCA:
        return line.startswith(";TYPE:Inner wall")
    elif Slicer_Type == Slicer.PRUSA:
        return line.startswith(";TYPE:Perimeter")
    elif Slicer_Type == Slicer.BAMBU:
        return line.startswith("; FEATURE: Inner wall")
    elif Slicer_Type == Slicer.CURA:
        return line.startswith(";TYPE:WALL-INNER")


# changed
def is_end_inner_wall_line(line: str) -> bool: #TODO delete?
    """Check if current line is the start of an outer wall section.

    Args:
        line (str): Gcode line

    Returns:
        bool: True if the line is the start of an outer wall section
    """
    if Slicer_Type == Slicer.ORCA:
        return line.startswith(";TYPE:Outer wall")
    elif Slicer_Type == Slicer.PRUSA:
        return line.startswith(";TYPE:External perimeter")
    elif Slicer_Type == Slicer.BAMBU:
        return line.startswith("; FEATURE: Outer wall")
    elif Slicer_Type == Slicer.CURA:
        return line.startswith(";TYPE:WALL-OUTER")


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
    if Slicer_Type == Slicer.ORCA:
        return line.startswith(";TYPE:Sparse infill")
    elif Slicer_Type == Slicer.PRUSA:
        return line.startswith(";TYPE:Internal infill")
    elif Slicer_Type == Slicer.BAMBU:
        return line.startswith("; FEATURE: Sparse infill")
    elif Slicer_Type == Slicer.CURA:
        return line.startswith(";TYPE:FILL")

def is_start_gcode(line: str)-> bool:
    """Check if current line indicates start gcode.

    Args:
        line (str): Gcode line

    Returns:
        bool: True if the line is the start gcode
    """
    if Slicer_Type == Slicer.ORCA or Slicer_Type == Slicer.PRUSA:
        return line.startswith(";TYPE:Custom")
    elif Slicer_Type == Slicer.BAMBU:
        return line.startswith("; FEATURE: Custom")
    elif Slicer_Type == Slicer.CURA:
        return False #TODO
        #return line.startswith(";Generated with Cura_SteamEngine")

def control_flow(hotend_max_flow: float, extrusionLength:float, distance:float, d_f:float)-> str:
    """Calculate new feedrate to stay at the limit of the hotend maximum flow.

    Args:
        hotend_max_flow (float): maximum volumetric flow of the hotend in mm^3/s
        extrusionLength (float): length of the extruded filament (E value) in mm
        distance (float): distance of the extrusion in mm
        d_f (float): diameter of the filament in mm
        current_flow (float): current flow value in mm^3/s

    Returns:
        str: Gcode line with new feedrate
    """
    F = round((hotend_max_flow*distance*60*4)/(extrusionLength*(d_f**2)*pi), 3)
    return "G1 F{}\n".format(F) 

def is_collinear(wall:Segment, p_0:Point2D, p_1:Point2D)->bool:
    """ Check if the infill segment is nearly collinear with the wall segment.
    Args:
        wall (Segment): The wall segment represented by two points (point1 and point2).
        p_0 (Point2D): The starting point of the infill segment.
        p_1 (Point2D): The ending point of the infill segment.
    Returns:
        bool: True if the infill segment is collinear with the wall segment
    """
    def clamp(value, min_value, max_value)-> float:
        return max(min(value, max_value), min_value)
    try:
        v_wall = Point2D(wall.point2.x - wall.point1.x, wall.point2.y - wall.point1.y)
        norm_wall = Point2D(*(1 / get_points_distance(wall.point1, wall.point2) * v for v in (v_wall.x, v_wall.y)))
        v_infill = Point2D(p_1.x - p_0.x, p_1.y - p_0.y)
        norm_infill = Point2D(*((1 / get_points_distance(p_0, p_1)) * v for v in v_infill))
        neg_norm_infill = Point2D(*((-1) * v for v in norm_infill))
        
        dot_product = norm_wall.x * norm_infill.x + norm_wall.y * norm_infill.y
        neg_dot_product = norm_wall.x * neg_norm_infill.x + norm_wall.y * neg_norm_infill.y
        # add boundries 
        dot_product = clamp(dot_product, -1, 1)
        neg_dot_product = clamp(neg_dot_product, -1, 1)

        angle = min(abs(degrees(acos(dot_product))), abs(degrees(acos(neg_dot_product))))
    except ZeroDivisionError:
        angle = 0
    return angle < 15

lines = []
# change to use search patterns instead of finding elements in string
def process_gcode(
    input_file_name: str,
    output_file_name: str,
    max_flow: float,
    min_flow: float,
    gradient_thickness: float,
    gradient_discretization: float,
    d_f: float,
    thin_inner_core: bool,
) -> None:
    """Parse input Gcode file and modify infill portions with an extrusion width gradient."""
    global currentLine, Slicer_Type
    prog_move = re.compile(r'^G[0-1].*X.*Y')
    prog_extrusion = re.compile(r'^G1.*X.*Y.*E')
    if Slicer_Type == Slicer.BAMBU:
        prog_type = re.compile(r'^; FEATURE:')
    else:
        prog_type = re.compile(r'^;TYPE:')
    
    edit = 0
    ignore_pos = True
    is_old_speed = False
    currentSection = Section.NOTHING
    lastPosition = Point2D(-float('inf'), -float('inf')) # set infinate start point
    gradientDiscretizationLength = gradient_thickness / gradient_discretization
    small_segments_infill_type = ['gyroid', 'honeycomb', '3dhoneycomb'] # list of all small segments infill types

    with open(input_file_name, "r") as gcodeFile:        
        gcode = gcodeFile.readlines()
        found_flow = False
        found_infill_type = False
        # find the volumetric flow limit of the filament from slicer settings (probaly not compatible with cura) and reversing isn't optimal for bambu
        for line in reversed(gcode):
            if line.startswith('; filament_max_volumetric_speed ='):
                hotend_max_flow = min(map(float, line.split('=')[-1].split(','))) # different filaments are sperated by , minimum is used
                print(f'Maximum flow is: {hotend_max_flow} [mm^3/s]')
                found_flow = True
            if line.startswith('; fill_pattern = ') or line.startswith('; sparse_infill_pattern = '):
                infill_type = line.split('=')[-1].strip().split()[0]
                if infill_type in small_segments_infill_type:
                    infill_type = InfillType.SMALL_SEGMENTS
                else:
                    infill_type = InfillType.LINEAR
                found_infill_type = True
            if all((found_infill_type, found_flow)):
                print(infill_type)
                break # task finished

        for currentLine in gcode:
            # find the slicer automatically
            if Slicer_Type == Slicer.SEARCH:
                if currentLine.startswith("; generated by PrusaSlicer"):
                    Slicer_Type = Slicer.PRUSA
                    prog_type = re.compile(r'^;TYPE:')
                    if REMOVE_SLICER_INFO:
                        continue # delete first line due to incorrect gcode preview

                elif currentLine.startswith("; BambuStudio"):
                    Slicer_Type = Slicer.BAMBU
                    prog_type = re.compile(r'^; FEATURE:')

                elif currentLine.startswith(";Generated with Cura_SteamEngine"):
                    Slicer_Type = Slicer.CURA
                    prog_type = re.compile(r'^;TYPE:')

                elif currentLine.startswith("; generated by OrcaSlicer"):
                    Slicer_Type = Slicer.ORCA
                    prog_type = re.compile(r'^;TYPE:')
                    if REMOVE_SLICER_INFO:
                        continue # delete first line due to incorrect gcode preview
                    
            # check if Orca slicer with bambu printer    
            if Slicer_Type == Slicer.ORCA and currentLine.startswith("; printer_model = Bambu"):
                Slicer_Type = Slicer.BAMBU
                prog_type = re.compile(r'^; FEATURE:')

            # get line width to determine critical zone next to wall
            if currentLine.startswith("; infill extrusion width = ") or currentLine.startswith("; sparse_infill_line_width = "):
                infill_linewidth = float(currentLine.split("=")[-1].split("mm")[0])
            if currentLine.startswith("; perimeters extrusion width = ") or currentLine.startswith("; inner_wall_line_width = "):
                inner_wall_linewidth = float(currentLine.split("=")[-1].split("mm")[0])

            writtenToFile = 0
                
            if is_begin_layer_line(currentLine):
                perimeterSegments = []
                
            # search if it indicates a type
            if prog_type.search(currentLine):
                if Slicer_Type == Slicer.SEARCH:
                    raise SyntaxError("Slicer not found.")
                    
                # ignore start or end gcode
                ignore_pos = is_start_gcode(currentLine)

                if is_begin_inner_wall_line(currentLine):
                    currentSection = Section.INNER_WALL
                elif is_begin_infill_segment_line(currentLine):
                    currentSection = Section.INFILL
                # irrelevent type, this was in the cura version searching for ; at the end
                else:
                    currentSection = Section.NOTHING

                # write to file and continue
                lines.append(currentLine)
                continue

            elif currentLine.startswith('G2 ') or currentLine.startswith('G3 '):
                raise TypeError("G2/3 commands aren't supported! Disable Arc fitting in your slicer")
                    
            if currentSection == Section.INNER_WALL and is_extrusion_line(currentLine):
                perimeterSegments.append(Segment(getXY(currentLine), lastPosition))

            if currentSection == Section.INFILL:
                if "F" in currentLine and "G1" in currentLine:
                    searchSpeed = re.search(r"F(\d*\.?\d*)", currentLine)
                    if searchSpeed:
                        infill_speed = searchSpeed.group(1)
                        infill_begin = True
                        # previous double F command fixed
                        if "E" in currentLine:
                            lines.append("G1 F{}\n".format(infill_speed))
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
                        # calculate original infill flow once per layer TODO might lead to issue with multiple objects with different settings
                        if infill_begin:
                            infill_flow = (float(infill_speed)*(d_f**2)*pi*extrusionLength) / (4*segmentLength*60)
                            infill_begin = False

                        if segmentSteps >= 2:
                            for _ in range(int(segmentSteps)):
                                segmentEnd = Point2D(
                                    lastPosition.x + segmentDirection.x, lastPosition.y + segmentDirection.y
                                )
                                shortestDistance = min_distance_from_segment(
                                    Segment(lastPosition, segmentEnd), perimeterSegments
                                )
                                if shortestDistance < gradient_thickness:
                                    flow_factor = mapRange(
                                        (0, gradient_thickness), (max_flow / 100, min_flow / 100), shortestDistance
                                    )
                                    segmentExtrusion = extrusionLengthPerSegment * flow_factor
                                    
                                else:
                                    segmentExtrusion = extrusionLengthPerSegment * min_flow / 100
                                    flow_factor = min_flow / 100
                                    
                                # check for flow limit
                                current_flow = infill_flow * flow_factor
                                if current_flow > hotend_max_flow:
                                    new_feedrate = control_flow(hotend_max_flow, extrusionLengthPerSegment*flow_factor, gradientDiscretizationLength, d_f)
                                    lines.append(new_feedrate + get_extrusion_command(segmentEnd.x, segmentEnd.y, segmentExtrusion))
                                    is_old_speed = False
                                elif is_old_speed:
                                    lines.append(get_extrusion_command(segmentEnd.x, segmentEnd.y, segmentExtrusion))
                                else:
                                    is_old_speed = True
                                    lines.append("G1 F{}\n".format(infill_speed) + get_extrusion_command(segmentEnd.x, segmentEnd.y, segmentExtrusion))

                                lastPosition = segmentEnd
                            # MissingSegment
                            segmentLengthRatio = get_points_distance(lastPosition, currentPosition) / segmentLength
                            current_flow = infill_flow * max_flow / 100
                            if current_flow > hotend_max_flow:
                                new_feedrate = control_flow(hotend_max_flow, extrusionLength * max_flow / 100, segmentLength, d_f)
                                
                                lines.append(new_feedrate +
                                                get_extrusion_command(
                                                    currentPosition.x,
                                                    currentPosition.y,
                                                    segmentLengthRatio * extrusionLength * max_flow / 100,
                                                )
                                            )
                            else:
                                lines.append(
                                    get_extrusion_command(
                                        currentPosition.x,
                                        currentPosition.y,
                                        segmentLengthRatio * extrusionLength * max_flow / 100,
                                    )
                                )
                        else: # not splitted line
                            outPutLine = ""
                            for element in splitLine:
                                if "E" in element:
                                    outPutLine = outPutLine + "E" + str(round(extrusionLength * max_flow / 100, 5))
                                    current_flow = infill_flow * max_flow / 100
                                else:
                                    outPutLine = outPutLine + element + " "

                            if current_flow > hotend_max_flow:
                                new_feedrate = control_flow(hotend_max_flow, extrusionLength * max_flow / 100, segmentSteps, d_f)
                                outPutLine = new_feedrate + outPutLine + "\n"
                            else:
                                outPutLine = outPutLine + "\n"
                            lines.append(outPutLine)
                        writtenToFile = 1

                    # gyroid or 3d/honeycomb
                    if infill_type == InfillType.SMALL_SEGMENTS:
                        seg_info = min_distance_from_segment(
                            Segment(lastPosition, currentPosition), perimeterSegments, return_seg=True
                        )
                        shortestDistance = seg_info[0]
                        min_seg = seg_info[1]
                        collinear = is_collinear(min_seg, lastPosition, currentPosition)

                        outPutLine = ""
                        if shortestDistance < gradient_thickness:
                            for element in splitLine:
                                if "E" in element:
                                    flow_factor = mapRange(
                                        (0, gradient_thickness), (max_flow / 100, min_flow / 100), shortestDistance
                                    )
                                    # prevent unclean extrusion near the wall
                                    critical_distance = (infill_linewidth + inner_wall_linewidth) * 1.4 / 2
                                    if shortestDistance <= critical_distance and collinear:
                                        flow_factor = 1
                                    newE = float(element[1:]) * flow_factor
                                    # calculate original infill flow once per layer
                                    if infill_begin:
                                        segmentLength = get_points_distance(lastPosition, currentPosition)
                                        infill_flow = (float(infill_speed)*(d_f**2)*pi*float(element[1:])) / (4*segmentLength*60)
                                        infill_begin = False

                                        if infill_flow > hotend_max_flow + 0.5: #TODO delete? will trigger if wrong max flow is set only happens with two filaments
                                            print('Your infill flow is higher, than the hotend limit in the script!')
                                            print('Please adjust either your slicer or script settings')
                                            print('Slicer Infill Flow:', infill_flow, 'Script Hotend Max Flow:', hotend_max_flow, '[mm^3/s]')
                                            input()
                                        
                                    outPutLine = outPutLine + "E" + str(round(newE, 5))
                                else:
                                    outPutLine = outPutLine + element + " "
                            current_flow = infill_flow * flow_factor
                            if current_flow > hotend_max_flow:
                                segmentLength = get_points_distance(lastPosition, currentPosition)
                                new_feedrate = control_flow(hotend_max_flow, newE, segmentLength, d_f)
                                is_old_speed = False
                                
                                outPutLine = new_feedrate + outPutLine + "\n"
                            
                            elif is_old_speed:
                                outPutLine = outPutLine + "\n"
                            else:
                                is_old_speed = True
                                outPutLine = "G1 F{}\n".format(infill_speed) + outPutLine + "\n"
                                
                            lines.append(outPutLine)
                            writtenToFile = 1

                        elif thin_inner_core:
                            # no need to check for maximum flow since it will be lowered
                            for element in splitLine:
                                if "E" in element:
                                    newE = float(element[1:]) * min_flow / 100
                                    outPutLine = outPutLine + "E" + str(round(newE, 5)) + "\n"
                                else:
                                    outPutLine = outPutLine + element + " "
                            
                            lines.append(outPutLine)
                            writtenToFile = 1
 

            # line with move
            if prog_move.search(currentLine) and not ignore_pos:
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
            print('No changes were made to the file!')
            print('Is this the right slicer?', Slicer_Type)
            print('if you use Orca slicer with a Bambu printer it should be BAMBU')

            if run_in_slicer:
                print('Press enter and check the script')
                input()


if __name__ == '__main__':
    try:
        # when more than one argument is parsed it's run by slicer
        run_in_slicer = len(sys.argv) > 1

        if run_in_slicer:
            file_path = sys.argv[1] # the path of the gcode given by the slicer
            if dialog_in_slicer:
                # repeat process up to 3 times if inserted values are incorrect
                for _ in range(3):
                    print('script called:', sys.argv[0],'\n')
                    print('Use default values (declared in the script)? [y] to proceed')
                    default = str(input())
                    if default == 'y':
                        print('Script is running please wait...')
                        break
                    
                    print('Input MAX_FLOW and press enter (default 350)')
                    MAX_FLOW = int(input())
                    
                    print('Input MIN_FLOW and press enter (default 50)')
                    MIN_FLOW = int(input())
                    
                    print('Input GRADIENT_THICKNESS and press enter (default 6.0)')
                    GRADIENT_THICKNESS = float(input())
                    
                    print('Input INFILL_TYPE choose [0] for SMALL_SEGMENTS and [1] for LINEAR:')
                    choose_infill_type = int(input()) #TODO update and find solution inside main program after finding infill type?
                    if choose_infill_type == 0:
                        INFILL_TYPE = InfillType.SMALL_SEGMENTS
                        print('Enable THIN_INNER_CORE ? [y] to enable')
                        if str(input()) == 'y':
                            THIN_INNER_CORE = True
                        else:
                            THIN_INNER_CORE = False
                    else:
                        INFILL_TYPE = InfillType.LINEAR
                        print('Input GRADIENT_DISCRETIZATION and press enter (default 4.0)')
                        GRADIENT_DISCRETIZATION = float(input())
                        
                    print('Are all values correct? [y] to proceed')
                    correct = str(input())
                    
                    if correct == 'y':
                        print('Script is running please wait...')
                        break
            start = time.time()   
            # changed out path
            process_gcode(
                file_path, file_path, MAX_FLOW, MIN_FLOW, GRADIENT_THICKNESS, GRADIENT_DISCRETIZATION, D_F, THIN_INNER_CORE
            )
            
        else:
            start = time.time()
            process_gcode(
                INPUT_FILE_NAME, OUTPUT_FILE_NAME, MAX_FLOW, MIN_FLOW, GRADIENT_THICKNESS, GRADIENT_DISCRETIZATION, D_F, THIN_INNER_CORE
            )
            
        print('Time to excecute:',time.time()- start) 
        
    except Exception:
        traceback.print_exc()
        
        print('currentLine:', currentLine)
        print('Press enter to close window')
        print('Is this the right slicer?', Slicer_Type)
        print('if you use Orca slicer with a Bambu printer it should be BAMBU')
        print('If you need help open an issue on my Github at: https://github.com/WatchingWatches/GradientInfill')
        print('Please share a .3mf file with all of the settings you were using and the error message')

        if run_in_slicer:
            input()