# Example: check every AFC purge (AFC + Blobifier + bdwidth)

[purge_check.cfg](purge_check.cfg) wraps the Blobifier purge that AFC runs
after each filament load and checks it two ways:

1. **bdwidth motion:** mm the bdwidth saw moving past it, against mm
   extruded. Catches a grinding extruder, a tangle or a snapped filament.
2. **bd_pressure:** the longest continuous nozzle pressure during the
   purge. Catches filament that never reached the melt zone.

Either check is skipped when its sensor is missing, so it also works with
only a bdwidth (stock bd_pressure firmware) or only a bd_pressure with the
load-check firmware. Setting up the load check itself (firmware, module,
commands) is described in [../README.md](../README.md).

| File | Does |
|---|---|
| `purge_check.cfg` | `BLOBIFIER_CHECKED` macro and its settings |
| `purge_until_empty.cfg` | `PURGE_UNTIL_EMPTY`: purge a snapped filament out until there is no more nozzle pressure |
| `bdwidth_status.py` | Klipper module that exposes bdwidth's motion counter and width to macros (bdwidth's own status only has `filament_detected` and `enabled`) |

## Install

1. Run `~/bd_pressure/klipper/install.sh`. It links `bdwidth_status.py` and
   `bdpressure_check.py` into `~/klipper/klippy/extras/`.
2. Copy `purge_check.cfg` to your config folder and add
   `[include purge_check.cfg]` to `printer.cfg`.
3. Check the two sensor names at the top of `purge_check.cfg`:
   `[bdwidth_status] sensor:` (name after `bdwidth` in `[bdwidth fila_width_0]`)
   and `[bdpressure_check] sensor:` (name after `bdpressure` in
   `[bdpressure bd_pa]`). Remove `[bdpressure_check]` if you have no
   load-check firmware.
4. In `[AFC]` (`AFC/AFC.cfg`) change `poop_cmd: BLOBIFIER` to
   `poop_cmd: BLOBIFIER_CHECKED` and restart Klipper.

Each purge then prints one line:

```
PURGE_CHECK lane1: motion 99.8/149.2 mm ratio 0.67, width 1.77; pressure longest 17883 ms, total 65535 ms, peak 148 -> OK
PURGE_CHECK ?: motion 0.0/149.2 mm ratio 0.00, width 0.00; pressure longest 31 ms, total 53 ms, peak 12 -> FAIL: filament not moving past bdwidth, no sustained nozzle pressure
```

## bdwidth outside a print

bdwidth only reads while it is enabled with `SET_BDWIDTH NAME=...
COMMAND=ENABLE_ALL` (or `ENABLE_MOTION` / `ENABLE_WIDTH`). After
`COMMAND=DISABLE`, and after every Klipper restart until the next
`ENABLE`, it reads nothing, so print start macros usually enable it and
print end macros disable it.

