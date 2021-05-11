__all__ = ["NewEraNe1000"]

import re
import asyncio
import time
from typing import Dict, Any, List

from yaqd_core import IsDaemon, HasPosition, HasLimits, UsesSerial, UsesUart, aserial


dis_regex = re.compile(r"^I([\.\d]+)W([\.\d]+)")
rate_regex = re.compile(r"([\.\d]+)")

rate_units = {
    "microliter/minute": "UM",
    "milliliter/minute": "MM",
    "microliter/hour": "UH",
    "milliliter/hour": "MH",
}
volume_units = {"microliter": "UL", "milliliter": "ML"}


class NewEraNe1000(UsesUart, UsesSerial, HasLimits, HasPosition, IsDaemon):
    _kind = "new-era-ne1000"

    def __init__(self, name, config, config_filepath):
        super().__init__(name, config, config_filepath)
        self._ser = aserial.ASerial(
            self._config["serial_port"], baudrate=self._config["baud_rate"], eol="\x03".encode()
        )
        self._rate = None
        self._purging = False
        # the pump ignores the very first RS232 command it sees after power cycling for some reason
        # so we send DIS as a junk command to get things going
        self._ser.write(f"{self._config['address']} DIS\r".encode())
        self._loop.create_task(self._get_rate())  # cache the rate later

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

    def prime(self):
        # withdraw fully
        self.logger.info("Priming.")
        self._busy = True
        self._ser.write(f"{self._config['address']} DIR WDR\r".encode())
        self._ser.write(f"{self._config['address']} PUR\r".encode())

    def purge(self):
        # purge fully
        # this also implies that the new position is zero
        self.logger.info("Purging.")
        self._busy = True
        self._purging = True
        self._ser.write(f"{self._config['address']} DIR INF\r".encode())
        self._ser.write(f"{self._config['address']} PUR\r".encode())

    def _set_position(self, position: float) -> None:
        self._loop.create_task(self._aset_position(position))

    async def _aset_position(self, position: float) -> None:
        await self._write("DIR STP")
        amount_from_here = position - self._state["position"]
        if abs(amount_from_here) > 99_999:
            raise ValueError("cannot represent a number that large over New Era RS232 interface!")
        elif abs(amount_from_here) < 0.001:
            return
        # set direction
        direction = "WDR" if amount_from_here > 0 else "INF"
        await self._write(f"DIR {direction}")
        # set volume
        units = volume_units[self._config["volume_units"]]
        await self._write(f"VOL {units}")
        vol = f"{abs(amount_from_here):0.3f}"[:5]
        await self._write(f"VOL {vol}")
        # run
        await self._write("RUN")

    def set_rate(self, rate):
        units = rate_units[self._config["rate_units"]]
        rate = f"{rate:0.3f}"[:5]
        self._ser.write(f"{self._config['address']} RAT C {rate} {units}\r".encode())
        self._loop.create_task(self._get_rate())

    async def update_state(self):
        while True:
            start_position = self._state["position"]
            while True:
                prompt, alarm, out = await self._write("DIS")
                if alarm is not None:
                    continue
                self.logger.debug(f"{prompt}, {alarm}, {out}")
                if not out.startswith("I"):
                    continue
                # prompt
                self._busy = prompt not in ("S", "P")
                # get current position
                try:
                    match = dis_regex.match(out)
                    infused = float(match[1])
                    withdrawn = float(match[2])
                    self._state["position"] = start_position - infused + withdrawn
                except (ValueError, TypeError) as e:
                    self.logger.error(e)
                    continue
                if not self._busy:
                    break
            # purging: once done, position goes to 0
            if self._purging:
                await self._write("STP")
                self._purging = False
                self._state["position"] = 0
                self._state["destination"] = 0
            # once busy is released, clear values from pump
            await self._write("CLD INF")
            await self._write("CLD WDR")
            try:
                await asyncio.wait_for(self._busy_sig.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

    async def _write(self, command):
        try:
            await asyncio.sleep(0.1)
            self._ser.reset_input_buffer()
            self._ser.flush()
            out = f"{self._config['address']} {command}\r".encode()
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
