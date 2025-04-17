#!/usr/bin/env python3
"""
Gradient Infill for 3D prints.
Orca slicer

License: MIT
Author: Stefan Hermann - CNC Kitchen
Fork Author: Benedikt Jansson - WatchingWatches
Re-Forked Author Epiphany
Version: 1.0
"""

import re
import time
import traceback
from math import pi
from collections import namedtuple
from enum import Enum
from typing import List, Tuple

__version__ = '1.0'

# Only accepts G1/G0 commands and relative extrusion

class InfillType(Enum):
    SMALL_SEGMENTS = 1  # infill with small segments like honeycomb or gyroid
    LINEAR = 2          # linear infill like rectilinear or triangles

Point2D = namedtuple('Point2D', 'x y')
Segment = namedtuple('Segment', 'point1 point2')

# ====== Edit this section for your creation parameters ======

HOTEND_MAX_FLOW = 20.0   # maximum volumetric flow of the hotend in mm^3/s
D_F = 1.75               # filament diameter in mm
THIN_INNER_CORE = True   # if enabled, infill outside the gradient zone is reduced

INFILL_TYPE = InfillType.SMALL_SEGMENTS  # choose SMALL_SEGMENTS or LINEAR
MAX_FLOW = 550.0         # maximum extrusion flow (as percentage of original extrusion)
MIN_FLOW = 50.0          # minimum extrusion flow (as percentage of original extrusion)
GRADIENT_THICKNESS = 20.0 # thickness of the gradient zone (mm)
GRADIENT_DISCRETIZATION = 4.0  # number of segments to divide the gradient (only for LINEAR infill)
# =============================================================

class Section(Enum):
    NOTHING = 0
    INNER_WALL = 1
    INFILL = 2

def dist(segment: Segment, point: Point2D) -> float:
    """Calculate the distance from a point to a finite line segment."""
    px = segment.point2.x - segment.point1.x
    py = segment.point2.y - segment.point1.y
    norm = px * px + py * py
    try:
        u = ((point.x - segment.point1.x) * px + (point.y - segment.point1.y) * py) / float(norm)
    except ZeroDivisionError:
        return 0
    u = max(0, min(1, u))
    x = segment.point1.x + u * px
    y = segment.point1.y + u * py
    dx = x - point.x
    dy = y - point.y
    return (dx*dx + dy*dy) ** 0.5

def get_points_distance(point1: Point2D, point2: Point2D) -> float:
    """Calculate the Euclidean distance between two points."""
    return ((point1.x - point2.x)**2 + (point1.y - point2.y)**2) ** 0.5

def min_distance_from_segment(segment: Segment, segments: List[Segment]) -> float:
    """Find the minimum distance from the midpoint of a segment to a list of segments."""
    middlePoint = Point2D((segment.point1.x + segment.point2.x) / 2,
                          (segment.point1.y + segment.point2.y) / 2)
    return min(dist(s, middlePoint) for s in segments)

prog_searchX = re.compile(r"X(\d*\.?\d*)")
prog_searchY = re.compile(r"Y(\d*\.?\d*)")

def getXY(currentLine: str) -> Point2D:
    """Extract X and Y values from a G-code line as a Point2D object."""
    searchX = prog_searchX.search(currentLine)
    searchY = prog_searchY.search(currentLine)
    if searchX and searchY:
        return Point2D(float(searchX.group(1)), float(searchY.group(1)))
    else:
        raise SyntaxError(f'Gcode file parsing error for line {currentLine}')

def mapRange(a: Tuple[float, float], b: Tuple[float, float], s: float) -> float:
    """Interpolate a multiplier based on distance s."""
    (a1, a2), (b1, b2) = a, b
    return b1 + ((s - a1) * (b2 - b1) / (a2 - a1))

def get_extrusion_command(x: float, y: float, extrusion: float) -> str:
    """Format a G-code extrusion command."""
    return "G1 X{} Y{} E{}\n".format(round(x, 3), round(y, 3), round(extrusion, 5))

def control_flow(hotend_max_flow: float, extrusionLength: float, distance: float, d_f: float) -> str:
    """Calculate a new feedrate to limit the hotend flow."""
    F = round((hotend_max_flow * distance * 60 * 4) / (extrusionLength * (d_f**2) * pi), 3)
    return "G1 F{}\n".format(F)

