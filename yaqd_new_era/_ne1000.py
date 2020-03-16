__all__ = ["NE1000"]

import asyncio
from typing import Dict, Any
import serial

from yaqd_core import ContinuousHardware, logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class NE1000(ContinuousHardware):
    _kind = "ne1000"
    defaults: Dict[str, Any] = {}

    def __init__(self, name, config, config_filepath):
        super().__init__(name, config, config_filepath)
        self.serial_port = config["serial_port"]
        self.baud_rate = config["baud_rate"]
        self.config_limits = config["limits"]
        self.diameter = config["diameter"]
        self.ser = serial.Serial(self.serial_port, self.baud_rate)

    def get_state(self):
        state = super().get_state()
        state["value"] = self.value
        return state

    def _set_position(self, position):
        ...

    async def update_state(self):
        """Continually monitor and update the current daemon state."""
        # If there is no state to monitor continuously, delete this function
        while True:
            # Perform any updates to internal state
            self._busy = False
            # There must be at least one `await` in this loop
            # This one waits for something to trigger the "busy" state
            # (Setting `self._busy = True)
            # Otherwise, you can simply `await asyncio.sleep(0.01)`
            await self._busy_sig.wait()
