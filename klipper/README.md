## klipper 


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
G1 Z30               ; move to the poop position
G1 X240 Y240   
M109 S[first_layer_temperature]      ; wait for extruder temp
; Pressure advance calibration
PA_CALIBRATE NOZZLE_TEMP=[first_layer_temperature] MAX_VOLUMETRIC=[filament_max_volumetric_speed] ACC_WALL=[outer_wall_acceleration]
```