# --- Simplified helper functions for Orca slicer only ---
def is_begin_layer_line(line: str) -> bool:
    return line.startswith(";LAYER_CHANGE")

def is_begin_inner_wall_line(line: str) -> bool:
    return line.startswith(";TYPE:Inner wall")

def is_end_inner_wall_line(line: str) -> bool:
    return line.startswith(";TYPE:Outer wall")

def is_begin_infill_segment_line(line: str) -> bool:
    return line.startswith(";TYPE:Sparse infill")

def is_start_gcode(line: str) -> bool:
    return line.startswith(";TYPE:Custom")

def is_extrusion_line(line: str) -> bool:
    return "G1" in line and " X" in line and "Y" in line and "E" in line

lines = []  # will collect output lines

def process_gcode(
    input_file_name: str,
    output_file_name: str,
    infill_type: InfillType,
    max_flow: float,
    min_flow: float,
    gradient_thickness: float,
    gradient_discretization: float,
    hotend_max_flow: float,
    d_f: float,
    thin_inner_core: bool,
) -> None:
    prog_move = re.compile(r'^G[0-1].*X.*Y')
    prog_extrusion = re.compile(r'^G1.*X.*Y.*E')
    prog_type = re.compile(r'^;TYPE:')
    
    # state variables
    edit = 0
    ignore_pos = True
    is_old_speed = False
    currentSection = Section.NOTHING
    lastPosition = Point2D(-10000, -10000)
    gradientDiscretizationLength = gradient_thickness / gradient_discretization

    with open(input_file_name, "r") as gcodeFile:
        for currentLine in gcodeFile:
            writtenToFile = False  # flag to check if currentLine got processed
            if is_begin_layer_line(currentLine):
                print("Starting new layer")
                perimeterSegments = []

            if prog_type.search(currentLine):
                if is_start_gcode(currentLine):
                    ignore_pos = True
                else:
                    ignore_pos = False

                if is_begin_inner_wall_line(currentLine):
                    currentSection = Section.INNER_WALL
                elif is_end_inner_wall_line(currentLine):
                    currentSection = Section.NOTHING
                elif is_begin_infill_segment_line(currentLine):
                    currentSection = Section.INFILL
                else:
                    currentSection = Section.NOTHING

                lines.append(currentLine)
                writtenToFile = True
                continue

            if currentSection == Section.INNER_WALL and is_extrusion_line(currentLine):
                perimeterSegments.append(Segment(getXY(currentLine), lastPosition))

            if currentSection == Section.INFILL:
                # Check for a speed (F) command that sets the infill speed
                if "F" in currentLine and "G1" in currentLine:
                    searchSpeed = re.search(r"F(\d*\.?\d*)", currentLine)
                    if searchSpeed:
                        infill_speed = searchSpeed.group(1)
                        infill_begin = True
                        if "E" in currentLine:
                            lines.append("G1 F{}\n".format(infill_speed))
                            writtenToFile = True
                    else:
                        raise SyntaxError(f'Gcode file parsing error for line {currentLine}')

                if prog_extrusion.search(currentLine):
                    currentPosition = getXY(currentLine)
                    splitLine = currentLine.split(" ")

                    if infill_type == InfillType.LINEAR:
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
                        if infill_begin:
                            infill_flow = (float(infill_speed) * (d_f**2) * pi * extrusionLength) / (4 * segmentLength * 60)
                        else:
                            infill_begin = False
                        if segmentSteps >= 2:
                            for step in range(int(segmentSteps)):
                                segmentEnd = Point2D(
                                    lastPosition.x + segmentDirection.x,
                                    lastPosition.y + segmentDirection.y
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
                                current_flow = infill_flow * flow_factor
                                if current_flow > hotend_max_flow:
                                    new_feedrate = control_flow(hotend_max_flow, extrusionLengthPerSegment * flow_factor, gradientDiscretizationLength, d_f)
                                    lines.append(new_feedrate + get_extrusion_command(segmentEnd.x, segmentEnd.y, segmentExtrusion))
                                elif is_old_speed:
                                    lines.append(get_extrusion_command(segmentEnd.x, segmentEnd.y, segmentExtrusion))
                                else:
                                    is_old_speed = True
                                    lines.append("G1 F{}\n".format(infill_speed) + get_extrusion_command(segmentEnd.x, segmentEnd.y, segmentExtrusion))
                                lastPosition = segmentEnd
                            segmentLengthRatio = get_points_distance(lastPosition, currentPosition) / segmentLength
                            current_flow = infill_flow * max_flow / 100
                            if current_flow > hotend_max_flow:
                                new_feedrate = control_flow(hotend_max_flow, extrusionLength * max_flow / 100, segmentLength, d_f)
                                lines.append(new_feedrate + get_extrusion_command(currentPosition.x, currentPosition.y, segmentLengthRatio * extrusionLength * max_flow / 100))
                            else:
                                edit += 1
                                lines.append(get_extrusion_command(currentPosition.x, currentPosition.y, segmentLengthRatio * extrusionLength * max_flow / 100))
                        else:
                            outPutLine = ""
                            for element in splitLine:
                                if "E" in element:
                                    outPutLine += "E" + str(round(extrusionLength * max_flow / 100, 5))
                                    current_flow = infill_flow * max_flow / 100
                                else:
                                    outPutLine += element + " "
                            if current_flow > hotend_max_flow:
                                new_feedrate = control_flow(hotend_max_flow, extrusionLength * max_flow / 100, segmentSteps, d_f)
                                outPutLine = new_feedrate + outPutLine + "\n"
                            else:
                                outPutLine = outPutLine + "\n"
                            edit += 1
                            edit += 1
                            lines.append(outPutLine)
                        writtenToFile = True

                    if infill_type == InfillType.SMALL_SEGMENTS:
                        shortestDistance = min_distance_from_segment(Segment(lastPosition, currentPosition), perimeterSegments)
                        outPutLine = ""
                        if shortestDistance < gradient_thickness:
                            for element in splitLine:
                                if "E" in element:
                                    flow_factor = mapRange((0, gradient_thickness), (max_flow / 100, min_flow / 100), shortestDistance)
                                    newE = float(element[1:]) * flow_factor
                                    if infill_begin:
                                        segmentLength = get_points_distance(lastPosition, currentPosition)
                                        infill_flow = (float(infill_speed) * (d_f**2) * pi * float(element[1:])) / (4 * segmentLength * 60)
                                        if infill_flow > hotend_max_flow + 0.5:
                                            print('Your infill flow is higher than the hotend limit!')
                                            input()
                                    else:
                                        infill_begin = False
                                    outPutLine += "E" + str(round(newE, 5))
                                else:
                                    outPutLine += element + " "
                            current_flow = infill_flow * flow_factor
                            if current_flow > hotend_max_flow:
                                segmentLength = get_points_distance(lastPosition, currentPosition)
                                new_feedrate = control_flow(hotend_max_flow, newE * flow_factor, segmentLength, d_f)
                                outPutLine = new_feedrate + outPutLine + "\n"
                            elif is_old_speed:
                                outPutLine = outPutLine + "\n"
                            else:
                                is_old_speed = True
                                outPutLine = "G1 F{}\n".format(infill_speed) + outPutLine + "\n"
                            edit += 1
                            edit += 1
                            lines.append(outPutLine)
                        elif thin_inner_core:
                            for element in splitLine:
                                if "E" in element:
                                    newE = float(element[1:]) * min_flow / 100
                                    outPutLine += "E" + str(round(newE, 5)) + "\n"
                                else:
                                    outPutLine += element + " "
                            edit += 1
                            edit += 1
                            lines.append(outPutLine)
                        writtenToFile = True

            if prog_move.search(currentLine) and not ignore_pos:
                lastPosition = getXY(currentLine)
            if not writtenToFile:
                lines.append(currentLine)
    
    with open(output_file_name, "w") as outputFile:
        for line in lines:
            outputFile.write("%s" % line)
    
    if edit == 0:
        print('No changes were made to the file!')

import sys



import tkinter as tk
from tkinter import filedialog, ttk

def launch_gui():
    def run_processor():
        import sys
        try:
            input_file = sys.argv[1]
            params = {
                "HOTEND_MAX_FLOW": float(entry_hotend.get()),
                "D_F": float(entry_diameter.get()),
                "THIN_INNER_CORE": var_thin_inner.get(),
                "INFILL_TYPE": InfillType[var_infill_type.get()],
                "MAX_FLOW": float(entry_max_flow.get()),
                "MIN_FLOW": float(entry_min_flow.get()),
                "GRADIENT_THICKNESS": float(entry_thickness.get()),
                "GRADIENT_DISCRETIZATION": float(entry_discretization.get()),
            }

            print("Running with parameters:", params)

            process_gcode(
                input_file,
                input_file,  # overwrite same file
                params["INFILL_TYPE"],
                params["MAX_FLOW"],
                params["MIN_FLOW"],
                params["GRADIENT_THICKNESS"],
                params["GRADIENT_DISCRETIZATION"],
                params["HOTEND_MAX_FLOW"],
                params["D_F"],
                params["THIN_INNER_CORE"]
            )
            print("G-code processed.")
            root.destroy()
        except Exception as e:
            print("Error:", e)

    root = tk.Tk()
    root.title("Gradient Infill Configuration")

    tk.Label(root, text="HOTEND_MAX_FLOW").grid(row=0, column=0)
    entry_hotend = tk.Entry(root)
    entry_hotend.insert(0, "20.0")
    entry_hotend.grid(row=0, column=1)

    tk.Label(root, text="D_F (Filament Diameter)").grid(row=1, column=0)
    entry_diameter = tk.Entry(root)
    entry_diameter.insert(0, "1.75")
    entry_diameter.grid(row=1, column=1)

    var_thin_inner = tk.BooleanVar(value=True)
    tk.Checkbutton(root, text="THIN_INNER_CORE", variable=var_thin_inner).grid(row=2, columnspan=2)

    tk.Label(root, text="INFILL_TYPE").grid(row=3, column=0)
    var_infill_type = tk.StringVar()
    combo = ttk.Combobox(root, textvariable=var_infill_type)
    combo['values'] = ['SMALL_SEGMENTS', 'LINEAR']
    combo.current(0)
    combo.grid(row=3, column=1)

    tk.Label(root, text="MAX_FLOW (%)").grid(row=4, column=0)
    entry_max_flow = tk.Entry(root)
    entry_max_flow.insert(0, "550.0")
    entry_max_flow.grid(row=4, column=1)

    tk.Label(root, text="MIN_FLOW (%)").grid(row=5, column=0)
    entry_min_flow = tk.Entry(root)
    entry_min_flow.insert(0, "50.0")
    entry_min_flow.grid(row=5, column=1)

    tk.Label(root, text="GRADIENT_THICKNESS (mm)").grid(row=6, column=0)
    entry_thickness = tk.Entry(root)
    entry_thickness.insert(0, "20.0")
    entry_thickness.grid(row=6, column=1)

    tk.Label(root, text="GRADIENT_DISCRETIZATION").grid(row=7, column=0)
    entry_discretization = tk.Entry(root)
    entry_discretization.insert(0, "4.0")
    entry_discretization.grid(row=7, column=1)

    tk.Button(root, text="Run Postprocessor", command=run_processor).grid(row=10, columnspan=2)

    root.mainloop()


def main(argv=None):
    print("Orca Gradient Postprocessor is running...")
    if argv is None:
        argv = sys.argv

    if len(argv) < 2:
        print("Usage: orca_gradient_debug_version.py <input_and_output_file>")
        input("press enter to continue")
        return

    input_file = argv[1]
    output_file = argv[1]  # overwrite same file

    try:
        start = time.time()
        process_gcode(
            input_file,
            output_file,
            INFILL_TYPE,
            MAX_FLOW,
            MIN_FLOW,
            GRADIENT_THICKNESS,
            GRADIENT_DISCRETIZATION,
            HOTEND_MAX_FLOW,
            D_F,
            THIN_INNER_CORE
        )
        print('G-code processed successfully.')
        print('Execution time:', time.time() - start)
    except Exception:
        traceback.print_exc()
        input("press enter to continue")


if __name__ == '__main__':
    launch_gui()
