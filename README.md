# GradientInfill
![alt text](https://static1.squarespace.com/static/5d88f1f13db677155dee50fa/t/5e184edf208b5e01f31462a2/1578651390859/vlcsnap-2020-01-10-11h15m40s688.png?format=2500w)

This a Python script that post-processes existing G-Code to add gradient infill for 3D prints.

Watch my YouTube video about it: https://youtu.be/hq53gsYREHU

# Important Notes

In its current for it only works with G-Code files generated with CURA due to the comments CURA puts into the G-Code files.

It is also important to make sure that the "Walls" are printed before the "Infill" ("Infill before Walls" OFF).
For this script to work, also activate "Relative Extrusion" under "Special Modes".

Further instructions can be found on my website: http://cnckitchen.com/blog/gradient-infill-for-3d-prints

# GradientInfill.py by 5axes

GradientInfill.py Posprocessing Script for Cura PlugIn. 

Save the file in the _C:\Program Files\Ultimaker Cura **X.X**\plugins\PostProcessingPlugin\scripts_ directory

![plugin](https://user-images.githubusercontent.com/11015345/72824291-513cca00-3c75-11ea-943a-4f8f7cb59d06.jpg)

Extrusion mode in Cura must be set in relative mode. If it's not the case an error message will be raised in Cura.

![Message](https://user-images.githubusercontent.com/11015345/72720216-c1662580-3b79-11ea-9583-60de8240eef2.jpg)

No Gcode will be generated by Cura in this case. Same behaviour if Cura settings are not suitable for Gradient Infill modification :

- Infill pattern type ZigZag and Concentric not allowed  
- The option "Connect Infill Lines" for the other patterns musn't be used.

A new Flow Value for short distance (Linear move < 2 x Gradient distance) added to the standard GradientInfill script.

Add a gradual speed variation for machine without direct drive extruder.

![82574446_1223039984569029_7656888964539744256_o](https://user-images.githubusercontent.com/11015345/72863160-ec628d80-3ccf-11ea-9891-8583b62866f7.jpg)

Sample part with a Gradient distance set to 8 mm :
![82570108_1223017127904648_3642722292435255296_o](https://user-images.githubusercontent.com/11015345/72863337-8e827580-3cd0-11ea-9681-e1de7e2071c2.jpg)

# GradientInfill by WatchingWatches
Adapted version of the script addGradientInfill.py, which works with Prusa and Orca slicer. Both scripts have a own folder with detailed instructions inside of the README file.