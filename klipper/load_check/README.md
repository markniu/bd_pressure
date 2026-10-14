# Filament load check

Confirms after a filament change that the new filament really reached the
nozzle, by measuring nozzle pressure while the purge runs. It works in the
sensor's normal probe mode, so unlike PA mode it needs no re-homing and can
run in the middle of a print.

It has two parts:

| Part | File |
|---|---|
| Firmware extension | `firmware_src/release_hex/BDpressureE_loadcheck_20261012.hex` (source in `firmware_src/Core/Src/main.c`) |
| Klipper module | `klipper/bdpressure_check.py`: `BDP_CHECK_START/STOP/QUERY` |

The register protocol is described in [firmware_src/LOAD_CHECK.md](../../firmware_src/LOAD_CHECK.md).

## Compatibility

- **The firmware is for bd_pressure E** (version string `pandapi3dv1`). Check
  yours first: `grep -a cmd_start ~/printer_data/logs/klippy.log*` shows
  `pandapi3dv1` for E and `pandapi3dV1.0` for G. There is no G build.
- The stock Klipper module `bdpressure.py` works unchanged with the new
  firmware: registers 0-53, PA mode, the endstop output and UART are the
  same, and PA calibration gives the same result as the factory firmware
  (see [Tested](#tested)).
- `bdpressure_check.py` on stock firmware reads the extension marker, reports
  "extension not found" and writes nothing.

## 1. Flash the firmware

The STM32C011's UART bootloader is in ROM and cannot be erased by flashing,
so the sensor can always be recovered with the BOOT button, as long as
option bytes and read protection stay untouched. The stm32flash `-r` and
`-w` commands below do not touch either.

### One-time setup on the Pi: a current stm32flash

Debian's `stm32flash` 0.7 package does not know the STM32C011 yet. Build the
current version (installs nothing system-wide):

```bash
cd ~ && git clone https://git.code.sf.net/p/stm32flash/code stm32flash-src
cd ~/stm32flash-src && make
```

The source build also calls itself `stm32flash 0.7`. It is the right one
if the probe below names the chip `STM32C011xx`; the old binary reports
"unknown/unsupported device 0x443".

### Flash

1. Copy the `.hex` to the Pi, for example by uploading it in Mainsail into
   the config folder.
2. Between prints, stop Klipper: `sudo systemctl stop klipper`.
3. At the toolhead: **unplug the sensor's I2C cable** so it is only powered
   over USB, **hold BOOT**, plug the sensor's USB **directly into a Pi
   port**, release BOOT.
4. Find the port: the sensor is the new `ttyUSB` entry in
   `ls -l /dev/serial/by-path/` (Klipper boards are usually `ttyACM`). A
   bdwidth uses the same USB chip, so if you have one, compare the list
   with the sensor unplugged. The number can change on every replug, so
   check it each time.
5. Probe, back up, write and verify:

   ```bash
   ~/stm32flash-src/stm32flash -b 115200 /dev/ttyUSB0      # must show Device ID 0x0443 (STM32C011xx)
   mkdir -p ~/bdp_firmware_backups
   ~/stm32flash-src/stm32flash -b 115200 -r ~/bdp_firmware_backups/bdp_backup_$(date +%Y%m%d_%H%M%S).bin -S 0x08000000:32768 /dev/ttyUSB0
   ~/stm32flash-src/stm32flash -b 115200 -w ~/printer_data/config/BDpressureE_loadcheck_20261012.hex -v /dev/ttyUSB0
   ```

   The backup must be exactly 32768 bytes; do not write if it is not. A
   successful write ends with `Wrote and verified address 0x08005f30 (100.00%) Done.`
6. Unplug the USB, reconnect the I2C cable, `sudo systemctl start klipper`.

A backup of the factory E firmware (`BDpressureE_20251226.hex`) gives
`266b8618f9a8cd4b9aa183a2c088f935e5742747fa41212cde5c36c97d9d8776` for
`head -c 19876 <backup>.bin | sha256sum`.

### Rollback

Write the backup back the same way:

```bash
~/stm32flash-src/stm32flash -b 115200 -w ~/bdp_firmware_backups/bdp_backup_<date>.bin -v -S 0x08000000 /dev/ttyUSB0
```

or flash `firmware_src/release_hex/BDpressureE_20251226.hex` like a new
firmware.

### Troubleshooting flashing

| Symptom | Cause |
|---|---|
| `Failed to init device, timeout.` | not in bootloader mode. With the I2C cable connected the sensor stays powered, so BOOT does nothing: unplug I2C, then replug USB with BOOT held. |
| `Cannot handle device "/dev/ttyUSBx"` | the port is gone or hung. `dmesg` showing `failed to send control message: -110` means the CH340 dropped out, seen through a USB hub; plug directly into the Pi. |
| `unknown/unsupported device 0x443` | old Debian stm32flash, use the source build. |

## 2. Install the Klipper module

`install.sh` links `bdpressure_check.py` into `~/klipper/klippy/extras/`
together with `bdpressure.py`:

```bash
~/bd_pressure/klipper/install.sh
```

Then add to your config, with the name of your `[bdpressure ...]` section:

```
[bdpressure_check]
sensor: bd_pa          # name after "bdpressure" in [bdpressure bd_pa]
```

and restart Klipper. `BDP_CHECK_QUERY` must report values, not
"extension not found".

### Commands

They act immediately, not in the move queue, so run `M400` first.

| Command | Does |
|---|---|
| `BDP_CHECK_START` | freezes the pressure baseline and starts measuring |
| `BDP_CHECK_STOP` | stops and prints the result |
| `BDP_CHECK_QUERY` | prints the current values without stopping |

The result: `peak` (largest pressure change), `above` (total ms above the
probe threshold) and `longest` (longest continuous ms above it). Vibration
gives short runs, a real extrusion one long run, so `longest` is the value
to judge by. Macros can read the same values from
`printer.bdpressure_check` (`available, running, peak, above_ms,
longest_ms, delta`).

### Quick test

Hot nozzle, filament loaded, toolhead over a purge bucket:

```
; standing still: longest stays near 0
M400
G4 P500
BDP_CHECK_START
G4 P10000
BDP_CHECK_STOP

; extruding: longest about as long as the move (6 s here)
M83
M400
G4 P500
BDP_CHECK_START
G1 E30 F300
M400
BDP_CHECK_STOP
```

Repeat the second block with the filament unloaded: `longest` stays near 0.

## 3. Use it in a macro

Wrap your purge in a check and judge the result in a second macro, so the
status is read after the purge has run (Klipper renders a macro completely
before running any of its commands):

```
[gcode_macro PURGE_WITH_CHECK]
gcode:
    M400
    G4 P500                    ; let the toolhead settle
    BDP_CHECK_START
    G1 E50 F300                ; your purge
    M400
    BDP_CHECK_STOP
    _PURGE_JUDGE

[gcode_macro _PURGE_JUDGE]
variable_min_ms: 5000          ; set from your own good purges
gcode:
    {% set p = printer.bdpressure_check %}
    {% if p.available and p.longest_ms < min_ms %}
        RESPOND TYPE=error MSG="Load check failed: longest pressure {p.longest_ms} ms"
        PAUSE
    {% endif %}
```

Pick `min_ms` from real values: run the purge a few times with filament and
once without, and set it well between the two. Toolhead moves during the
check (a park move, a wipe) also register as pressure, so keep the toolhead
still while measuring if you can.

For AFC with a Blobifier and a bdwidth there is a complete example that
checks every purge: [afc_blobifier/README.md](afc_blobifier/README.md).

## Comparing PA calibration

To check that a firmware calibrates PA like another one, use the same
`PA_CALIBRATE` setup for both. With the macro defaults each step took about
15 s on the test printer, the sensor only detected every 4th to 5th pass
and the `R:` readings repeated, on the factory firmware as well. With the
settings below each step takes about 3 s and every step gives a new
reading:

```
G28
M109 S210
G1 Z50 F3000
G1 X30 Y349 Z10 F3000          ; over the purge tray, adjust to your printer
G1 Z0 F3000
PA_CALIBRATE NOZZLE_TEMP=210 MAX_VOLUMETRIC=25 ACC_WALL=5000 TRAVEL_SPEED=300 ACC_TO_DECEL_FACTOR=50
G28                            ; PA_CALIBRATE switches the X/Y drivers
```

## Tested

2026-10-07, bd_pressure E, hardware V1.2, with the first load-check build
(`BDpressureE_loadcheck_20261007`, before the I2C robustness changes; the
current build has not been through these tests yet):

| Test | Factory `BDpressureE_20251226` | `BDpressureE_loadcheck_20261007` |
|---|---|---|
| `PA_CALIBRATE` (setup above) | 0.054 | 0.054, same curve |
| Standing still 10 s | - | longest 0 ms |
| `G1 E30 F300`, filament loaded | - | longest 6034 ms |
| `G1 E30 F300`, lane unloaded | - | longest 0 ms |
| 150 mm purge, filament loaded | - | longest 17-18 s |
| 150 mm purge, no filament | - | longest 31 ms (1589 ms when a pause moved the toolhead) |

## Building the firmware

Keil MDK (the free Community edition is enough), pack
`Keil::STM32C0xx_DFP`, ARM Compiler 6.24. Open
`firmware_src/MDK-ARM/STM32C011F6U6.uvprojx` and build (F7). Expected:
0 errors, 10 warnings (all from the original code), RAM (`RW + ZI`)
6104 of 6144 bytes, flash 24368 bytes. Building rewrites files under
`MDK-ARM/STM32C011F6U6/`; do not commit those.
