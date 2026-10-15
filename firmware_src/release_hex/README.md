# Firmware Download into the sensor

1. Press and Hold on the boot button on the sensor
2. Power on the sensor with usb cable
3. Release the boot button
4. Open the STM32CubeProgrammer.exe
5. Choose the UART port and click connect button in the STM32CubeProgrammer
6. Open the firmware file for example: BD_PA20250925.hex --> Click Download
7. Finish

This process is the same as bdwidth sensor, here is the video of bdwidth: https://youtu.be/c74Q1chOo8M

## Load-check firmware

`BDpressureE_loadcheck_20261015.hex` is the bd_pressure E firmware built from
the current source with the optional filament load-check extension, plus I2C
error recovery, a watchdog and diagnostic registers (see `../LOAD_CHECK.md`).
It replaces the 20261012 build, whose I2C recovery reset the I2C every 500 ms
and made Klipper shut down with I2C NACK errors, mostly during homing. It
behaves like the stock firmware for Klipper's `bdpressure.py` and adds the
registers described in `../LOAD_CHECK.md`. Flashing from a Raspberry Pi and
usage: `../../klipper/load_check/README.md`.

`BDpressureE_loadcheck_20261015b.hex` is the current source: the same, plus
two-byte values (delta, the check times, diagnostics) are no longer updated
while the host reads them, so a read can no longer mix bytes of two updates
(e.g. delta 255 instead of 0). Not tested on hardware yet; 20261015 is the
tested build.
