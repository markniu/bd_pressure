import logging
import math
import statistics
import serial
import serial.tools.list_ports
import os
import time


from . import bus
from . import filament_switch_sensor


# --- USB Auto-detection helpers ---

# Version strings:
#   bdpressure -> pandapi3dv1  (lowercase v)  identified by 'V;' command
#   bdwidth    -> pandapi3dV1  (capital V)
# We match on 'pandapi3dv' (lowercase v, case-sensitive) to identify bdpressure.
BDPRESSURE_BAUD = 38400
BDPRESSURE_VERSION_MARKER = 'endstop mode'   # lowercase v = bdpressure
BDPRESSURE_PROBE_CMDS = [ b'e;\n']

CH340_VID = 0x1A86
CH340_KEYWORDS = ('ch340', 'ch341', '1a86', 'qinheng')


def _list_all_serial_ports():
    """Return all available serial port device paths."""
    return [p.device for p in serial.tools.list_ports.comports()]


def _list_ch340_ports():
    found = set()

    # --- Methods 1 & 2: pyserial comports() ---
    for p in serial.tools.list_ports.comports():
        vid = getattr(p, 'vid', None)
        desc = (p.description or '').lower()
        hwid = (p.hwid or '').lower()
        if vid == CH340_VID:
            found.add(p.device)
            continue
        if any(k in desc or k in hwid for k in CH340_KEYWORDS):
            found.add(p.device)

    # --- Method 3: /dev/serial/by-id/ symlink scan ---
    by_id_dir = '/dev/serial/by-id'
    if os.path.isdir(by_id_dir):
        for name in os.listdir(by_id_dir):
            if any(k in name.lower() for k in CH340_KEYWORDS):
                symlink = os.path.join(by_id_dir, name)
                try:
                    real = os.path.realpath(symlink)
                    found.add(real)
                    logging.info(
                        "bdpressure auto-detect: found CH340 via by-id: %s -> %s" % (name, real)
                    )
                except Exception:
                    pass

    return list(found)


def _probe_port_for_bdpressure(port):
    ser = None
    try:
        ser = serial.Serial(port, BDPRESSURE_BAUD, timeout=0.6)
        time.sleep(0.2)
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        for cmd in BDPRESSURE_PROBE_CMDS:
            ser.write(cmd)
            line = ser.readline()
            text = line.decode('utf-8', errors='ignore').strip()
            logging.info(
                "bdpressure auto-detect: port=%s baud=%d cmd=%r response=%r"
                % (port, BDPRESSURE_BAUD, cmd, text)
            )
            # Lowercase v = bdpressure; capital V = bdwidth
            if BDPRESSURE_VERSION_MARKER in text:
                ser.close()
                return port, text

        ser.close()
    except Exception as e:
        logging.warning("bdpressure auto-detect: error probing %s at %d baud: %s"
                        % (port, BDPRESSURE_BAUD, e))
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
    return port, None

def auto_detect_bdpressure_port(baud=38400):
    # --- Pass 1: CH340-labelled ports ---
    ch340_ports = _list_ch340_ports()
    logging.info("bdpressure auto-detect: CH340 candidates = %s" % ch340_ports)
    for port in ch340_ports:
        matched_port, resp = _probe_port_for_bdpressure(port)
        if resp is not None:
            logging.info(
                "bdpressure auto-detect: found BD_Pressure on %s (response: %r)" % (matched_port, resp)
            )
            return matched_port

    # --- Pass 2: all serial ports (fallback) ---
    all_ports = _list_all_serial_ports()
    remaining = [p for p in all_ports if p not in ch340_ports]
    logging.info(
        "bdpressure auto-detect: CH340 pass found nothing; "
        "trying remaining ports: %s" % remaining
    )
    for port in remaining:
        matched_port, resp = _probe_port_for_bdpressure(port)
        if resp is not None:
            logging.info(
                "bdpressure auto-detect: found BD_Pressure on %s (response: %r)" % (matched_port, resp)
            )
            return matched_port

    logging.warning(
        "bdpressure auto-detect: BD_Pressure not found. "
        "Probed CH340=%s + fallback=%s. "
        "Check klippy.log for per-port responses." % (ch340_ports, remaining)
    )
    return None


