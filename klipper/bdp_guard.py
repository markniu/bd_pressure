# Nozzle collision guard with bd_pressure (load-check firmware extension).
#
# The bd_pressure strain gauge sees the force on the nozzle. The load-check
# firmware keeps the current force minus its baseline in register 64-65
# (chk_delta), also outside a load check; it is the same value the endstop
# output switches on (|delta| >= thrhold). While the guard is armed this
# module reads it every INTERVAL seconds. In stop mode, SAMPLES reads in a
# row at or above THRESHOLD shut Klipper down at once (like M112, without
# waiting for the running macro to finish).
#
# Every ARM (also a phase switch) first sets a new zero (register 52), so
# slow pressure changes before the guarded moves do not count (TPU in a
# nozzle that heats up keeps changing pressure for minutes).
#
# Arm it only around moves where the nozzle must not touch anything: not
# around contact homing, extrusion, purges or AFC cuts and loads, which
# press on the nozzle on purpose. ARM and DISARM act immediately, not in
# the move queue: put an M400 before them (the _GUARD_ON/_GUARD_OFF macros
# do this).
#
# Install: install.sh links it into ~/klipper/klippy/extras/ (see
# load_check/README.md, "Nozzle guard"), then add
#
#   [bdp_guard]
#   sensor: bd_pa        # name after "bdpressure" in [bdpressure bd_pa]
#   mode: log            # log: only measure; stop: shut down on a touch
#   threshold: 30        # |delta| that counts as a touch
#   samples: 3           # reads in a row at or above threshold
#   interval: 0.02       # seconds between reads while armed
#   timeout: 600         # disarm by itself when a phase runs this long
#                        # (a macro that failed before its DISARM)
#
# Commands:
#   BDP_GUARD_ARM PHASE=<name> [MODE=log|stop]   start (or switch phase)
#   BDP_GUARD_DISARM                             stop and report the phase
#   BDP_GUARD_QUERY                              report without stopping
#
# Each phase reports (console and klippy.log, "bdp_guard:") the reads, the
# peak |delta|, the longest run of reads at or above threshold and the
# read errors, so a log-mode print start shows what each phase normally
# measures before stop mode is switched on.
#
# Status (printer.bdp_guard): armed, mode, phase, threshold, samples, peak,
#   longest_run, reads, errors, available

import logging

REG_MODE = 48         # status_clk: 7 = PA mode, 2 = probe (endstop) mode
REG_RESET_PROBE = 52  # set_normal: write 1 = take the current reading as zero
MODE_PA = 7
REG_EXT = 54          # extension marker 0xBD + version
REG_DELTA = 64        # chk_delta, int16 little-endian
EXT_MAGIC = 0xBD
MODES = ('log', 'stop')


# Klipper allows only one I2C request at a time per device: a second one
# while the first waits for its answer raises KeyError ('i2c_response', oid)
# and shuts Klipper down. The guard reads from a timer while G-code runs,
# and bdpressure.py writes the probe mode at every homing move (seen: G28
# during an armed guard), so all transfers of the sensor take this lock.
# The guard puts it on the sensor object at connect; bdpressure and
# bdpressure_check use sensor.i2c and so get it too.
class LockedI2C:
    def __init__(self, i2c, mutex):
        self._i2c = i2c
        self._mutex = mutex

    def i2c_read(self, *args, **kwargs):
        with self._mutex:
            return self._i2c.i2c_read(*args, **kwargs)

    def i2c_write(self, *args, **kwargs):
        with self._mutex:
            return self._i2c.i2c_write(*args, **kwargs)

    def i2c_write_wait_ack(self, *args, **kwargs):
        with self._mutex:
            return self._i2c.i2c_write_wait_ack(*args, **kwargs)

    def i2c_transfer(self, *args, **kwargs):
        with self._mutex:
            return self._i2c.i2c_transfer(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._i2c, name)


