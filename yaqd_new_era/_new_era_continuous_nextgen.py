import re
import asyncio
import time
from typing import Dict, Any, List

from yaqd_core import IsDaemon, HasPosition, IsDiscrete, UsesSerial, UsesUart, aserial


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
            self._config["serial_port"], baudrate=self._config["baud_rate"], eol="\x03".encode()
        )
        self._rate = None
        self._purging = False
        self._busy=False
        # the pump ignores the very first RS232 command it sees after power cycling for some reason
        # so we send DIS as a junk command to get things going
        self._ser.write(f"*DIS\r".encode())
        self.reset_alarm()
        self._loop.create_task(self._get_rate())  # cache the rate later
        self._cached_alarm= ""
        self._cached_prompt = ""
        self.set_position(self._state.get("destination", 0))

    def close(self):
        self._ser.close()

    def direct_serial_write(self, message: bytes):
        self._ser.write(message)

    def get_rate(self) -> float:
        return self._rate

    async def _get_rate(self):
        try:
            prompt, alarm, out = await self._write("*RAT\r".encode())
            match = rate_regex.match(out)
            self._rate = float(match[1])
        except TypeError:
            await self._get_rate()

    def _set_position(self, position):
        if position >= 0.5:
            self._ser.write("*RUN\r".encode())
        else:
            if self._cached_prompt not in ["S", "P"]:
                self._ser.write("*STP\r".encode())

    def set_rate(self, rate):
        rate = f"{rate:0.3f}"[:5]
        self._ser.write(f"*RAT{rate}\r".encode())
        self._loop.create_task(self._get_rate())

    def set_alarm(self, alarm):
        """Sets the alarm on the pump."""
        assert (alarm==True or alarm==False)
        if alarm:
            self._ser.write("*OUT51\r".encode())
            self._ser.write("*BUZ1\r".encode())
            self._state["current_alarm"]="U"
            self.logger.info(f"Alarmed by User: time {time.localtime()}")
        else:
            self._ser.write("*OUT50\r".encode())
            self._ser.write("*BUZ0\r".encode())
            self._state["current_alarm"]=""
  
    def get_alarm(self):
        alarm=self._state["current_alarm"]
        if (alarm == None or alarm == ""):
            return False
        else:
            return True

    async def update_state(self):
        while True:
            prompt, alarm, out = await self._write("".encode())
            if prompt is not None:
                if self._cached_prompt != prompt:
                    self._cached_prompt = prompt
                    self.logger.info(f"prompt change {prompt}: time {time.localtime()}")
                    self._state["current_prompt"]=prompt

            if alarm is not None:
                if self._cached_alarm != alarm:
                    self._cached_alarm = alarm
                    self.logger.info(f"Alarm {alarm}: time {time.localtime()}")
                    self._state["current_alarm"]=alarm
                    prompt, alarm, out = await self._write("BUZ12")
                    prompt, alarm, out = await self._write("OUT51")

            if self._state["destination"] >= 0.5:
                self._busy = prompt not in ["I", "W"]
            else:
                self._busy = prompt in ["I", "W"]
            if not self._busy:
                self._state["position"] = self._state["destination"]
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
