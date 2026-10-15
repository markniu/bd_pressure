# Load check using the bd_pressure firmware extension (bdp_check_ext.patch).
#
# Uses the I2C connection the existing [bdpressure <name>] section already
# set up. Before writing anything it reads the extension marker (bytes
# 54-55 = 0xBD, version 1 or 2). On stock firmware the marker is missing and every
# command only reports "not available": nothing is written, because
# writing past the stock register table would land in the sensor's RAM.
#
# Install: copy to ~/klipper/klippy/extras/bdpressure_check.py
#
# Config:
#   [bdpressure_check]
#   sensor: bd_pa          # name after "bdpressure" in [bdpressure bd_pa]
#
# Commands (run them after M400, they act immediately, not in the move queue):
#   BDP_CHECK_START   freeze the baseline and start measuring
#   BDP_CHECK_STOP    stop measuring and read the result
#   BDP_CHECK_QUERY   read the current values without stopping
#
# Status (printer.bdpressure_check), updated by the commands above:
#   available, running, peak, above_ms, longest_ms, delta
# Extension version 2 also reports diagnostics (BDP_CHECK_QUERY prints them):
#   reset_cause, i2c_resets, i2c_errors, uptime_s

REG_EXT = 54          # first byte of the extension
REG_CTRL = 56
EXT_LEN = 18          # bytes 54-71 (version 1 only has 54-65)
EXT_MAGIC = 0xBD
EXT_VERSIONS = (1, 2)
RESET_BITS = ((0x01, 'watchdog'), (0x02, 'reset pin'), (0x04, 'power'),
              (0x08, 'software'), (0x10, 'option bytes'), (0x20, 'other'))


class BDPressureCheck:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.sensor_name = 'bdpressure ' + config.get('sensor', 'bd_pa')
        self.i2c = None
        self.status = {'available': False, 'running': False, 'peak': 0,
                       'above_ms': 0, 'longest_ms': 0, 'delta': 0}
        self.printer.register_event_handler('klippy:connect',
                                            self._handle_connect)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('BDP_CHECK_START', self.cmd_START,
                               desc="Start a bd_pressure load check")
        gcode.register_command('BDP_CHECK_STOP', self.cmd_STOP,
                               desc="Stop the bd_pressure load check and read it")
        gcode.register_command('BDP_CHECK_QUERY', self.cmd_QUERY,
                               desc="Read the bd_pressure load check values")

    def _handle_connect(self):
        sensor = self.printer.lookup_object(self.sensor_name)
        self.i2c = getattr(sensor, 'i2c', None)

    def _read(self):
        if self.i2c is None:
            self.status['available'] = False
            return False
        data = bytearray(self.i2c.i2c_read([REG_EXT], EXT_LEN)['response'])
        if (len(data) < EXT_LEN or data[0] != EXT_MAGIC
                or data[1] not in EXT_VERSIONS):
            self.status['available'] = False
            return False
        u16 = lambda i: data[i] | (data[i + 1] << 8)
        delta = u16(10)
        self.status.update({
            'available': True,
            'running': bool(data[3] & 1),
            'peak': u16(4),
            'above_ms': u16(6),
            'longest_ms': u16(8),
            'delta': delta - 0x10000 if delta & 0x8000 else delta,
        })
        if data[1] >= 2:
            self.status.update({
                'reset_cause': data[12],
                'i2c_resets': data[13],
                'i2c_errors': u16(14),
                'uptime_s': u16(16),
            })
        return True

    def _respond(self, gcmd, prefix):
        s = self.status
        if not s['available']:
            gcmd.respond_info("%s: bd_pressure check extension not found "
                              "(stock firmware?), nothing done" % prefix)
            return
        gcmd.respond_info("%s: running=%d peak=%d above=%d ms longest=%d ms "
                          "delta=%d" % (prefix, s['running'], s['peak'],
                                        s['above_ms'], s['longest_ms'],
                                        s['delta']))

    def cmd_START(self, gcmd):
        # The values read here are still the previous check's, so only
        # confirm the start instead of printing them.
        if self._read():
            self.i2c.i2c_write([REG_CTRL, 1])
            self.status.update({'running': True, 'peak': 0, 'above_ms': 0,
                                'longest_ms': 0})
            gcmd.respond_info("BDP_CHECK_START: started")
            return
        self._respond(gcmd, "BDP_CHECK_START")

    def cmd_STOP(self, gcmd):
        if self._read():
            self.i2c.i2c_write([REG_CTRL, 0])
            self._read()
        self._respond(gcmd, "BDP_CHECK_STOP")

    def cmd_QUERY(self, gcmd):
        self._read()
        self._respond(gcmd, "BDP_CHECK_QUERY")
        s = self.status
        if s['available'] and 'uptime_s' in s:
            cause = ', '.join(n for b, n in RESET_BITS
                              if s['reset_cause'] & b) or 'none'
            gcmd.respond_info("BDP_CHECK_QUERY: sensor up %d s, last reset: "
                              "%s, I2C resets: %d, I2C errors: %d"
                              % (s['uptime_s'], cause, s['i2c_resets'],
                                 s['i2c_errors']))

    def get_status(self, eventtime):
        return dict(self.status)


def load_config(config):
    return BDPressureCheck(config)