class BDPGuard:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.sensor_name = 'bdpressure ' + config.get('sensor', 'bd_pa')
        self.mode = config.getchoice('mode', {m: m for m in MODES}, 'log')
        self.threshold = config.getint('threshold', 30, minval=1)
        self.samples = config.getint('samples', 3, minval=1)
        self.interval = config.getfloat('interval', 0.02, minval=0.005)
        self.timeout = config.getfloat('timeout', 600., minval=10.)
        self.armed_until = 0.
        self.sensor = self.i2c = None
        self.available = False
        self.timer = None
        self.zeroing = False
        self.phase_mode = self.mode
        self._reset_phase('')
        self.printer.register_event_handler('klippy:connect',
                                            self._handle_connect)
        self.printer.register_event_handler('klippy:shutdown',
                                            self._handle_shutdown)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('BDP_GUARD_ARM', self.cmd_ARM,
                               desc="Arm the bd_pressure nozzle collision guard")
        gcode.register_command('BDP_GUARD_DISARM', self.cmd_DISARM,
                               desc="Disarm the bd_pressure nozzle collision guard")
        gcode.register_command('BDP_GUARD_QUERY', self.cmd_QUERY,
                               desc="Report the bd_pressure guard values")

    def _handle_connect(self):
        self.sensor = self.printer.lookup_object(self.sensor_name, None)
        i2c = getattr(self.sensor, 'i2c', None)
        if i2c is not None and not isinstance(i2c, LockedI2C):
            # Every user of the sensor (bdpressure, bdpressure_check, this
            # guard) now goes through one lock, see LockedI2C
            i2c = LockedI2C(i2c, self.reactor.mutex())
            self.sensor.i2c = i2c
        self.i2c = i2c

    def _handle_shutdown(self):
        self._stop_timer()

    def _reset_phase(self, phase):
        self.phase = phase
        self.peak = self.longest_run = self.run = 0
        self.reads = self.errors = 0

    def _check_available(self):
        if self.i2c is None:
            return False
        try:
            data = bytearray(self.i2c.i2c_read([REG_EXT], 2)['response'])
        except Exception:
            logging.exception("bdp_guard: reading the extension marker failed")
            return False
        return len(data) == 2 and data[0] == EXT_MAGIC and data[1] >= 1

    def _ensure_probe_mode(self, gcmd):
        # An emergency stop during PA_CALIBRATE leaves the sensor in PA mode
        # (FIRMWARE_RESTART does not reset it); bdpressure.py only switches
        # it back at the next homing move, after the guard is armed. In PA
        # mode the delta means nothing and could trip the guard. Register 48
        # is the mode: 7 = PA, 2 = probe.
        if getattr(self.sensor, 'last_state', 0):
            return  # a calibration runs, leave the sensor alone
        try:
            mode = bytearray(self.i2c.i2c_read([REG_MODE], 1)['response'])
            if len(mode) != 1 or mode[0] != MODE_PA:
                return
            self.sensor.set_probe_mode()
            # new samples at the probe sample rate, then a fresh baseline
            self.reactor.pause(self.reactor.monotonic() + 0.5)
            self.i2c.i2c_write([REG_RESET_PROBE, 1])
            self.reactor.pause(self.reactor.monotonic() + 0.3)
        except Exception:
            logging.exception("bdp_guard: switching the sensor to probe mode failed")
            return
        msg = ("BDP_GUARD: bd_pressure was still in PA mode (stopped "
               "calibration?), switched to probe mode")
        logging.info(msg)
        gcmd.respond_info(msg)

    def _rezero(self):
        # A fresh zero at every ARM: only force that appears during the
        # guarded moves counts, not slow changes before them. Seen with TPU
        # in a nozzle heating to 150C: the pressure kept rising (+43) or
        # falling as it oozed (-56) for minutes, faster than the sensor's
        # own baseline update (only when steady, about every 7 s), and
        # stopped the printer at the start of a phase with the nozzle in
        # the air. ARM comes after M400, with the nozzle clear of
        # everything, so this does not hide a real touch.
        self.zeroing = True
        try:
            self.i2c.i2c_write([REG_RESET_PROBE, 1])
            self.reactor.pause(self.reactor.monotonic() + 0.2)
        except Exception:
            logging.exception("bdp_guard: setting a new zero failed")
        self.zeroing = False

    def _stop_timer(self):
        if self.timer is not None:
            self.reactor.unregister_timer(self.timer)
            self.timer = None

    def _poll(self, eventtime):
        # Never raise here: an exception in a timer shuts Klipper down.
        try:
            if self.printer.is_shutdown():
                return self.reactor.NEVER
            if eventtime > self.armed_until:
                logging.info("bdp_guard: %s armed longer than %.0f s, "
                             "disarmed (%d reads, peak %d)", self.phase,
                             self.timeout, self.reads, self.peak)
                self._stop_timer()
                self.phase = ''
                return self.reactor.NEVER
            # PA mode (SET_BDPRESSURE START): the delta means nothing then;
            # while ARM sets a new zero the readings are in between
            if self.zeroing or getattr(self.sensor, 'last_state', 0):
                return eventtime + self.interval
            data = bytearray(self.i2c.i2c_read([REG_DELTA], 2)['response'])
            if len(data) != 2:
                self.errors += 1
                return eventtime + self.interval
            delta = data[0] | (data[1] << 8)
            if delta & 0x8000:
                delta -= 0x10000
            mag = abs(delta)
            self.reads += 1
            self.peak = max(self.peak, mag)
            self.run = self.run + 1 if mag >= self.threshold else 0
            self.longest_run = max(self.longest_run, self.run)
            if self.run >= self.samples and self.phase_mode == 'stop':
                # klippy:shutdown unregisters this timer
                self.printer.invoke_shutdown(
                    "bd_pressure guard: nozzle touched something during %s "
                    "(delta %d, threshold %d)"
                    % (self.phase or 'an armed move', delta, self.threshold))
                return self.reactor.NEVER
        except Exception:
            self.errors += 1
            if self.errors == 1:  # 50 reads/s: only the first per phase
                logging.exception("bdp_guard: read failed")
        return eventtime + self.interval

    def _report(self, gcmd, prefix):
        msg = ("%s %s (%s): %d reads, peak %d, longest run >= %d: %d reads, "
               "%d errors" % (prefix, self.phase or '-', self.phase_mode,
                              self.reads, self.peak, self.threshold,
                              self.longest_run, self.errors))
        logging.info("bdp_guard: %s", msg)
        gcmd.respond_info(msg)

    def cmd_ARM(self, gcmd):
        phase = gcmd.get('PHASE', 'armed')
        mode = gcmd.get('MODE', self.mode).lower()
        if mode not in MODES:
            raise gcmd.error("BDP_GUARD_ARM: MODE must be log or stop")
        if self.timer is not None:
            self._report(gcmd, "BDP_GUARD")
        else:
            self.available = self._check_available()
            if not self.available:
                gcmd.respond_info("BDP_GUARD_ARM: bd_pressure load-check "
                                  "extension not found, guard not armed")
                return
            self._ensure_probe_mode(gcmd)
        self._rezero()
        self.phase_mode = mode
        self._reset_phase(phase)
        self.armed_until = self.reactor.monotonic() + self.timeout
        if self.timer is None:
            self.timer = self.reactor.register_timer(
                self._poll, self.reactor.monotonic() + self.interval)

    def cmd_DISARM(self, gcmd):
        if self.timer is None:
            return
        self._stop_timer()
        self._report(gcmd, "BDP_GUARD")
        self.phase = ''

    def cmd_QUERY(self, gcmd):
        self._report(gcmd, "BDP_GUARD_QUERY" + (" armed" if self.timer
                                                 else " not armed"))

    def get_status(self, eventtime):
        return {'armed': self.timer is not None, 'mode': self.phase_mode,
                'phase': self.phase, 'threshold': self.threshold,
                'samples': self.samples, 'peak': self.peak,
                'longest_run': self.longest_run, 'reads': self.reads,
                'errors': self.errors, 'available': self.available}


def load_config(config):
    return BDPGuard(config)
