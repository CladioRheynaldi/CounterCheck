pass
import argparse
import os
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_FLAX", "0")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
import time
import numpy as np
import torch
from transformers import WhisperForConditionalGeneration, WhisperProcessor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from countercheck_web.settings import DATA, BASE, CHECKPOINTS, RATE, REPORTS
from countercheck_web.audio import load_audio, preprocess, overlap


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["clean", "overlap"], required=True)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=2)
    args = parser.parse_args()
    if not torch.cuda.is_available():
        raise RuntimeError("Fine-tuning requires CUDA PyTorch in .venv-whisper; setup_whisper.bat installs it.")
    torch.set_num_threads(4)
    seed = 20261123
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    manifest_path = DATA / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    train = [row for row in manifest["samples"] if row["split"] == "train"]
    if not train:
        raise ValueError("No real human training data; run prepare_whisper_data.py")
    processor = WhisperProcessor.from_pretrained(BASE, local_files_only=True)
    model = WhisperForConditionalGeneration.from_pretrained(BASE, local_files_only=True, attn_implementation="sdpa").cuda()
    model.config.use_cache = False
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-5, weight_decay=.01)
    scaler = torch.amp.GradScaler("cuda")
    audios = [preprocess(load_audio(ROOT / row["audio"])) for row in train]
    records = []
    start = time.perf_counter()
    model.train()
    for epoch in range(args.epochs):
        order = list(range(len(train)))
        random.Random(seed+epoch).shuffle(order)
        losses = []
        for offset in range(0, len(order), args.batch_size):
            indices = order[offset:offset+args.batch_size]
            waveforms = []
            for index in indices:
                audio = audios[index]
                if args.model == "overlap" and (index + epoch) % 2 == 0:
                    
                    other = (index + 17 + epoch) % len(train)
                    while train[other]["group"] == train[index]["group"]:
                        other = (other+1) % len(train)
                    audio = overlap(audio, audios[other], snr=15)
                waveforms.append(audio)
            features = processor.feature_extractor(waveforms, sampling_rate=RATE, return_tensors="pt").input_features.cuda()
            encoded = processor.tokenizer([train[index]["reference"] for index in indices], padding=True, return_tensors="pt")
            labels = encoded.input_ids.masked_fill(encoded.attention_mask.ne(1), -100)
            if (labels[:, 0] == model.config.decoder_start_token_id).all():
                labels = labels[:, 1:]
            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast("cuda", dtype=torch.float16):
                loss = model(input_features=features, labels=labels.cuda()).loss
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1)
            scaler.step(optimizer)
            scaler.update()
            value = float(loss.detach())
            if not np.isfinite(value):
                raise RuntimeError("Non-finite training loss")
            losses.append(value)
            if offset % (args.batch_size * 10) == 0:
                print(f"{args.model} epoch {epoch+1}/{args.epochs} examples {offset}/{len(train)} loss={value:.4f} GPU={torch.cuda.max_memory_allocated()/2**30:.2f}GiB", flush=True)
        records.append(dict(epoch=epoch+1, mean_training_loss=float(np.mean(losses))))
    path = CHECKPOINTS[args.model]
    path.mkdir(parents=True, exist_ok=True)
    model.config.use_cache = True
    model.gradient_checkpointing_disable()
    model.save_pretrained(path, safe_serialization=True)
    processor.save_pretrained(path)
    report = dict(created_at=datetime.now(timezone.utc).isoformat(), model=args.model,
                  baseline="openai/whisper-base.en", method="Full-parameter supervised fine-tuning; identical seed/optimizer/steps across experiments",
                  parameters=sum(p.numel() for p in model.parameters()), seed=seed, epochs=args.epochs,
                  batch_size=args.batch_size, learning_rate=1e-5, samples=len(train),
                  original_audio_seconds=sum(row["duration"] for row in train),
                  train_ids=[row["id"] for row in train], dataset_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                  augmentation="50% clean, 50% another real training voice at 15 dB SIR" if args.model == "overlap" else "Original recordings; shared high-pass preprocessing",
                  validation_usage="Not used for model selection in this fixed two-epoch pilot; available for later threshold calibration.",
                  training_seconds=time.perf_counter()-start, peak_gpu_gib=torch.cuda.max_memory_allocated()/2**30,
                  device=torch.cuda.get_device_name(), history=records,
                  limitations=["Small pilot subset, not final cafe deployment validation.", "No synthetic speech or held-out audio used in training."])
    (path / "training.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"training_{args.model}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved real fine-tuned checkpoint: {path}", flush=True)


if __name__ == "__main__":
    main()
