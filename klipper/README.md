## bd_pressure klipper install


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

1. Disable the Pressure advance in the Material settings in the slicer.

2. Add the flowing gcode lines into the start_gcode in the slicer, then it will do pressure advance calibration with your setting and set the PA value before printing
```
G1 Z30
G1 X10 Y10 ；Modify this to change the calibration position
PA_CALIBRATE NOZZLE_TEMP=[first_layer_temperature] MAX_VOLUMETRIC=[filament_max_volumetric_speed] ACC_WALL=[outer_wall_acceleration]
```





