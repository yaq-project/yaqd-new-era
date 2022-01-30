import re
import asyncio
import time
from typing import Dict, Any, List

from yaqd_core import IsDaemon, HasPosition, IsDiscrete, UsesSerial, UsesUart, aserial


"""
The next generation continuous pumping program works in multiple phases.

When queried (PHN):
I - infusing
W - withdrawing
S - stopped
P - paused
T - timed pause
U - user wait
X - purging
"""


rate_regex = re.compile(r"([\.\d]+)")


class NewEraContinuousNextGen(UsesUart, UsesSerial, IsDiscrete, HasPosition, IsDaemon):
    _kind = "new-era-continuous-nextgen"

    def __init__(self, name, config, config_filepath):
        super().__init__(name, config, config_filepath)
        self._ser = aserial.ASerial(
            self._config["serial_port"], baudrate=self._config["baud_rate"], eol="\x03".encode()
        )
        self._rate = None
        self._purging = False
        # the pump ignores the very first RS232 command it sees after power cycling for some reason
        # so we send DIS as a junk command to get things going
        self._ser.write(f"*DIS\r".encode())
        self._loop.create_task(self._get_rate())  # cache the rate later
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
            prompt, alarm, out = await self._write("RAT")
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
        self._ser.write(f"* RAT {rate}\r".encode())
        self._loop.create_task(self._get_rate())

    async def update_state(self):
        while True:
            prompt, alarm, out = await self._write("PHN")
            self._cached_prompt = prompt
            if self._state["destination"] >= 0.5:
                self._busy = self._cached_prompt not in ["I", "W"]
            else:
                self._busy = self._cached_prompt in ["I", "W"]
            if not self._busy:
                self._state["position"] = self._state["destination"]
            if self._state["position"] >= 0.5:
                self._state["position_identifier"] = "pumping"
            else:
                self._state["position_identifier"] = "paused"

    async def _write(self, command):
        try:
            await asyncio.sleep(0.1)
            self._ser.reset_input_buffer()
            self._ser.flush()
            self.logger.debug(f"Sent command: {command}")
            out = f"*{command}\r".encode()
            response = await self._ser.awrite_then_readline(out)
            response = response.decode().strip()
            self.logger.debug(f"Recieved response: {response}")
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
