#  Auto PA Calibration
<img src="https://cdn.hackaday.io/images/9126891778570065542.jpg" width='600'>

## Overview 

Pressure Advance (PA) is one of the most impactful tuning parameters in FDM printing, yet calibrating it correctly is time-consuming, requires print-specific expertise, and must be repeated whenever filament, speed, or temperature changes.

**bd_pressure** solves this with a dedicated strain-gauge sensor module that measures extruder pressure directly — without printing a single line. It runs an automated calibration routine by simulating extrusion pressure behaviour during acceleration and deceleration, then reports the optimal PA value to the printer firmware over USB or I2C.

The same hardware doubles as a high-precision nozzle probe for bed levelling, acting as a drop-in switch-type endstop on the Z− pin.

 ![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
 
---

<a href="https://www.youtube.com/watch?v=r0qmRdhEHUw">
  <img src="https://cdn.hackaday.io/images/2558341778555225387.jpg" alt=" " width="600"/>
</a>

<img src="https://cdn.hackaday.io/images/3838211778555198341.jpg" width='600'>
<img src="https://static.wixstatic.com/media/0d0edf_eee5984961de44a7bad4c91010afd43b~mv2.jpg/v1/crop/x_10,y_43,w_1744,h_886/fill/w_846,h_430,al_c,q_85,usm_0.66_1.00_0.01,enc_avif,quality_auto/vsV.jpg" width =600>

---




## Features

| Feature | Description |
|---|---|
| **Automatic PA calibration** | No calibration prints. Extruder-only routine measures pressure response directly. |
| **Nozzle probe** | Strain-gauge contact detection for Z homing, Z tilt, bed mesh or work with eddy bed scan sensor. |
| **EMI immunity** | Sensor mounted zero-distance to PCB — minimal analog trace length eliminates noise pickup. |
| **USB & I2C** | Flexible host connectivity; works with Klipper and compatible setups. |
| **Broad compatibility** | Mounting footprint compatible with E3D and Voron toolhead ecosystems. |

---

## How It Works

### Mode 1 — Pressure Advance Calibration

Rather than printing calibration patterns and analysing them visually, bd_pressure directly measures the mechanical relationship between extruder motor acceleration and filament pressure at the nozzle.

1. The extruder motor runs a controlled acceleration/deceleration sequence.
2. The strain gauge measures the resulting force in real time.
3. The on-board MCU fits a pressure-vs-acceleration curve and derives the optimal PA value.
4. The result is sent to the printer firmware via USB or I2C.

This approach is conceptually similar to the automated calibration used in the Bambu Lab A1, but uses a **strain gauge** rather than an eddy current sensor — providing a direct force measurement.

### Mode 2 — Nozzle Probe

bd_pressure operates as a standard switch-type endstop sensor:

1. The printer issues a normal Z homing move.
2. As the nozzle contacts the bed, the strain gauge detects the resulting force change.
3. The module asserts a signal on the endstop output — compatible with any mainboard Z− pin.
4. No additional firmware plugin is required for basic probe operation.

---

 
## Installation & Documentation
 
Full installation guide, wiring diagrams, and Klipper configuration reference:

 Klipper:
**[pandapi3d.cn/en/bdpressure/home](https://pandapi3d.cn/en/bdpressure/home)**

  RepRapFirmware:
**[github.com/jaysuk](https://github.com/jaysuk/bd_pressure_dwc_plugin)**

<img src="https://cdn.hackaday.io/images/7559601778573237186.jpg" width=600>

<img src="https://cdn.hackaday.io/images/6554431774086421961.jpg" width=600>

<img src="https://cdn.hackaday.io/images/6383141778555677061.jpg" width='600'>
 
 The strain gauge sensor: BF350-1EB  350Ω 5.6*4.6mm 
 
---
 
## Community & Support
 
| Channel | Link |
|---|---|
| Discord | [discord.gg/z6ahddnGVU](https://discord.gg/z6ahddnGVU) |
| Facebook group | [facebook.com/groups/380795976169477](https://facebook.com/groups/380795976169477) |
| Thingiverse | [thingiverse](https://thingiverse.com) |
| Where to buy | [fabreeko.com](https://fabreeko.com) <br> [west3d.com](https://west3d.com)  <br> [pandapi3d.com](https://pandapi3d.com) , [淘宝](https://shop62248922.taobao.com/)
|
---
 