BDP_CHIP_ADDR = 4
BDP_I2C_SPEED = 10000
BDP_REGS = {
     '_version' : 0x0,
     '_measure_data' : 15,
      'pa_probe_mode' : 48, ## 7= CLOCK_OSR_16384  2=CLOCK_OSR_512
     'raw_data_out' : 49,
     'probe_thr' : 50,
     'rang' : 51,
     'reset_probe' : 52,
     'invert_data' : 53

}

PIN_MIN_TIME = 0.100
RESEND_HOST_TIME = 0.300 + PIN_MIN_TIME
MAX_SCHEDULE_TIME = 5.0


class BD_Pressure_Advance:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.port = config.get("port")
        self.old_res=''
        # if config.get("resistance1", None) is None:
        self.thrhold = config.getint('thrhold', 4, minval=1) 
        if "i2c" in self.port:  
            self.i2c = bus.MCU_I2C_from_config(config, BDP_CHIP_ADDR, BDP_I2C_SPEED)
        elif "usb" in self.port:
            self._baud = config.getint('baud', 38400, minval=2400)
            configured_serial = config.get("serial", None)
            if configured_serial is None or configured_serial.strip().lower() == "auto":
                # Auto-detect: scan CH340 ports and identify BD_Pressure
                self.usb_port = auto_detect_bdpressure_port(self._baud)
                if self.usb_port is None:
                    raise config.error(
                        "BD_Pressure_Advance: could not auto-detect USB port. "
                        "No CH340 device responded as a BD_Pressure sensor. "
                        "Set 'serial' explicitly in the config if auto-detection fails."
                    )
                logging.info("BD_Pressure_Advance: using auto-detected port %s" % self.usb_port)
            else:
                self.usb_port = configured_serial
            self.usb = serial.Serial(self.usb_port, self._baud, timeout=0.5)
            self.usb.reset_input_buffer()
            self.usb.reset_output_buffer()
        self.PA_data = []    
        self.bd_name = config.get_name().split()[1]     
        self.gcode = self.printer.lookup_object('gcode')
        self._invert_stepper_x, self.mcu_enable_pin_x = self.enable_pin_init(config,"stepper_x")
        self._invert_stepper_x1, self.mcu_enable_pin_x1 = self.enable_pin_init(config,"stepper_x1")
        self._invert_stepper_y, self.mcu_enable_pin_y = self.enable_pin_init(config,"stepper_y")
        self._invert_stepper_y1, self.mcu_enable_pin_y1 = self.enable_pin_init(config,"stepper_y1")
        self.last_state = 0
        self.gcode.register_mux_command("SET_BDPRESSURE", "NAME", self.bd_name,
                                   self.cmd_SET_BDPRESSURE,
                                   desc=self.cmd_SET_BDPRESSURE_help)   
        self.printer.register_event_handler('klippy:ready',
                                                self._handle_ready)
        self.printer.register_event_handler("homing:homing_move_begin",
                                            self.handle_homing_move_begin)                                        

    def set_probe_mode(self):
        response = ""
        if "usb" == self.port:
            self.usb.write('e;'.encode())
            self.usb.write((str(self.thrhold)+';').encode())
           # response += self.usb.readline().decode('utf-8', errors='ignore').strip()
        elif "i2c" == self.port: 
          #  response += self.read_register('_version', 15).decode('utf-8', errors='ignore')
          #  self.gcode.respond_info("%s "%(response))
            #self.write_register('endstop_thr',6)
            self.write_register('pa_probe_mode',2)
            self.write_register('probe_thr',self.thrhold)


    def _handle_ready(self):
        #self.set_probe_mode()
        self.toolhead = self.printer.lookup_object('toolhead')
        
         
    def handle_homing_move_begin(self, hmove):
        self.set_probe_mode()
       # if self.mcu_probe in hmove.get_mcu_endstops():
        #    self.mcu_probe.probe_prepare(hmove)        
        
    cmd_SET_BDPRESSURE_help = "cmd for BD_PRESSURE sensor,SET_BDPRESSURE NAME=xxx COMMAND=START/STOP/RESET_PROBE/READ VALUE=X"
    def cmd_SET_BDPRESSURE(self, gcmd):
        # Read requested value
        cmd = gcmd.get('COMMAND')
       # self.gcode.respond_info("Send %s to bdpressure:%s"%(cmd,self.bd_name))
        if 'START' in cmd:
            self.cmd_start(gcmd)
        elif 'STOP' in cmd:  
            self.cmd_stop(gcmd)
        elif 'RESET_PROBE' in cmd:  
            self.cmd_reset_probe(gcmd) 
        elif 'READ' in cmd:  
            self.cmd_read(gcmd)  
        
            
            
    def _resend_current_val(self, eventtime):
        if self.last_value == self.shutdown_value:
            self.reactor.unregister_timer(self.resend_timer)
            self.resend_timer = None
            return self.reactor.NEVER

        systime = self.reactor.monotonic()
        print_time = self.mcu_enable_pin_x.get_mcu().estimated_print_time(systime)
       # print_time = self.mcu_enable_pin_y.get_mcu().estimated_print_time(systime)
        time_diff = (self.last_print_time + self.resend_interval) - print_time
        if time_diff > 0.:
            # Reschedule for resend time
            return systime + time_diff
        self._set_pin(print_time + PIN_MIN_TIME, self.last_value, True)
        return systime + self.resend_interval

    def enable_pin_init(self, config, stepper_name):

        stconfig = config.getsection(stepper_name) 
        if stconfig is None:
            return None, None
        enable_pin_s = stconfig.get('enable_pin', None) 
        if enable_pin_s is None:
            return None, None
        logging.info(" init %s"%(stepper_name))       
        self.printer = config.get_printer()
        ppins = self.printer.lookup_object('pins')
        # Determine pin type

        pin_params = ppins.lookup_pin(enable_pin_s, can_invert=True, can_pullup=True,share_type='stepper_enable')
        mcu_pin_s = pin_params['chip'].setup_pin('digital_out', pin_params)
        _invert_stepper = pin_params['invert']

   
        self.scale = 1.
        self.last_print_time = 0.
        # Support mcu checking for maximum duration
        self.reactor = self.printer.get_reactor()
        self.resend_timer = None
        self.resend_interval = 0.
        max_mcu_duration = config.getfloat('maximum_mcu_duration', 0.,
                                           minval=0.500,
                                           maxval=MAX_SCHEDULE_TIME)
        mcu_pin_s.setup_max_duration(max_mcu_duration)
        if max_mcu_duration:
            config.deprecate('maximum_mcu_duration')
            self.resend_interval = max_mcu_duration - RESEND_HOST_TIME
        # Determine start and shutdown values
        static_value = (_invert_stepper==True) #config.getfloat('static_value', None,
                           #            minval=0., maxval=self.scale)
        self.last_value = self.shutdown_value = static_value / self.scale
        mcu_pin_s.setup_start_value(self.last_value, self.shutdown_value)
        return _invert_stepper,mcu_pin_s
                                       
    def _set_pin(self, print_time, value, is_resend=False):
        if value == self.last_value and not is_resend:
            return
        print_time = max(print_time, self.last_print_time + PIN_MIN_TIME)
        self.mcu_enable_pin_x.set_digital(print_time, value)
        self.mcu_enable_pin_y.set_digital(print_time, value)
        if self.mcu_enable_pin_x1 is not None:
            self.mcu_enable_pin_x1.set_digital(print_time, value) 
        if self.mcu_enable_pin_y1 is not None:
            self.mcu_enable_pin_y1.set_digital(print_time, value) 
        self.last_value = value
        self.last_print_time = print_time
        if self.resend_interval and self.resend_timer is None:
            self.resend_timer = self.reactor.register_timer(
                self._resend_current_val, self.reactor.NOW)        
    
    def cmd_start(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        ##disable y motor
        toolhead.register_lookahead_callback(
               lambda print_time: self._set_pin(print_time, self._invert_stepper_x==False))  

        self.PA_data=[] 
        self.last_state = 1
        response = ""
        if "usb" == self.port:
            self.usb.reset_input_buffer()
            self.usb.reset_output_buffer()

            # Send 'D;' once to enable data output before entering the retry loop
            self.usb.write('D;'.encode())
            toolhead.dwell(0.4)
            
            #raise self.printer.command_error("Must home before probe")
            PA_MODE_MAX_RETRIES = 5
            pa_mode_confirmed = False
            # Accumulate raw bytes across all attempts
            raw = b''

            for attempt in range(PA_MODE_MAX_RETRIES):
                self.usb.write('l;'.encode())
                toolhead.dwell(0.4)

                # Poll the serial buffer for up to 0.6s; break as soon as 'PA mode' is found
                deadline = self.reactor.monotonic() + 0.6
                while self.reactor.monotonic() < deadline:
                    waiting = self.usb.in_waiting
                    if waiting:
                        raw += self.usb.read(waiting)
                    if b'PA mode' in raw:
                        break
                    time.sleep(0.05)

                text = raw.decode('utf-8', errors='ignore').strip()
                logging.info(
                    "bdpressure cmd_start: attempt %d/%d, response=%r"
                    % (attempt + 1, PA_MODE_MAX_RETRIES, text)
                )

                if 'PA mode' in text:
                    response += text
                    pa_mode_confirmed = True
                    break

            # Flush buffers at exit: whether succeeded or exhausted all retries
            self.usb.reset_input_buffer()
            self.usb.reset_output_buffer()
            if not pa_mode_confirmed:
                self.gcode.respond_info(
                    "BD_Pressure WARNING: 'PA mode' not received after %d attempts on %s. "
                    "Please check the USB connection and sensor firmware."
                    % (PA_MODE_MAX_RETRIES, self.usb_port)
                )
                self.last_state=0
                return

        elif "i2c" == self.port: 
            
            #self.write_register('endstop_thr',6)
          #  toolhead.dwell(0.2)
            self.write_register('pa_probe_mode',7)
        #    toolhead.dwell(0.4)
            self.write_register('raw_data_out',0)
            response += self.read_register('_version', 15).decode('utf-8', errors='ignore')
        self.gcode.respond_info(".cmd_start %s: %s"%(self.port,response)) 

    def pa_data_process(self,gcmd,str_data):
        self.gcode.respond_info("%s: %s"%(self.bd_name,str_data))
        if 'R:' in str_data and ',' in str_data:
            R_v=str_data.strip().split('R:')[1].split(',')
          #  self.gcode.respond_info("%s %s"%(R_v[3],R_v[4]))
            if len(R_v)==5:                
                res=int(R_v[0])
                lk=int(R_v[1])
                rk=int(R_v[2])
                Hk=int(R_v[3])
                Ha=int(R_v[4].split('\n')[0])
                val_step = float(gcmd.get('VALUE'))
                pa_val = [val_step,res,lk,rk,Hk,Ha]
                self.PA_data.append(pa_val)
             #   self.gcode.respond_info("The Pressure Value at %f is res:%d,L:%d,R:%d,H:%d,Hav:%d"%(pa_val[0],pa_val[1],pa_val[2],pa_val[3],pa_val[4],pa_val[5])) 
          #  if len(self.PA_data)>=10: 
            num=len(self.PA_data)
            flag=1
            if num>=20:
                for s_pa in self.PA_data[num-5:]:
                    if s_pa[4]<2 or s_pa[5]<5:
                        flag=0
                        break
                if flag==1:         
                    self.stop_pa(gcmd)
            
        elif 'stop' in str_data:
            self.last_state=0
                        
    def cmd_read(self, gcmd):    
        self.bdw_data = ''    
        buffer = bytearray()
        response = ""
       # self.gcode.respond_info("cmd_read %s"%self.bd_name)
        if "usb" == self.port:
            if self.usb.is_open:
               # self.usb.write('R;\n'.encode())
                self.usb.timeout = 1
                
                try:
                    response = self.usb.read(self.usb.in_waiting or 1).decode('utf-8', errors='ignore').strip() 
                   # response += self.usb.readline().decode('utf-8', errors='ignore').strip()
                    #self.usb.write('l;'.encode())
                   # self.gcode.respond_info("%s: %s........"%(self.bd_name,response))
                except:
                    return False
                if response:
                    self.old_res=response
                    self.pa_data_process(gcmd,response)
                else:
                    self.pa_data_process(gcmd,self.old_res)
                    
        elif "i2c" == self.port:
            response = self.read_register('_measure_data', 32).decode('utf-8', errors='ignore').strip('\0')
            self.pa_data_process(gcmd,response)
        #if self.is_debug == True:
        #    self.gcode.respond_info("bdwidth, port:%s, width:%.3f mm (%d),motion:%d" % (self.port,self.lastFilamentWidthReading,
         #                                        self.raw_width,self.lastMotionReading))          
        return True       

    def read_register(self, reg_name, read_len):
        # read a single register
        regs = [BDP_REGS[reg_name]]
        params = self.i2c.i2c_read(regs, read_len)
        return bytearray(params['response'])

    def write_register(self, reg_name, data):
        if type(data) is not list:
            data = [data]
        reg = BDP_REGS[reg_name]
        data.insert(0, reg)
        self.i2c.i2c_write(data)

    def stop_pa(self,gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        ##enable y motor
        toolhead.register_lookahead_callback(
                lambda print_time: self._set_pin(print_time, self._invert_stepper_x==True))
        self.last_state = 0     
        response = ""
        if "usb" == self.port:
            self.usb.write('e;'.encode())
            self.usb.write('D;'.encode())
           # response += self.usb.readline().decode('utf-8', errors='ignore').strip()
        elif "i2c" == self.port: 
           # response += self.read_register('_version', 15).decode('utf-8', errors='ignore')
            #self.write_register('endstop_thr',6)
            self.write_register('pa_probe_mode',2)
            self.write_register('raw_data_out',0)

            
    def cmd_stop(self, gcmd):
        
        self.stop_pa(gcmd)     
        if len(self.PA_data)>=5: 
            self.PA_data.pop(0)
            self.PA_data.pop(1)
            self.PA_data.pop(2)
            self.PA_data.pop(3)
            self.PA_data.pop(4)
           # for s_pa in self.PA_data:
           #     if s_pa[5]<0 or s_pa[4]<=0:
            #        self.PA_data.remove(s_pa)
            min_s = self.PA_data[-1]  
            min_index = len(self.PA_data)-1
            for index, s_pa in enumerate(reversed(self.PA_data)):
                if s_pa[4]<5:
                    min_index=len(self.PA_data)-1-index
                    break
            if min_index == len(self.PA_data)-1:
                for index, s_pa in enumerate(reversed(self.PA_data)):
                    if s_pa[5]<5:
                        min_index=len(self.PA_data)-1-index
                        break   
            if  min_index == len(self.PA_data)-1:
                self.gcode.respond_info("Calc the best Pressure Advance error!")  
                return
            min_r= self.PA_data[-1]   
            for s_pa in self.PA_data[min_index:]:
                if (min_r[1]+abs(min_r[5]))>(s_pa[1]+abs(s_pa[5])):
                    min_r=s_pa
            min_s=min_r      
           # min_s=self.PA_data[min_index]    

            self.gcode.respond_info("Calc the best Pressure Advance: %f, %d %d"%(min_s[0],min_s[1],min_index))  
            set_pa = 'SET_PRESSURE_ADVANCE ADVANCE='+str(min_s[0])
            self.gcode.run_script_from_command(set_pa)
            
        else:
            self.gcode.respond_info("No PA calibration data or number is <=5") 
         
    def cmd_reset_probe(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        ##enable y motor
      #  toolhead.register_lookahead_callback(
       #         lambda print_time: self._set_pin(print_time, self._invert_stepper_y==True))
            
        response = ""
        if "usb" == self.port:
            self.usb.write('N;'.encode())
            response += self.usb.readline().decode('utf-8', errors='ignore').strip()
        elif "i2c" == self.port: 
            #response += self.read_register('reset_probe',52).decode('utf-8')   
            self.write_register('reset_probe',1)

    def get_status(self, eventtime=None):
        if self.last_state:
            return {'state': "START"} 
        return {'state': "STOP"}        

def load_config_prefix(config):
    return BD_Pressure_Advance(config)