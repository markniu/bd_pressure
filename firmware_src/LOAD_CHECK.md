# Load-check extension (I2C)

An optional extension of the I2C register table that lets the host measure
nozzle pressure over a time window while the sensor stays in endstop
(probe) mode. Typical use: confirm that filament really reached the nozzle
after a filament swap, by checking for sustained pressure during the purge.

It does not use PA mode, so the host does not need `SET_BDPRESSURE START`,
which switches the X/Y stepper drivers and needs a re-home afterwards.

## Compatibility

- Registers 0-53 are unchanged. Hosts that do not know the extension never
  touch bytes 54 and up and see exactly the old behaviour.
- PA mode, the endstop output and the UART protocol are unchanged.
- The automatic baseline update in endstop mode only pauses while a check
  is running.
- Hosts must read the marker at 54-55 before writing byte 56. On firmware
  without the extension that byte is outside the register table.

## Registers

All values little-endian. Read with a single read starting at 54, 18 bytes
(12 bytes on version 1).

| Offset | Size | Name | Access | Meaning |
|---|---|---|---|---|
| 54 | 2 | ext_magic | R | `0xBD` + version: 1 = bytes 54-65, 2 = also 66-71 |
| 56 | 1 | chk_ctrl | R/W | write 1 to start a check, 0 to stop it |
| 57 | 1 | chk_state | R | bit0: check running, bit1: above threshold now |
| 58 | 2 | chk_peak | R | largest \|delta\| since start |
| 60 | 2 | chk_above | R | ms above `THRHOLD_Z` since start (saturates at 65535) |
| 62 | 2 | chk_longest | R | longest continuous ms above `THRHOLD_Z` |
| 64 | 2 | chk_delta | R | current delta (signed), also updated outside a check |
| 66 | 1 | diag_reset | R | cause of the last sensor reset (v2), bits below |
| 67 | 1 | diag_i2c_resets | R | I2C1 resets by the I2C watchdog since boot (v2, saturates at 255) |
| 68 | 2 | diag_i2c_errs | R | I2C errors other than NACK since boot (v2) |
| 70 | 2 | diag_uptime | R | seconds since boot (v2, saturates at 65535) |

`diag_reset` bits: 0x01 watchdog, 0x02 reset pin, 0x04 power, 0x08
software, 0x10 option bytes, 0x20 other. A normal power-up shows reset pin
and power. A sensor that answers but resets its I2C often, or restarts by
itself, shows it here without a power cycle.

`delta` is the same value the endstop trigger uses: the average of the last
4 samples minus the baseline.

## Behaviour

On start (`chk_ctrl` 0 -> 1) the firmware:

1. sets the baseline to the median of 16 block averages over the last 256
   samples (about 0.2 s), so a short burst just before the start is ignored,
2. clears peak and timers,
3. stops the automatic baseline update until the check is stopped, so a
   steady extrusion pressure keeps counting instead of being learned as the
   new zero.

Vibration gives short runs above the threshold, a sustained extrusion gives
one long run, so `chk_longest` separates the two. The host should start the
check while the toolhead is still (after `M400` and a short dwell).

## I2C robustness and watchdog

The load-check build also hardens the I2C slave. With Klipper's software
I2C on a toolhead board a transfer can be corrupted, and the stock code
could then stay silent until a power cycle (seen in practice: garbage
readings during `PA_CALIBRATE`, then only empty ones):

- register numbers past the table are ignored (reads return 0) instead of
  writing into the RAM after the register table,
- every write is handled as starting with the register number, also when
  the STOP of the previous transfer was missed (before, such a write was
  never armed and the slave held SCL low),
- `HAL_I2C_ErrorCallback` starts listening again after an I2C error,
- the main loop resets I2C1 when it has not been back in the listen state
  for 500 ms (a transfer that never ended). The bus BUSY flag is not used:
  a glitch can leave it set while the slave works, and resetting then made
  the sensor NACK every ~500 ms (the withdrawn 20261012 build),
- an independent watchdog restarts the sensor when the main loop stops for
  1 s. Registers written by the host (mode, threshold) are lost then; the
  host sees a failed reading and sets them again on its next command.

Register layout and behaviour of correct transfers are unchanged.

## Klipper

`klipper/bdpressure_check.py` adds `BDP_CHECK_START`, `BDP_CHECK_STOP` and
`BDP_CHECK_QUERY` on top of an existing `[bdpressure <name>]` I2C section,
and reports `available, running, peak, above_ms, longest_ms, delta` in its
status. On firmware without the extension it reports `available: False` and
writes nothing.

```
[bdpressure_check]
sensor: bd_pa
```

Flashing, installation, a macro example, test results and build notes:
[klipper/load_check/README.md](../klipper/load_check/README.md).
A prebuilt firmware is in
`release_hex/BDpressureE_loadcheck_20261015.hex`.
