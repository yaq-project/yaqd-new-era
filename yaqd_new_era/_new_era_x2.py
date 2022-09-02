import asyncio
import time
from typing import Dict, List, Optional

from yaqd_core import UsesUart, UsesSerial, IsHomeable, HasPosition, IsDaemon, IsDiscrete
from ._serial import SerialDispatcher


class NewEraX2(UsesUart, UsesSerial, IsHomeable, IsDiscrete, HasPosition, IsDaemon):
    _kind = "new-era-x2"

    errors = {
        "?" : "Command unrecognized",
        "NA" : "Command N/A",
        "OOR" : "Command data out of range",
        "COM" : "Invalid Serial packet",
        "IGN" : "Command ignored",
    }

    prompts = {
        "I" : "infusing",
        "W" : "withdrawing",
        "S" : "stopped",
        "P" : "paused",
        "T" : "timed pause",
        "U" : "user wait",
        "X" : "purging",
    }

    alarms = {
        "R" : "Pump Reset (Power interrupted)",
        "S" : "Pump Stall",
        "T" : "Serial Timeout",
        "E" : "Program error",
        "O" : "Program phase number out of range",
        "U" : "User Commanded Alarm (added)",
    }
        
    errors_dict = {value: key for key, value in errors.items()}
    alarms_dict = {value: key for key, value in alarms.items()}
    prompts_dict = {value: key for key, value in prompts.items()}


    serial_dispatchers: Dict[str, SerialDispatcher] = {}

    def __init__(self, name, config, config_filepath):
        super().__init__(name, config, config_filepath)
        self._homing = False
        self._busy=False
        #self._ignore_ready = 0
        addr=int(config["address"])
        if int(addr) > 7 or int(addr)<0:
            return IndexError("pump systems currently only addressable in range 00-07")
        else:
            self._address=addr
        self._serial = SerialDispatcher(config["serial_port"], baudrate=config["baud_rate"])
        NewEraX2.serial_dispatchers[config["serial_port"]] = self._serial
        self._state["current_alarm"]=""
        self._state["current_prompt"]=""
        self.loop = asyncio.get_event_loop()
        self.read_from_serial()


    def busy(self):
        return bool(super().busy())


    def _set_position(self, position):

        async def _wait_for_ready_and_set_position(self, position):
            if float(position) != float(0.0):
                pos=True
            else:
                pos=False
            if pos:
                if ( "P" in self._state["current_prompt"] or "S" in self._state["current_prompt"]) and (self._state["current_alarm"] ==""):
                    strn="*RUN\r"
                    await self._serial.write_queue.put(strn.encode())
                    self.logger.info(f"start: time {time.localtime()}") 
                if self._busy and not self._homing:
                    await self._not_busy_sig.wait()
                    self._busy = True
            else:
                strn="*STP\r"
                await self._serial.write_queue.put(strn.encode())
                self.logger.info(f"stop: time {time.localtime()}") 
                if self._busy and not self._homing:
                    await self._not_busy_sig.wait()
                    self._busy = True
        
        self._serial.loop.create_task(_wait_for_ready_and_set_position(self, position))


    def set_alarm(self, alarm):
        assert isinstance(alarm, bool)

        async def _wait_for_ready_and_set_alarm(self,alarm):
            if alarm:
                strn1="*BUZ2\r"
                strn2="*OUT51\r"
                await self._serial.write_queue.put(strn1.encode())
                await self._serial.write_queue.put(strn2.encode())
                al=self._state["current_alarm"]
                self.logger.info(f"alarm sounded  type:{al}  time:{time.localtime()}") 
                if self._busy and not self._homing:
                    await self._not_busy_sig.wait()
                    self._busy = True
            else:
                strn1="*OUT50\r"
                await self._serial.write_queue.put(strn1.encode())
                self.logger.info(f"alarm reset time:{time.localtime()}") 
                if self._busy and not self._homing:
                    await self._not_busy_sig.wait()
                    self._busy = True
        
        self._serial.loop.create_task(_wait_for_ready_and_set_alarm(self, alarm))

    def get_alarm(self):
        al=self._state["current_alarm"]
        if al is not "":
            alarm=True
        else:
            alarm=False
        return alarm

    async def update_state(self):
        while True:
            strn="*\r"
            await self._serial.write_queue.put(strn.encode())
            await asyncio.sleep(0.25)

        '''
            if not self._busy:
                try:
                    await asyncio.wait_for(self._busy_sig.wait(), 1)
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(0.25)
        '''

    def read_from_serial(self):
        self.loop.create_task(self._async_read_from_serial())

    async def _async_read_from_serial(self):
        while True:
            prompt, alarm, error, out= self._serial.workers[self._address]
            
            if prompt is not None:
                self._state["current_prompt"]=prompt
                self._state["current_alarm"]=""
            if alarm is not None:
                if alarm != self._state["current_alarm"]:
                    self.set_alarm(True)
                    self._state["current_alarm"]=alarm
                    #log the alarm 
            else:
                alarm=""
                if (alarm != self._state["current_alarm"]):
                    self.set_alarm(False)
                    self._state["current_alarm"]=""
                    #log the alarm turn off
                pass
            if error is not None:
                self.logger.info(f"command error: {error}") 
                pass
            if out is not None:
                self.logger.info(f"return: {out}") 
                #process rate into self._rate
                pass
            await asyncio.sleep(0.25)


    def home(self):
        self._busy = True
        self._loop.create_task(self._home())

    async def _home(self):
        self._homing = True
        self._busy = True
        #await self.write_queue.put() info here
        self._homing = False
        self._busy=False


    def direct_serial_write(self, command: bytes):
        self._busy = True
        self._serial.write_queue.put_nowait(command)

    def close(self):
        self._serial.flush()
        self._serial.close()
        

if __name__ == "__main__":
    NewEraX2.main()