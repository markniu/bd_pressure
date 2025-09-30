# BD_Pressure

### Features:

#### 1. Automated Pressure Advanced Calibration

#### 2. Nozzle Probe


### How it works?

1. PA Mode:

    Automated Pressure Advanced Calibration.

    Without printing calibration lines, it just simulate extrusion pressure behavior during acceleration and deceleration while only the extruder is working.

    This work process is similar to the Bambu Lab A1 printer, instead, we use  strain gauge, not eddy sensor.

2. Nozzle Probe Mode: 

     Use the strain gauge to sense the nozzle pressure while probing .

     It works as a normal switch endstop sensor, so we can just power it and connect the Z- pin on the mainboard. 


### klipper 

#### 1. Install software module
```
cd  ~
git clone https://github.com/markniu/bd_pressure.git
chmod 777 ~/bd_pressure/klipper/install.sh
~/bd_pressure/klipper/install.sh
```

#### 2. Configure Klipper

Add [include bd_pressure.cfg] into the printer.cfg , and modify the pins to your actual use in the section [probe] and [bdpressure bd_pa]

#### 3. OrcaSlicer:

1. Disable the Pressure advance in the Material settings.

2. Add the following G-code lines into the beginning of the Start_Gcode in the slicer, then it will do pressure advance calibration with your setting and automatically set the right PA value. 
```
G28                    ; Home all the axis
G1 Z30                 ; move to the poop position
G1 X240 Y240   
M109 S[first_layer_temperature]      ; wait for extruder temp
; Pressure advance calibration
PA_CALIBRATE NOZZLE_TEMP=[first_layer_temperature] MAX_VOLUMETRIC=[filament_max_volumetric_speed] ACC_WALL=[outer_wall_acceleration]
```


### others
Store: https://www.pandapi3d.com/product-page/bdpressure

video: [test video](https://youtu.be/zLuWcR-ahno)

Discord:  [3D Printscape](https://discord.com/channels/804253067784355863/1403863863367176312)
