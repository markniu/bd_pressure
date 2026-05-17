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
G1 Z30                 ; move to the poop position
G1 X240 Y240   
PA_CALIBRATE NOZZLE_TEMP=[first_layer_temperature] MAX_VOLUMETRIC=[filament_max_volumetric_speed] ACC_WALL=[outer_wall_acceleration]  TRAVEL_SPEED=[travel_speed]  ACC_TO_DECEL_FACTOR=[accel_to_decel_factor]
```
#### 3. Prusa Slicer:
```
G28                    ; Home all the axis
G1 Z30                 ; move to the poop position
G1 X240 Y240   
PA_CALIBRATE NOZZLE_TEMP=[nozzle_temperature] MAX_VOLUMETRIC=[filament_max_volumetric_speed] ACC_WALL=[outer_wall_acceleration]  TRAVEL_SPEED=[travel_speed]  MINIMUM_CRUISE_RATIO=0.5 #ACC_TO_DECEL_FACTOR=[accel_to_decel_factor] this klipper specifc variable was replaced in Klipper with MINIMUM_CRUISE_SPEED. and the 0.5 is the default 50% which can be adjusted to suite the printer's capabilities. 
```

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



