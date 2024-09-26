import asyncio
import serial
import numpy as np
from ._new_era_x2 import NewEraX2

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


class NewEraContinuousNextGen(NewEraX2):
    _kind = "new-era-continuous-nextgen"

    def __init__(self, name, config, config_filepath):
        super().__init__(name, config, config_filepath)
        self._rate = float(np.nan)
        self._rate_units = ""
        self._purging = False
        self.tasks.append(self._process_x2_data())
        self.get_rate()

    def get_rate(self):
        self._get_rate()
        return self._rate

    def get_rate_units(self):
        self._get_rate()
        return self._rate_units

    def _get_rate(self):
        async def _wait_for_ready_and_get_rate(self):
            strn = f"{self._address}RAT\r"
            await self._serial.write_queue.put(strn.encode())
            if self._busy and not self._homing:
                await self._not_busy_sig.wait()
                self._busy = True

        self._loop.create_task(_wait_for_ready_and_get_rate(self))

    def set_rate_units(self, units):
        assert isinstance(units, str)
        self.logger.info("rate units setter deactivated")
        # self._rate_units=units

    def set_rate(self, rate):
        async def _wait_for_ready_and_set_rate(self, rate):
            if self._state["current_alarm"] == "":
                rate = int(rate)
                await self._serial.write_queue.put(f"{self._address}RAT{rate}\n".encode())
                if self._busy and not self._homing:
                    await self._not_busy_sig.wait()
                    self._busy = True

        self._loop.create_task(_wait_for_ready_and_set_rate(self, rate))

    async def _process_x2_data(self):
        while True:
            prompt, alarm, error, data = self._serial.workers[self._read_address]
            # add data, alarm, error, prompt processing here and not in the parent...

            # I would have to set up a emitter for changes within self._serial.workers
            # if this processing step were to be streamlined...I did not want
            # to cross ports yet.  See serial for reason behind the array of workers
            def process_x2_rate(data):
                units = data[-2:]
                if (units == "UM") or (units == "MM") or (units == "UH") or (units == "MH"):
                    self._rate = float(data[:-3])
                    self._rate_units = units

            if data is not None:
                process_x2_rate(data)
            await asyncio.sleep(0.25)

    # def process_x2_data(self):
    #    self._loop.create_task(self._process_x2_data())
