pass
import asyncio
import time
import numpy as np
import webrtcvad
from .audio import preprocess
from .orders import ground
from .settings import RATE

ENDPOINT_SECONDS=.4
PARTIAL_SECONDS=.6


class Session:
    def __init__(self, engine, emit):
        self.engine, self.emit = engine, emit
        self.queue = asyncio.Queue(maxsize=80)  
        self.vad = webrtcvad.Vad(1)
        self.buffer = []
        self.buffer_size = 0
        self.voice_samples = 0
        self.silence = 0
        self.last_partial = 0
        self.audio_received = self.audio_processed = 0.0
        self.segment_start = 0.0
        self.epoch = 0
        self.phrases = []
        self.raw_phrases = []
        self.dropped = 0
        self.queue_peak = 0
        self.compute_seconds = 0.0
        self.first_partial_at = None
        self.voice_started_at = None
        self.last_voice_received_at = None
        self.first_voice_client_ms = None
        self.order_overflow = False
        self.worker = None

    def push(self, audio, client_ms=None):
        self.audio_received += len(audio) / RATE
        try:
            self.queue.put_nowait((audio, time.perf_counter(), self.epoch, client_ms))
            self.queue_peak = max(self.queue_peak, self.queue.qsize())
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            return False

    def new_order(self):
        self.epoch += 1
        self.phrases.clear()
        self.raw_phrases.clear()
        self.reset_segment()
        self.dropped = 0
        self.order_overflow = False

    def reset_segment(self):
        self.buffer.clear()
        self.buffer_size = self.voice_samples = self.silence = self.last_partial = 0
        self.segment_start = self.audio_processed
        self.voice_started_at = self.first_partial_at = None
        self.last_voice_received_at = None
        self.first_voice_client_ms = None

    async def run(self):
        while True:
            entry = await self.queue.get()
            try:
                if entry is None:
                    await self.decode(final=True)
                    return
                audio, captured, epoch, client_ms = entry
                self.audio_processed += len(audio)/RATE
                if epoch != self.epoch:
                    continue
                pcm = (np.clip(audio, -1, 1)*32767).astype("<i2").tobytes()
                voiced = any(self.vad.is_speech(pcm[i:i+640], RATE) for i in range(0, len(pcm)-639, 640))
                if not self.buffer:
                    self.segment_start = self.audio_processed - len(audio)/RATE
                    if not voiced:
                        continue
                self.buffer.append(audio)
                self.buffer_size += len(audio)
                if voiced:
                    self.voice_samples += len(audio)
                    self.silence = 0
                    if self.voice_started_at is None:
                        self.voice_started_at = captured
                        self.first_voice_client_ms = client_ms
                    self.last_voice_received_at = captured
                else:
                    self.silence += len(audio)
                final = self.silence >= int(ENDPOINT_SECONDS*RATE) or self.buffer_size >= 12*RATE
                if final:
                    await self.decode(final=True)
                elif self.voice_samples >= int(.3*RATE) and self.buffer_size-self.last_partial >= int(PARTIAL_SECONDS*RATE):
                    self.last_partial = self.buffer_size
                    await self.decode(final=False)
            finally:
                self.queue.task_done()

    async def decode(self, final):
        if not self.buffer_size or self.voice_samples < RATE*.1:
            if final:
                self.reset_segment()
            return
        epoch = self.epoch
        segment_start = self.segment_start
        audio = preprocess(np.concatenate(self.buffer))
        started = time.perf_counter()
        result = await asyncio.to_thread(self.engine.transcribe, audio, final)
        words = [dict(word, start=word["start"]+segment_start, end=word["end"]+segment_start) for word in result["words"]]
        interpretation = None
        if final and result["text"]:
            interpretation = await asyncio.to_thread(ground, self.engine, audio, result["text"], result["words"])
        completed = time.perf_counter()
        self.compute_seconds += completed-started
        if epoch != self.epoch:
            return  
        first = None
        if result["text"] and self.first_partial_at is None:
            self.first_partial_at = completed
            first = completed-self.voice_started_at if self.voice_started_at else None
        event = dict(type="final" if final else "partial", text=result["text"], words=words,
                     audio_end=self.audio_processed, epoch=epoch,
                     metrics=dict(inference_response_ms=1000*(completed-started),
                                  first_text_latency_ms=None if first is None else first*1000,
                                  first_voice_client_ms=self.first_voice_client_ms if first is not None else None,
                                  final_processing_ms=1000*(completed-started) if final else None,
                                  speech_end_to_final_proxy_ms=1000*(completed-self.last_voice_received_at) if final and self.last_voice_received_at is not None else None,
                                  silence_endpoint_threshold_ms=ENDPOINT_SECONDS*1000,
                                  rtf=self.compute_seconds/max(self.audio_processed, .001),
                                  queue=self.queue.qsize(), queue_peak=self.queue_peak, dropped_frames=self.dropped))
        if interpretation:
            self.phrases.append(interpretation["interpreted_text"])
            self.raw_phrases.append(result["text"])
            if len(self.phrases)>100:
                self.order_overflow=True
                self.phrases=self.phrases[-100:]
                self.raw_phrases=self.raw_phrases[-100:]
            from .orders import draft
            event["order"] = draft(" ".join(self.phrases))
            event["order"]["menu_decisions"] = interpretation["menu_decisions"]
            event["order"]["warnings"] += [warning for warning in interpretation["warnings"] if warning not in event["order"]["warnings"]]
            event["order_overflow"] = self.order_overflow
            if self.order_overflow:
                event["order"]["warnings"].append("Order exceeded 100 phrases. Start a new order; capture stays on. Confirmation is blocked to avoid confirming incomplete history.")
        await self.emit(event)
        if final:
            self.reset_segment()
