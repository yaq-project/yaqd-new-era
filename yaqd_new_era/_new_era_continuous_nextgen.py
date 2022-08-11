from cmath import nan
import re
import asyncio
import time
from typing import Dict, Any, List
import serial
from yaqd_core import IsDaemon, HasPosition, IsDiscrete, UsesSerial, UsesUart, aserial
import numpy as np

"""
The full command set, alarms, prompts, addressing (for multiple pumps) mechanisms,
and errors are found in the NE4000 manual.   Additional commands may be found for the X2
firmware.

Current daemon assumes only one pump system is in communication with the associated
serial ("COM") port.   To avoid errors in addressing, all commands are preceded by an
asterisk which bypasses the requirement for tagging an address to the serial command but
then does not allow other devices on the serial line.

When queried (*):
Prompts
I - infusing
W - withdrawing
S - stopped
P - paused
T - timed pause
U - user wait
X - purging

Alarms
R - Pump Reset (Power interrupted)
S - Pump Stall
T - Serial timeout
E - Program error
O - Program phase number out of range
U - User Commanded Alarm (added)

Errors (Prompt fields)
? - Command unrecognized
NA- Command N/A
OOR Command data out of range
COM Invalid Serial packet
IGN Command ignored
"""


rate_regex = re.compile(r"([\.\d]+)")

class NewEraContinuousNextGen(UsesUart, UsesSerial, IsDiscrete, HasPosition, IsDaemon):
    _kind = "new-era-continuous-nextgen"

    def __init__(self, name, config, config_filepath):
        super().__init__(name, config, config_filepath)
        #self._state["destination"] = 0.0
        self._state["current_alarm"]=""
        self._state["current_prompt"]=""
        self._state["position_identifier"] = "paused" # possibly to be read from config
        self._ser = aserial.ASerial(
            self._config["serial_port"], baudrate=self._config["baud_rate"], parity=serial.PARITY_NONE, bytesize=8, stopbits=1, eol="\x03".encode()
        )
        self._rate = float(np.nan)
        self._purging = False
        self._busy=False
        self._updating=False
        # the pump ignores the very first RS232 command it sees after power cycling for some reason
        # so we send DIS as a junk command to get things going
        self._ser.reset_input_buffer()
        self._ser.flush()
        self._ser.write(f"*DIS\r".encode())
        self.set_alarm(False)
        #self._loop.create_task(self._get_rate())  # cache the rate later
        self._ser.reset_input_buffer()
        self._ser.flush()
        self._get_rate()
        self._get_rate()
        self._cached_alarm= ""
        self._cached_prompt = ""
        self._state["destination"] = 0.0
        self.set_position(0.0)
        
        self._update=True

    def close(self):
        self._ser.close()

    def direct_serial_write(self, message: bytes):
        self._ser.write(message)

    def get_rate(self) -> float:
        return self._rate

    def _get_rate(self):
        self._update=False
        self._ser.reset_input_buffer()
        self._ser.flush()
        self._ser.write(f"*RAT\r".encode())
        out=self._ser.readline()
        response = out.decode().strip()
        data = response[4:]
        if data=="":
            data=None
        if data:
            self._rate = float(data)
        self._update=True
        
    async def _set_position(self, position):
        self._update=False
        while self._updating:
            await asyncio.sleep(0.1)
        if "S" in self._state["current_prompt"]:
            if position >= 0.5:
                await self._write("STP")
                await self._write("RUN")
                self.logger.info(f"start: time {time.localtime()}") 
        else:
            if position <= 0.5:
                await self._write("RUN")
                await self._write("STP")
                self.logger.info(f"stop: time {time.localtime()}") 
        self._update=True

    def set_rate(self, rate):
        self._update=False
        while self._updating:
            time.sleep(0.1)
        rate = f"{rate:0.3f}"[:5]
        self._ser.reset_input_buffer()
        self._ser.flush()
        self._ser.write(f"*RAT{rate}\r".encode())
        self.logger.info(f"rate change to {rate}: time {time.localtime()}") 
        self._rate=rate
        self._update=True
        
    def set_alarm(self, alarm):
        """Sets the alarm on the pump."""
        assert (alarm==True or alarm==False)
        if alarm:
            self._ser.reset_input_buffer()
            self._ser.flush()
            self._ser.write(f"*OUT51\r".encode()) 
            self._ser.write(f"*BUZ2\r".encode())
            self._state["current_alarm"]="U"
            self.logger.info(f"Alarmed by User: time {time.localtime()}")
        else:
            self._ser.reset_input_buffer()
            self._ser.flush()
            self._ser.write(f"*OUT50\r".encode()) 
            self._ser.write(f"*BUZ2\r".encode())
            self._state["current_alarm"]=""
            self.logger.info(f"Alarm deactivated by User: time {time.localtime()}")


    def get_alarm(self):
        alarm=self._state["current_alarm"]
        if (alarm == None or alarm == ""):
            return False
        else:
            return True

    async def update_state(self):
        while True:
            prompt, alarm, out = await self._write("")
            if prompt is not None:
                if self._cached_prompt != prompt:
                    self._cached_prompt = prompt
                    self.logger.info(f"prompt change {prompt}: time {time.localtime()}")
                    self._state["current_prompt"]=prompt
                    self._state["current_alarm"] = ""
            if alarm is not None:
                if self._cached_alarm != alarm:
                    self._cached_alarm = alarm
                    self.logger.info(f"Alarm {alarm}: time {time.localtime()}")
                    self._state["current_alarm"]=alarm
                    prompt, alarm, out = await self._write("BUZ12")
                    prompt, alarm, out = await self._write("OUT51")
            if self._state["position"] >= 0.5:
                self._state["position_identifier"] = "pumping"
            else:
                self._state["position_identifier"] = "paused"

            await asyncio.sleep(0.2)

    async def _write(self, command):
        try:
            await asyncio.sleep(0.1)
            self._ser.reset_input_buffer()
            self._ser.flush()
            self.logger.debug(f"Sent command: {command}")
            out = f"*{command}\r".encode()
            response = await self._ser.awrite_then_readline(out)
            response = response.decode().strip()
            self.logger.debug(f"Received response: {response}")
            address = int(response[1:3])
            prompt = alarm = data = None
            if response[3] == "A":  # alarm
                alarm = response[5]
            else:
                prompt = response[3]
                data = response[4:]
            return prompt, alarm, data
        except (UnicodeDecodeError, ValueError) as e:  # try again
            self.logger.error(e)
            return await self._write(command)
