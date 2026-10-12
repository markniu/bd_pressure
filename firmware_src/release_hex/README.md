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

`BDpressureE_loadcheck_20261012.hex` is the bd_pressure E firmware built from
the current source with the optional filament load-check extension, plus I2C
error recovery and a watchdog (see `../LOAD_CHECK.md`). It
behaves like the stock firmware for Klipper's `bdpressure.py` and adds the
registers described in `../LOAD_CHECK.md`. Flashing from a Raspberry Pi and
usage: `../../klipper/load_check/README.md`.
