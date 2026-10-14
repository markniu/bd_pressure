# Expose bdwidth's motion counter and width reading to gcode macros.
#
# bdwidth.py replaces its own get_status() with the runout helper's, so
# macros only see filament_detected/enabled. This read-only wrapper looks
# up the existing [bdwidth <name>] object and reports the values it
# already keeps. It never talks to the sensor, so bdwidth.py stays
# untouched and updates to bdwidth do not overwrite this file.
#
# Install: copy to ~/klipper/klippy/extras/bdwidth_status.py
#
# Config:
#   [bdwidth_status]
#   sensor: fila_width_0   # name after "bdwidth" in [bdwidth fila_width_0]
#
# Status (printer.bdwidth_status):
#   total_move     - motion counter since Klipper start (raw counts, signed)
#   last_motion    - counts seen in the last sample
#   diameter       - last width reading in mm (0.0 on a bad reading)
#   name           - the sensor name, for SET_BDWIDTH NAME=...
#   reading        - True while bdwidth is polling the sensor (after
#                    SET_BDWIDTH ... ENABLE..., until DISABLE)
#   enabled        - runout response on (SET_FILAMENT_SENSOR ENABLE=1/0);
#                    says nothing about whether bdwidth is reading
#   active         - "motion", "width", "all" or "disable" (SET_BDWIDTH). After
#                    a Klipper restart it shows the config value, but bdwidth
#                    only starts reading on the first SET_BDWIDTH ... ENABLE
#   linear_motion  - counts per mm (motion_linear_coefficient)
#   sample_time    - seconds between sensor reads


class BDWidthStatus:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get('sensor', 'fila_width_0')
        self.sensor_name = 'bdwidth ' + self.name
        self.sensor = None
        self.printer.register_event_handler('klippy:connect',
                                            self._handle_connect)

    def _handle_connect(self):
        self.sensor = self.printer.lookup_object(self.sensor_name)

    def get_status(self, eventtime):
        s = self.sensor
        # bdwidth's own get_status() is the runout helper's, which carries
        # the SET_FILAMENT_SENSOR enable flag
        try:
            enabled = bool(s.get_status(eventtime).get('enabled', True))
        except Exception:
            enabled = True
        # bdwidth reads from a reactor timer that only SET_BDWIDTH ... ENABLE
        # starts and DISABLE stops; is_active alone does not show it
        timer = getattr(s, 'extrude_factor_update_timer', None)
        never = self.printer.get_reactor().NEVER
        reading = (timer is not None and getattr(timer, 'waketime', never) != never
                   and 'disable' not in str(getattr(s, 'is_active', 'disable')))
        return {
            'name': self.name,
            'reading': reading,
            'enabled': enabled,
            'total_move': getattr(s, 'actual_total_move', 0),
            'last_motion': getattr(s, 'lastMotionReading', 0),
            'diameter': round(getattr(s, 'lastFilamentWidthReading', 0.), 3),
            'active': str(getattr(s, 'is_active', 'disable')),
            'linear_motion': getattr(s, 'linear_motion', 42.8),
            'sample_time': getattr(s, 'sample_time', 1.),
        }


def load_config(config):
    return BDWidthStatus(config)
