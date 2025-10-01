import logging
import math
import statistics
import serial
import os


from . import bus
from . import filament_switch_sensor


BDP_CHIP_ADDR = 4
BDP_I2C_SPEED = 100000
BDP_REGS = {
     '_version' : 0x0,
     '_measure_data' : 15,
      'pa_probe_mode' : 48, ## 7= CLOCK_OSR_16384  2=CLOCK_OSR_512
     'raw_data_out' : 49,
     'probe_thr' : 50

}

PIN_MIN_TIME = 0.100
RESEND_HOST_TIME = 0.300 + PIN_MIN_TIME
MAX_SCHEDULE_TIME = 5.0


class BD_Pressure_Advance:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.port = config.get("port")

        # if config.get("resistance1", None) is None:
        if "i2c" in self.port:  
            self.i2c = bus.MCU_I2C_from_config(config, BDP_CHIP_ADDR, BDP_I2C_SPEED)
        elif "usb" in self.port:
            self.usb_port = config.get("serial")
            baudrate = 500000
            self.usb = serial.Serial(self.usb_port, baudrate,timeout=1)
            self.usb.reset_input_buffer()
            self.usb.reset_output_buffer()
        self.PA_data = []    
        self.bd_name = config.get_name().split()[1]     
        self.gcode = self.printer.lookup_object('gcode')
        self.enable_pin_init(config)
        self.last_state = 0
        self.gcode.register_mux_command("SET_BDPRESSURE", "NAME", self.bd_name,
                                   self.cmd_SET_BDPRESSURE,
                                   desc=self.cmd_SET_BDPRESSURE_help)   
        self.printer.register_event_handler('klippy:ready',
                                                self._handle_ready)

    def _handle_ready(self):
        
        #self.toolhead = self.printer.lookup_object('toolhead')
        response = ""
        if "usb" == self.port:
            self.usb.write('e;'.encode())
            response += self.usb.readline().decode('ascii').strip()
        elif "i2c" == self.port: 
            response += self.read_register('_version', 15).decode('utf-8')
            #self.write_register('endstop_thr',6)
            self.write_register('pa_probe_mode',2)
         
        
        
    cmd_SET_BDPRESSURE_help = "cmd for BD_PRESSURE sensor,SET_BDPRESSURE NAME=xxx COMMAND=START/STOP/RESET_PROBE/READ VALUE=X"
    def cmd_SET_BDPRESSURE(self, gcmd):
        # Read requested value
        cmd = gcmd.get('COMMAND')
        self.gcode.respond_info("Send %s to bdpressure:%s"%(cmd,self.bd_name))
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
        print_time = self.mcu_pin_x.get_mcu().estimated_print_time(systime)
        print_time = self.mcu_pin_y.get_mcu().estimated_print_time(systime)
        time_diff = (self.last_print_time + self.resend_interval) - print_time
        if time_diff > 0.:
            # Reschedule for resend time
            return systime + time_diff
        self._set_pin(print_time + PIN_MIN_TIME, self.last_value, True)
        return systime + self.resend_interval


    def enable_pin_init(self, config):

        stconfig = config.getsection('stepper_x')     
        enable_pin_x = stconfig.get('enable_pin') 
        stconfig = config.getsection('stepper_y')
        enable_pin_y = stconfig.get('enable_pin')
       # self.gcode.respond_info("%s: %s"%(self.bd_name,response))
        
        self.printer = config.get_printer()
        ppins = self.printer.lookup_object('pins')
        # Determine pin type

        pin_params = ppins.lookup_pin(enable_pin_x, can_invert=True, can_pullup=True,share_type='stepper_enable')
        self.mcu_pin_x = pin_params['chip'].setup_pin('digital_out', pin_params)
        self._invert_stepper_x = pin_params['invert']
        pin_params = ppins.lookup_pin(enable_pin_y, can_invert=True, can_pullup=True,share_type='stepper_enable')
        self.mcu_pin_y = pin_params['chip'].setup_pin('digital_out', pin_params)
        self._invert_stepper_y = pin_params['invert']
           # self.mcu_pin_x = ppins.setup_pin('digital_out', config.get('pin'))
        self.scale = 1.
        self.last_print_time = 0.
        # Support mcu checking for maximum duration
        self.reactor = self.printer.get_reactor()
        self.resend_timer = None
        self.resend_interval = 0.
        max_mcu_duration = config.getfloat('maximum_mcu_duration', 0.,
                                           minval=0.500,
                                           maxval=MAX_SCHEDULE_TIME)
        self.mcu_pin_x.setup_max_duration(max_mcu_duration)
        self.mcu_pin_y.setup_max_duration(max_mcu_duration)
        if max_mcu_duration:
            config.deprecate('maximum_mcu_duration')
            self.resend_interval = max_mcu_duration - RESEND_HOST_TIME
        # Determine start and shutdown values
        static_value = (self._invert_stepper_y==True) #config.getfloat('static_value', None,
                           #            minval=0., maxval=self.scale)
        self.last_value = self.shutdown_value = static_value / self.scale
        self.mcu_pin_x.setup_start_value(self.last_value, self.shutdown_value)
        self.mcu_pin_y.setup_start_value(self.last_value, self.shutdown_value)
        # Register commands

                                       

    def _set_pin(self, print_time, value, is_resend=False):
        if value == self.last_value and not is_resend:
            return
        print_time = max(print_time, self.last_print_time + PIN_MIN_TIME)
        self.mcu_pin_x.set_digital(print_time, value)
        self.mcu_pin_y.set_digital(print_time, value)
        self.last_value = value
        self.last_print_time = print_time
        if self.resend_interval and self.resend_timer is None:
            self.resend_timer = self.reactor.register_timer(
                self._resend_current_val, self.reactor.NOW)        
    
    def cmd_start(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        ##disable y motor
        toolhead.register_lookahead_callback(
               lambda print_time: self._set_pin(print_time, self._invert_stepper_y==False))  
        self.PA_data=[] 
        self.last_state = 1
        response = ""
        if "usb" == self.port:
            response += self.usb.readline().decode('ascii').strip()
            self.usb.reset_input_buffer()
            self.usb.reset_output_buffer()
            while self.usb.in_waiting:
                self.usb.read(self.usb.in_waiting)
            self.usb.write('l;'.encode())
            toolhead.dwell(0.4)
            self.usb.write('D;'.encode())
            toolhead.dwell(0.4) 
            self.usb.write('l;'.encode())
            toolhead.dwell(0.4)
    
        elif "i2c" == self.port: 
            
            #self.write_register('endstop_thr',6)
          #  toolhead.dwell(0.2)
            self.write_register('pa_probe_mode',7)
        #    toolhead.dwell(0.4)
            self.write_register('raw_data_out',1)
            response += self.read_register('_version', 15).decode('utf-8')
        self.gcode.respond_info("cmd_start %s: %s"%(self.port,response)) 

    def pa_data_process(self,gcmd,str_data):
        self.gcode.respond_info("%s: %s"%(self.bd_name,str_data))
        if 'R:' in str_data and ',' in str_data:
            R_v=str_data.strip().split('R:')[1].split(',')
            self.gcode.respond_info("%s %s"%(R_v[3],R_v[4]))
            if len(R_v)==5:                
                res=int(R_v[0])
                lk=int(R_v[1])
                rk=int(R_v[2])
                Hk=int(R_v[3])
                Ha=int(R_v[4].split('\n')[0])
                val_step = float(gcmd.get('VALUE'))
                pa_val = [val_step,res,lk,rk,Hk,Ha]
                self.PA_data.append(pa_val)
                self.gcode.respond_info("The Pressure Value at %f is res:%d,L:%d,R:%d,H:%d,Hav:%d"%(pa_val[0],pa_val[1],pa_val[2],pa_val[3],pa_val[4],pa_val[5])) 
          #  if len(self.PA_data)>=10: 
            num=len(self.PA_data)
            flag=1
            if num>=20:
                for s_pa in self.PA_data[num-5:]:
                    if s_pa[4]<10 or s_pa[5]<10:
                        flag=0
                        break
                if flag==1:         
                    self.cmd_stop(gcmd)
            
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
                    response = self.usb.readline().decode('ascii').strip()
                except:
                    return False
                if response:
                    self.pa_data_process(gcmd,response)
                    
        elif "i2c" == self.port:
            response = self.read_register('_measure_data', 32).strip('\0')
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

    def cmd_stop(self, gcmd):
        toolhead = self.printer.lookup_object('toolhead')
        ##enable y motor
        toolhead.register_lookahead_callback(
                lambda print_time: self._set_pin(print_time, self._invert_stepper_y==True))
        self.last_state = 0     
        response = ""
        if "usb" == self.port:
            self.usb.write('e;'.encode())
            response += self.usb.readline().decode('ascii').strip()
        elif "i2c" == self.port: 
           # response += self.read_register('_version', 15).decode('utf-8')
            #self.write_register('endstop_thr',6)
            self.write_register('pa_probe_mode',2)
            self.write_register('raw_data_out',0)
            
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
                if s_pa[4]<10:
                    min_index=len(self.PA_data)-1-index
                    break
            if min_index == len(self.PA_data)-1:
                for index, s_pa in enumerate(reversed(self.PA_data)):
                    if s_pa[5]<10:
                        min_index=len(self.PA_data)-1-index
                        break   
            if  min_index == len(self.PA_data)-1:
                self.gcode.respond_info("Calc the best Pressure Advance error!")  
                return
            min_r= self.PA_data[-1]   
            for s_pa in self.PA_data[min_index:]:
                if min_r[1]>s_pa[1]:
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
        toolhead.register_lookahead_callback(
                lambda print_time: self._set_pin(print_time, self._invert_stepper_y==True))
            
        response = ""
        if "usb" == self.port:
            self.usb.write('G00;'.encode())
            response += self.usb.readline().decode('ascii').strip()
        elif "i2c" == self.port: 
            response += self.read_register('_version', 15).decode('utf-8')   

    def get_status(self, eventtime=None):
        if self.last_state:
            return {'state': "START"} 
        return {'state': "STOP"}        

def load_config_prefix(config):
    return BD_Pressure_Advance(config)
