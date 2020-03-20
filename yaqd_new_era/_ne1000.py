__all__ = ["NE1000"]

import asyncio
from typing import Dict, Any
import serial  # type: ignore

from yaqd_core import ContinuousHardware, logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class NE1000(ContinuousHardware):
    _kind = "ne1000"
    traits = ["uses-uart", "uses-serial"]
    defaults: Dict[str, Any] = {}  # of config
    defaults["baud_rate"] = 19200
    defaults["address"] = 0
    defaults["limits"] = [0, 1e3]
    defaults["units"] = "ML"
    defaults["rate_units"] = "MM"
    defaults["low_noise"] = True

    def __init__(self, name, config, config_filepath):
        super().__init__(name, config, config_filepath)
        self.serial_port = config["serial_port"]
        self.baud_rate = config["baud_rate"]
        self.config_limits = config["limits"]
        self.diameter = config["diameter"]
        self.address = config["address"]
        self.limits = config["limits"]
        self._units = config["units"]
        self._rate_units = config["rate_units"]
        self._purging = False
        self._previous_infused = 0
        self._previous_withdrawn = 0
        self.ser = serial.Serial(port=self.serial_port, baudrate=self.baud_rate, timeout=1)
        # get rate from pump
        prompt, alarm, data = self._write("RAT")
        self._rate = float(data[:-3])
        # low noise mode
        self._write(f"LN {int(config['low_noise'])}")

    def close(self):
        self.ser.close()

    def direct_serial_write(self, message):
        self.ser.write(message.encode())

    def get_state(self):
        state = super().get_state()
        state["rate"] = self._rate
        return state

    def prime(self):
        self._busy = True
        self._loop.create_task(self._prime())

    async def _prime(self):
        # prime
        self.ser.write(f"{self.address} DIR WDR\r\n".encode())
        self.ser.write(f"{self.address} PUR\r\n".encode())

    def purge(self):
        self._busy = True
        self._loop.create_task(self._purge())

    async def _purge(self):
        # purge
        self._purging = True
        self._write("DIR INF")
        self._write("PUR")

    def _set_position(self, position):
        amount_from_here = position - self._position
        if abs(amount_from_here) < 0.001:
            return
        if amount_from_here > 0:
            self._write("DIR WDR")
        elif amount_from_here < 0:
            self._write("DIR INF")
        self._write(f"VOL {self._units}")
        # amount needs special formatting
        if abs(amount_from_here) > 10000:
            raise Exception
        vol = str(abs(amount_from_here))[:5]
        self._write(f"VOL {vol}")  # TODO: truncate charachters
        self._write(f"RUN")

    def set_rate(self, rate):
        self._rate = rate
        self._write(f"RAT C {self._rate} {self._rate_units}")

    async def update_state(self):
        while True:
            # prompt
            prompt, alarm, out = self._write("DIS")
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
            # purging: once done, position goes to 0
            if self._purging and alarm == "S":
                self._purging = False
                self._position = 0
            # get current position
            if not alarm:
                infused = float(out[out.find("I") + 1 : out.find("W")])
                withdrawn = float(out[out.find("W") + 1 : out.find("ML")])
                self._position -= infused - self._previous_infused
                self._position += withdrawn - self._previous_withdrawn
                self._previous_infused = infused
                self._previous_withdrawn = withdrawn
                # wait
                if self._busy:
                    await asyncio.sleep(0.1)
                else:
                    self._write(f"CLD INF")
                    self._previous_infused = 0
                    self._write(f"CLD WDR")
                    self._previous_withdrawn = 0
                    await asyncio.sleep(1)
            else:
                await asyncio.sleep(0)

    def _write(self, command):
        try:
            self.ser.flush()
            out = f"{self.address} {command}\r\n"
            self.direct_serial_write(out)
            _in = self.ser.read_until(b"\x03").decode().strip()
            address = int(_in[1:3])
            if _in[3] == "A":
                prompt = None
                alarm = _in[5]
                data = None
            else:
                prompt = _in[3]
                alarm = None
                data = _in[4:]
            return prompt, alarm, data
        except UnicodeDecodeError:
            return self._write(command)
