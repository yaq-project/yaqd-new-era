import asyncio
import serial
from yaqd_core import aserial
import time


MAX_ADDRESSES=10

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
        while self.port.is_open:
            if True: 
                data = await self.write_queue.get()
                self.port.write(data)

    def read_dispatch(self):
        self.loop.create_task(self._async_read_dispatch())

    async def _async_read_dispatch(self):
        # This section is not fully configured for an X2 method.  Workers
        # fill an array equal to the total allowed addresses on a single
        # serial line (10).  If a dual syringe pump is installed, it does
        # not respond to an address and must instead be communicated to via
        # "*", but it returns an address of 0 and therefore fills that index.
        # But a "*" call will return values for all other pumps on the line
        # as well. The addressing/arraying method herein was felt to best
        # accomodate these varying serial setups.  
        
        while self.port.is_open:
            if True: #self.closing==False:
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
                        if response[4] == "?":  #error
                            error = response[5:-1] 
                            if error=="":
                                error=None         
                        else:
                            data = response[4:-1]
                            if data=="":
                                data=None
                        self.workers[address]=prompt,alarm,error,data
                except:
                    pass

    def flush(self):
        self.port.flush()

    def close(self):
        for task in self.tasks:
            task.cancel()   # NOTE: the task order may be important (cancel writes before reads)
            time.sleep(1.0) # somewhere around what we would think a serial timeout to be
        self._close()
        self.port.close()

    def _close(self):
        pass