`bdwidth_status.py` reports whether bdwidth is actually reading
(`reading`, from bdwidth's read timer). When it is not, outside a print
or early in a print start before the start macro enables it,
`BLOBIFIER_CHECKED`:

1. turns bdwidth's runout response off (`SET_FILAMENT_SENSOR SENSOR=...
   ENABLE=0`), so an empty lane is reported by the check instead of
   bdwidth pausing the printer,
2. enables motion only (`COMMAND=ENABLE_MOTION`), so the width-based flow
   adjustment stays off during the purge,
3. waits `bdwidth_warmup_ms`: bdwidth's first read often fails and it
   retries after 10 s,
4. runs the purge with both checks and restores the runout response to what
   it was before. Outside a print it also switches bdwidth off again
   (`COMMAND=DISABLE`); during a print it leaves it on, so a print never
   loses bdwidth because of the check.

When bdwidth is already reading, the check uses it as it is.

Without step 1, a check with no filament loaded made bdwidth pause the
printer (`filament width is out of range: 0.000mm`), and the pause moved
the toolhead, which registered as nozzle pressure (1589 ms instead of the
usual ~30 ms). bdwidth reads the width in every mode, so the `width` in
the result line is always a current reading.

## Settings and rollout

The variables of `_PURGE_CHECK_CFG`:

| Variable | Default | Meaning |
|---|---|---|
| `mode` | `"report"` | `report` only prints, `enforce` pauses on a failure, `off` runs the plain purge |
| `min_ratio` | 0.4 | fail below this sensed/extruded ratio |
| `pressure_min_ms` | 5000 | fail below this longest continuous pressure |
| `min_extrude` | 20 | skip both checks for shorter purges (mm) |
| `bdwidth_warmup_ms` | 15000 | wait after switching bdwidth on outside a print |

1. Run in `report` mode for a few days and note the values of good purges.
2. Set `min_ratio` and `pressure_min_ms` well below the lowest good values.
3. Switch to `enforce`.

Reference values from the test printer (150 mm Blobifier purge):

| | ratio | longest pressure |
|---|---|---|
| good load, lane1 | 0.63-0.67 | 17-18 s |
| good load, lane4 (full `CHANGE_TOOL`) | 1.26 | 18 s |
| no filament | 0.00 | 31 ms, 1589 ms with a runout pause during the purge |
| real failed load in a print start (AFC reported lane1 loaded) | 0.00, width 0.000 | 1915 ms, peak 45: caught, print paused in `enforce` |

The ratio depends on the filament: the bdwidth's measuring wheel grips or
slips differently on different spools, so one `motion_linear_coefficient`
does not fit them all. That does not matter for the check, only the gap
between good and empty does; collect values per lane before raising
`min_ratio`.

The `width` in the result line is the last single reading. The bdwidth
sometimes reports about twice the real width for one sample (3.4-3.6 mm
instead of 1.75); the check does not use the width.

## Purging a broken filament out: `PURGE_UNTIL_EMPTY`

[purge_until_empty.cfg](purge_until_empty.cfg) is for a filament that
snapped inside the PTFE tube. The piece below the break is still in the
extruder; the macro repeats the Blobifier purge until bd_pressure measures
no more sustained pressure, so as much of it as possible comes out without
opening the printer.

```
PURGE_UNTIL_EMPTY TEMP=240
```

- Run it with the printer homed, idle or paused (not while printing).
  It heats to `TEMP` (default: the current target) and needs
  `[bdpressure_check]` with the load-check firmware.
- It first unsyncs the loaded AFC lane (`UNSET_LANE_LOADED`). Otherwise AFC
  keeps feeding that lane with the extruder, the spool side of the break
  catches up and the purge never ends. `UNSYNC=0` keeps the lane synced.
- bdwidth sits at the start of the tube: after the break it sees no
  filament while the extruder keeps pushing, and its runout response would
  pause the printer mid-purge. The macro switches that response off
  (`SET_FILAMENT_SENSOR ... ENABLE=0`) for the purges and back on after.
  It does not use bdwidth to decide when to stop, only the nozzle pressure.
- One line per purge, then the result: empty after N purges, or still
  pressure after `MAX` purges (default 15: the lane is still feeding or the
  nozzle is blocked).
- A purge counts as empty when its longest pressure is below `empty_ms`
  (5000 ms; a full 150 mm purge gives about 17 s, an empty nozzle ~30 ms).
  A purge where the filament runs out halfway still shows pressure, so the
  macro always ends with one fully empty purge.
- `LENGTH=` sets the mm per purge (default: the Blobifier's purge length).

What stays behind: the extruder can only push filament its gears still
grip. The bit between the gears and the nozzle stays in the hotend; the
next normal load pushes it out. Afterwards AFC has no lane loaded: eject
the broken lane, then load as usual (for a paused print: load the lane,
then `RESUME`).

## Troubleshooting

| Message | Cause |
|---|---|
| `FAIL: no data from bdwidth since Klipper start` | bdwidth was enabled but delivered nothing: is it connected, and is `bdwidth_warmup_ms` long enough? (An earlier version of the check also gave this when a tool change in the print start ran before the start macro enabled bdwidth.) |
| `motion skipped (bdwidth disabled)` during a print | bdwidth was switched off with `SET_BDWIDTH ... COMMAND=DISABLE`; the check leaves it alone during a print |
| `pressure skipped (no check firmware)` | stock bd_pressure firmware, or `[bdpressure_check]` missing |
| `PURGE_CHECK ?:` | no lane loaded according to AFC |
