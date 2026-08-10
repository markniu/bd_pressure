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
G1 Z30                 ; raise the nozzle above the bed
PA_CALIBRATE NOZZLE_TEMP=[nozzle_temperature] MAX_VOLUMETRIC=[filament_max_volumetric_speed] ACC_WALL=[outer_wall_acceleration]  TRAVEL_SPEED=[travel_speed]  ACC_TO_DECEL_FACTOR=[accel_to_decel_factor] NOZZLE=[nozzle_diameter] FLOW=[filament_flow_ratio]
```
`PA_CALIBRATE` only shifts the pattern's starting point to the smallest valid coordinates (x_min/y_min + `X_MARGIN`/`Y_MARGIN`, default 5 mm); the movement distances are unchanged (80 mm line = 20/40/20 mm, Y step 3.5 mm × 50 passes). This removes the old hard-coded X78/Y38.75 offset, so the same pattern fits smaller XY machines too. `NOZZLE=[nozzle_diameter]` and `FLOW=[filament_flow_ratio]` scale the extrusion amounts for your nozzle size and slicer flow ratio (the built-in E values are for a 0.4 mm nozzle at 100 % flow). NOTE: OrcaSlicer's flow-ratio placeholder is `[filament_flow_ratio]` (the config key is `filament_flow_ratio`); `[filament_flow]` only exists in PrusaSlicer and will cause "Variable does not exist" in Orca.

Manual example (run after homing / raising Z):
```
PA_CALIBRATE NOZZLE_TEMP=210 MAX_VOLUMETRIC=20 ACC_WALL=4000 TRAVEL_SPEED=400 ACC_TO_DECEL_FACTOR=50% NOZZLE=0.4 FLOW=1.0
```

If the fixed pattern still exceeds your machine (e.g. a 120 mm bed), reduce it explicitly, e.g. `PASSES=30` or `Y_STEP=2.5`:

#### 3. Prusa Slicer:
```
G28                    ; Home all the axis
G1 Z30                 ; raise the nozzle above the bed
PA_CALIBRATE NOZZLE_TEMP=[temperature] MAX_VOLUMETRIC=[filament_max_volumetric_speed] ACC_WALL=[outer_wall_acceleration]  TRAVEL_SPEED=[travel_speed]  MINIMUM_CRUISE_RATIO=0.5 NOZZLE=[nozzle_diameter] FLOW=[extrusion_multiplier] #ACC_TO_DECEL_FACTOR=[accel_to_decel_factor] this klipper specifc variable was replaced in Klipper with MINIMUM_CRUISE_SPEED. and the 0.5 is the default 50% which can be adjusted to suite the printer's capabilities. 
```
NOTE: PrusaSlicer's flow-multiplier placeholder is `[extrusion_multiplier]` (Filament → Advanced → "Extrusion multiplier", config key `extrusion_multiplier`); there is no `[filament_flow]` in PrusaSlicer.
Optional overrides: `X_MARGIN=5 Y_MARGIN=5 Y_STEP=3.5 PASSES=50` (the macro auto-fits them to the machine's travel).

#### bd_pressure.cfg considerations:
If you have both a bd_pressure and as well bd_width, each has the same by-id UUID because of this, each plugin will try and take control of the first device it can see with the UUID for the USB example 
So you would:
unplug bd_pressure usb cable, then use ls /dev/serial/by-path and take note of the entries there, then plug it back in and use the new entry that appears in place of the "ReplaceWithYourDevicePath" in the example below. 
you will only have to repeat this process if you change what port the bd_pressure is plugged into.
as well you will need to do this in bd_width.cfg as well as both devices will still show the by-id string and can still select the wrong device. 

##   usb example 
# port:usb
# serial:/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0  ## uncomment this if you only have bd_pressure an no bd_width device
# serial:/dev/serial/by-path/ReplaceWithYourDevicePath  # Uncomment this if you have both bd_pressure and bd_width. You need to use ls /dev/serial/by-path , and determine which entry belongs to bd_pressure. 
#if you change the port it's plugged into, you will need to adjust this after doing so to reflect the new usb port you moved it to. 
#you also repeat this with bd_width.cfg to resolve the conflict.
# baud:38400



