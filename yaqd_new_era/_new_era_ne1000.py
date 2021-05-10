__all__ = ["NewEraNe1000"]

import asyncio
import time
from typing import Dict, Any, List

from yaqd_core import IsDaemon, HasPosition, HasLimits, UsesSerial, UsesUart, aserial


class NewEraNe1000(UsesUart, UsesSerial, HasLimits, HasPosition, IsDaemon):
    _kind = "new-era-ne1000"

    def __init__(self, name, config, config_filepath):
        super().__init__(name, config, config_filepath)
        self._ser = aserial.ASerial(
            self._config["serial_port"], baudrate=self._config["baud_rate"], eol="\x03".encode()
        )
        self._rate = None
        self._purging = False
        self._position = self._state["position"]
        # the pump ignores the very first RS232 command it sees after power cycling for some reason
        # so we send DIS as a junk command to get things going
        self._ser.write(f"{self._config['address']} DIS\r".encode())
        self._loop.create_task(self._get_rate())  # cache the rate later

    def close(self):
        self._ser.close()

    def direct_serial_write(self, message):
        self._ser.write(message.encode())

    def get_rate(self) -> float:
        return self._rate

    async def _get_rate(self):
        prompt, alarm, out = await self._write("RAT")
        # TODO: actually parse out rate

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
        self._ser.write(f"{self._config['address']} DIR STP\r".encode())
        amount_from_here = position - self._state["position"]
        if abs(amount_from_here) < 0.001:
            return
        if amount_from_here > 0:
            self._ser.write(f"{self._config['address']} DIR WDR\r".encode())
        elif amount_from_here < 0:
            self._ser.write(f"{self._config['address']} DIR INF\r".encode())
        time.sleep(0.1)
        self._ser.write(f"{self._config['address']} VOL {self._units}\r".encode())
        # amount needs special formatting
        if abs(amount_from_here) > 10_000:
            raise NotImplementedError
        vol = str(abs(amount_from_here))[:5]
        time.sleep(0.1)
        self._ser.write(f"{self._config['address']} VOL {vol}\r".encode())
        time.sleep(0.1)
        self._ser.write(f"{self._config['address']} RUN\r".encode())
        self._ser.flush()

    def set_rate(self, rate: float):
        self._ser.write(
            f"{self._config['address']} RAT C {rate} {self._config['rate_units']}\r".encode()
        )
        self._rate = None
        self._loop.create_task(self._get_rate())

    async def update_state(self):
        while True:
            start_position = self._state["position"]
            while True:
                prompt, alarm, out = await self._write("DIS")
                # prompt
                if prompt == "I":  # infusing
                    self._busy = True
                elif prompt == "W":  # withdrawing
                    self._busy = True
                elif prompt == "S":  # stopped
                    self._busy = False
                elif prompt == "P":  # paused
                    self._busy = False
                elif prompt == "X":  # purging
                    self._busy = True
                else:
                    self._busy = True
                # get current position
                if not alarm:
                    try:
                        infused = float(out[out.find("I") + 1 : out.find("W")])
                        withdrawn = float(out[out.find("W") + 1 : out.find("ML")])
                        self._state["position"] = start_position - infused + withdrawn
                    except ValueError:
                        pass
                await asyncio.sleep(0.1)
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
            self._ser.flush()
            out = f"{self._config['address']} {command}\r".encode()
            response = await self._ser.awrite_then_readline(out)
            response = response.decode().strip()
            self.logger.debug(f"Recieved response: {response}")
            address = int(response[1:3])
            if response[3] == "A":  # alarm
                prompt = None
                alarm = response[5]
                data = None
            else:
                prompt = response[3]
                alarm = None
                data = response[4:]
            return prompt, alarm, data
        except UnicodeDecodeError:  # try again
            return self._write(command)
