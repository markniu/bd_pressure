
### BD Pressure Monitor – Quick Guide

1. Setup: Select COM port & baud (default 38400) → Connect. Auto-sends d; to start streaming.
2. Plot: Live waveform. Use Window slider for time range, toggle Auto Y scale. Pause to freeze, Clear to reset.
3. Serial Response Box: Shows all raw incoming and outgoing data (including command echo and sensor values).
4. Supported Quick Commands (BD Firmware Specific)

| Button     | Command | Function                                                      |
|------------|---------|---------------------------------------------------------------|
| Endstop    | e;      | Switch to Endstop / Probe mode                                |
| Raw on     | d;      | Enable continuous ADC data output (sent automatically on connect) |
| Raw off    | D;      | Disable data output                                           |
| PA mode    | l;      | Switch to Pressure Advance mode                               |
| Normal     | i;      | Set normal polarity                                           |
| Inverted   | I;      | Set inverted polarity                                         |
| Set normal | N;      | Use the current reading as the baseline (zero point)         |

