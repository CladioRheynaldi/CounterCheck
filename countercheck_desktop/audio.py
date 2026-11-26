import asyncio
import threading
import time
import numpy as np
from countercheck_web.audio import load_audio
from countercheck_web.settings import RATE
from countercheck_web.stream import Session


class AudioWorker:
    def __init__(self, engine, emit, epoch=0):
        self.engine, self.emit, self.epoch = engine, emit, epoch
        self.stop_event = threading.Event()
        self.loop = self.session = None
        self.thread = None

    def start(self, device=None, path=None):
        self.thread = threading.Thread(target=self._run, args=(device, path), daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def new_order(self, epoch):
        self.epoch = epoch
        if self.loop is not None:
            def reset():
                self.session.new_order()
                self.session.epoch = epoch
            self.loop.call_soon_threadsafe(reset)

    def _run(self, device, path):
        try:
            asyncio.run(self._listen(device, path))
        except Exception as error:
            self.emit(dict(type='error', text=str(error)))
        finally:
            self.loop = None
            self.emit(dict(type='stopped'))

    async def _listen(self, device, path):
        self.loop = asyncio.get_running_loop()
        async def emit(event):
            self.emit(event)
        self.session = Session(self.engine, emit)
        self.session.epoch = self.epoch
        consumer = asyncio.create_task(self.session.run())
        try:
            if path:
                audio = load_audio(path)
                audio = np.concatenate([audio, np.zeros(RATE, dtype=np.float32)])
                for offset in range(0, len(audio), 1600):
                    if self.stop_event.is_set() or consumer.done():
                        break
                    self._push(audio[offset:offset+1600].copy())
                    await asyncio.sleep(.1)
            else:
                import sounddevice as sd
                from scipy.signal import resample_poly
                from math import gcd
                try:
                    sd.check_input_settings(device=device, channels=1, dtype='float32', samplerate=RATE)
                    rate = RATE
                except sd.PortAudioError:
                    rate = int(sd.query_devices(device, 'input')['default_samplerate'])
                factor = gcd(rate, RATE)
                def callback(data, frames, timing, status):
                    if status:
                        self.emit(dict(type='audio_loss', text=f'Microphone dropped audio: {status}. Start a new order and repeat.'))
                    audio = data[:, 0].copy()
                    if rate != RATE:
                        audio = resample_poly(audio, RATE//factor, rate//factor).astype(np.float32)
                    self.loop.call_soon_threadsafe(self._push, audio)
                with sd.InputStream(device=device, samplerate=rate, channels=1, dtype='float32',
                                    blocksize=int(rate*.1), callback=callback):
                    self.emit(dict(type='capture_started', sample_rate=rate))
                    while not self.stop_event.is_set() and not consumer.done():
                        await asyncio.sleep(.05)
            if consumer.done():
                await consumer
                return
            await self.session.queue.put(None)
            await consumer
        finally:
            if not consumer.done():
                consumer.cancel()
                try:
                    await consumer
                except asyncio.CancelledError:
                    pass

    def _push(self, audio):
        if not self.session.push(audio, time.perf_counter()*1000):
            self.emit(dict(type='audio_loss', text='Audio processing fell behind. Start a new order and repeat.'))
