import re 
import sys
import traceback

MAX_FLOW = 220
MIN_FLOW = 60
NUMBER_OF_LAYERS = 185
BOTTTOM_LAYERS = 4
TOP_LAYERS = 4
EQUALIZE_FLOW = True
RUN_IN_SLICER = False
DENSE_TO_LIGHT = True # True: high line width at the bottom at the top low

input_file_name = r"C:\Users\bjans\Downloads\Shape-Box_44m_0.20mm_240C_PETG_ENDER5PRO.gcode"
output_file_name = r"C:\Users\bjans\Downloads\Z_gradient.gcode"

prog_type = re.compile(r'^;TYPE:')

NUMBER_OF_LAYERS -= (TOP_LAYERS + BOTTTOM_LAYERS)
if RUN_IN_SLICER:
    input_file_name = sys.argv[1]
    output_file_name = input_file_name

with open(input_file_name, "r") as gcodeFile:
    lines = gcodeFile.readlines()

layer_count = -BOTTTOM_LAYERS
lines_out = []
try:
    for line in lines:
        if line.startswith(";LAYER_CHANGE"):
            layer_count += 1
            if DENSE_TO_LIGHT:
                E_factor = round((1- layer_count/NUMBER_OF_LAYERS) * MAX_FLOW + layer_count/NUMBER_OF_LAYERS * MIN_FLOW,2) 
            else:
                E_factor = round((layer_count/NUMBER_OF_LAYERS) * MAX_FLOW + (1 - layer_count/NUMBER_OF_LAYERS) * MIN_FLOW,2)

        if prog_type.match(line):
            if line.startswith(";TYPE:Internal infill"):
                line += "M221 S{}\n".format(E_factor) 
                if EQUALIZE_FLOW and E_factor > 100:
                    line += "M220 S{}\n".format(round(10000/E_factor,2))

            else: #different type of infill
                # reset flow
                line += "M221 S100 \n"
                if EQUALIZE_FLOW:
                    line += "M220 S100\n"

        lines_out.append(line)

    with open(output_file_name, "w") as outputFile:
        for line in lines_out:
                outputFile.write("%s"  % line)

except Exception:
    traceback.print_exc()
    