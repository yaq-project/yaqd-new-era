import asyncio
import serial
from yaqd_core import aserial

MAX_ADDRESSES = 10


class SerialDispatcher:
    def __init__(self, port, baudrate):
        self.port = aserial.ASerial(
            port,
            baudrate,
            stopbits=serial.STOPBITS_ONE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            eol=b"\x03",
        )
        self.workers = [(None, None, None, None)] * MAX_ADDRESSES
        self.write_queue = asyncio.Queue()
        self.read_queue = asyncio.Queue()
        self.loop = asyncio.get_event_loop()
        self.tasks = [
            self.do_writes(),
            self.read_dispatch(),
        ]

    def write(self, data):
        self.write_queue.put_nowait(data)

    def do_writes(self):
        self.loop.create_task(self._async_do_writes())

    async def _async_do_writes(self):
        while True:
            data = await self.write_queue.get()
            self.port.write(data)
            await asyncio.sleep(0.25)
            # self.write_queue.task_done()

    def read_dispatch(self):
        self.loop.create_task(self._async_read_dispatch())

    async def _async_read_dispatch(self):
        while True:
            line = await self.port.areadline()
            response = line.decode().strip()

            await self.read_queue.put(response)
            try:
                address = int(response[1:3])
                if (address >= 0) and (address <= 9):
                    prompt = alarm = error = data = None
                    if response[3] == "A":  # alarm
                        alarm = response[4:-1]
                    else:
                        prompt = response[3]
                    if response[4] == "?":  # error
                        error = response[5:-1]
                        if error == "":
                            error = None
                    else:
                        data = response[4:-1]
                        if data == "":
                            data = None
                    self.workers[address] = prompt, alarm, error, data
            except:
                pass
            await asyncio.sleep(0.25)

    def flush(self):
        self.port.flush()

    def close(self):
        self.loop.create_task(self._close())

    async def _close(self):
        await self.write_queue.join()
        for worker in self.workers.values():
            await worker.join()
        for task in self.tasks:
            task.cancel()
