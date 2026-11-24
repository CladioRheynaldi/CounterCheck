pass
import threading
import hashlib
import time
import numpy as np
import torch
from transformers import WhisperForConditionalGeneration, WhisperProcessor, pipeline
from transformers.modeling_outputs import BaseModelOutput
from .settings import CHECKPOINTS, RATE


class WhisperEngine:
    def __init__(self, checkpoint="baseline"):
        self.name = checkpoint
        path = CHECKPOINTS[checkpoint]
        if not (path / "config.json").exists():
            raise FileNotFoundError(f"Checkpoint {checkpoint} unavailable. Run the setup/training scripts first.")
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.dtype = torch.float16 if self.device == "cuda" else torch.float32
        torch.set_num_threads(4)
        self.processor = WhisperProcessor.from_pretrained(str(path), local_files_only=True)
        self.model = WhisperForConditionalGeneration.from_pretrained(str(path), local_files_only=True,
                    torch_dtype=self.dtype, attn_implementation="eager").to(self.device).eval()
        self.pipe = pipeline("automatic-speech-recognition", model=self.model,
                             tokenizer=self.processor.tokenizer, feature_extractor=self.processor.feature_extractor,
                             device=0 if self.device == "cuda" else -1, torch_dtype=self.dtype)
        self.lock = threading.RLock()
        self._score_cache = None

    def transcribe(self, audio, timestamps=True):
        if len(audio) < RATE * .15:
            return dict(text="", words=[], processing_seconds=0)
        before = time.perf_counter()
        with self.lock, torch.inference_mode():
            result = self.pipe({"raw": audio, "sampling_rate": RATE},
                               return_timestamps="word" if timestamps else False,
                               generate_kwargs=dict(max_new_tokens=112, num_beams=1, do_sample=False))
        words = []
        previous = 0
        duration = len(audio) / RATE
        for chunk in result.get("chunks", []):
            start, end = chunk["timestamp"]
            start = min(duration, max(previous, float(start or previous)))
            end = min(duration, max(start, float(end if end is not None else duration)))
            words.append(dict(word=chunk["text"].strip(), start=start, end=end))
            previous = end
        return dict(text=result["text"].strip(), words=words, processing_seconds=time.perf_counter() - before)

    def score_candidates(self, audio, candidates):
        pass
        with self.lock, torch.inference_mode():
            cache_key=hashlib.blake2b(np.ascontiguousarray(audio).tobytes(),digest_size=16).digest()
            if self._score_cache is None or self._score_cache[0]!=cache_key:
                features = self.processor(audio, sampling_rate=RATE, return_tensors="pt").input_features.to(self.device, self.dtype)
                hidden=self.model.model.encoder(features).last_hidden_state
                self._score_cache=(cache_key,hidden)
            hidden=self._score_cache[1]
            rows = []
            prefix = len(self.processor.tokenizer.prefix_tokens)
            for start in range(0,len(candidates),8):
                batch=candidates[start:start+8]
                encoded=self.processor.tokenizer(batch,padding=True,return_tensors='pt')
                ids=encoded.input_ids.to(self.device)
                attention=encoded.attention_mask.to(self.device)
                encoder=BaseModelOutput(last_hidden_state=hidden.expand(len(batch),-1,-1))
                logits = self.model(encoder_outputs=encoder, decoder_input_ids=ids[:, :-1],decoder_attention_mask=attention[:,:-1], use_cache=False).logits.float()
                logp = logits.log_softmax(-1).gather(-1, ids[:, 1:, None]).squeeze(-1)
                mask=attention[:,1:].bool()
                mask[:,:max(0,prefix-1)]=False
                scores=(logp*mask).sum(-1)/mask.sum(-1).clamp_min(1)
                rows.extend(dict(text=candidate,score=float(score)) for candidate,score in zip(batch,scores))
        return sorted(rows, key=lambda row: row["score"], reverse=True)
