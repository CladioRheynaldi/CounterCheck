pass
import argparse
from datetime import datetime, timezone
import gc
import hashlib
import json
from pathlib import Path
import platform
import sys
import time
import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from countercheck_web.audio import load_audio, preprocess, overlap
from countercheck_web.engine import WhisperEngine
from countercheck_web.metrics import errors, normalize
from countercheck_web.settings import DATA, REPORTS, RATE, CHECKPOINTS, MENU


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", choices=list(CHECKPOINTS), default=list(CHECKPOINTS))
    parser.add_argument("--split", choices=["validation", "test"], default="test")
    args = parser.parse_args()
    manifest_path = DATA / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    samples = [row for row in manifest["samples"] if row["split"] == args.split]
    audio = [preprocess(load_audio(ROOT / row["audio"])) for row in samples]
    rows = []
    REPORTS.mkdir(parents=True, exist_ok=True)
    existing = REPORTS / "evaluation.json"
    if existing.exists():
        old = json.loads(existing.read_text())
        if old.get("manifest_sha256") == hashlib.sha256(manifest_path.read_bytes()).hexdigest():
            rows = [row for row in old["results"] if row["model"] not in args.models]
    for name in args.models:
        engine = WhisperEngine(name)
        
        warm = next(row for row in manifest["samples"] if row["split"] == "train")
        engine.transcribe(preprocess(load_audio(ROOT / warm["audio"])), timestamps=True)
        for condition in ("original", "overlap_10db", "bandwidth_8khz"):
            for index, sample in enumerate(samples):
                signal = audio[index]
                if condition == "overlap_10db":
                    other = (index+1) % len(samples)
                    while samples[other]["group"] == sample["group"]:
                        other = (other+1) % len(samples)
                    signal = overlap(signal, audio[other], snr=10)
                elif condition == "bandwidth_8khz":
                    from scipy.signal import resample_poly
                    signal = resample_poly(resample_poly(signal, 1, 2), 2, 1)[:len(signal)].astype(np.float32)
                result = engine.transcribe(signal, timestamps=True)
                timing = result["processing_seconds"]
                measured = errors(sample["reference"], result["text"])
                row = dict(id=sample["id"], model=name, condition=condition, dataset=sample["dataset"],
                           group=sample["group"], speaker=sample.get("speaker"), native_english=sample.get("native_english"),
                           reference=sample["reference"], hypothesis=result["text"], words=result["words"],
                           audio_seconds=len(signal)/RATE, processing_seconds=timing, **measured)
                rows.append(row)
                print(f"{name} {condition} {index+1}/{len(samples)} WER={measured['wer']:.1%} RTF={timing/(len(signal)/RATE):.3f}", flush=True)
        del engine
        gc.collect()
        torch.cuda.empty_cache()
        save(rows, manifest, manifest_path, args.split)


def save(rows, manifest, manifest_path, split):
    summaries = []
    for name in CHECKPOINTS:
        for condition in ("original", "overlap_10db", "bandwidth_8khz"):
            group = [row for row in rows if row["model"] == name and row["condition"] == condition]
            if not group:
                continue
            seconds = sum(row["audio_seconds"] for row in group)
            processing = sum(row["processing_seconds"] for row in group)
            summaries.append(dict(model=name, condition=condition, samples=len(group), audio_seconds=seconds,
                                  word_errors=sum(row["word_errors"] for row in group),
                                  reference_words=sum(row["reference_words"] for row in group),
                                  wer=sum(row["word_errors"] for row in group)/sum(row["reference_words"] for row in group),
                                  cer=sum(row["character_errors"] for row in group)/sum(row["reference_characters"] for row in group),
                                  rtf=processing/seconds, throughput=seconds/processing,
                                  median_processing_ms=float(np.median([row["processing_seconds"]*1000 for row in group])),
                                  p95_processing_ms=float(np.percentile([row["processing_seconds"]*1000 for row in group],95))))
    report = dict(created_at=datetime.now(timezone.utc).isoformat(), available=True, split=split,
                  manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                  design="Fixed baseline Whisper base.en; M1 full fine-tune original human recordings; M2 same training budget, 50% real 15dB interfering training speech. All evaluated on identical held-out recordings.",
                  interpretation="Small pilot, not complete cafe validation. Lower WER/CER/RTF is better. Original audio is not guaranteed clean. Controlled overlap and bandwidth conditions are simulated using genuine recordings. Scores are raw ASR before menu inference.",
                  environment=dict(python=platform.python_version(), torch=torch.__version__, cuda=torch.version.cuda,
                                   device=torch.cuda.get_device_name() if torch.cuda.is_available() else "CPU"),
                  dataset_limits=manifest["limitations"], summaries=summaries, results=rows,
                  limitations=["Offline file processing times are not microphone-to-visible-text latency; separate paced/browser tests measure event/UI proxies.",
                               "No supplied speaker identity for DoorDash, therefore only task-disjoint evaluation can be asserted there.",
                               "SLURP native-language metadata permits native/non-native grouping, not specific accent labeling.",
                               "No labels to establish actual fast speech, named-entity accuracy, or physically recorded cafe noise effects."])
    (REPORTS / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = ["# Whisper checkpoint comparison", "", report["design"], "", report["interpretation"], "",
             "WER = (S + D + I) / N_words. CER = (S + D + I) / N_characters; lowercase alphanumeric/apostrophe text, character spaces excluded. Corpus totals pooled, not average per-clip WER.",
             "RTF = preprocessing/inference seconds / audio seconds. Throughput = audio seconds / processing seconds. This run preprocesses signals before timing, so these table RTFs measure inference/alignment only; streaming metrics include preprocessing/grounding.", "",
             "| Model | Condition | Clips | WER | CER | RTF | Audio s / compute s | P50 processing ms | P95 processing ms |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for value in summaries:
        lines.append(f"| {value['model']} | {value['condition']} | {value['samples']} | {value['wer']:.2%} | {value['cer']:.2%} | {value['rtf']:.3f} | {value['throughput']:.2f} | {value['median_processing_ms']:.0f} | {value['p95_processing_ms']:.0f} |")
    lines += ["", "## Error patterns supported by references", ""]
    for name in CHECKPOINTS:
        selected = [row for row in rows if row["model"] == name and row["condition"] == "original"]
        if not selected:
            continue
        lines += [f"### {name}", ""]
        for row in sorted(selected, key=lambda row: row["word_errors"], reverse=True)[:6]:
            lines += [f"- Reference: {row['reference']}", f"  Hypothesis: {row['hypothesis']} ({row['word_errors']} errors)."]
        menu_rows = [row for row in selected if any(word in normalize(row["reference"]).split() for word in " ".join(MENU["drink"] + MENU["milk"] + MENU["size"] + MENU["temperature"]).split())]
        lines += ["", f"Menu-term-containing reference clips: {len(menu_rows)}. No supplied human slot labels, so these examples do not establish exact order accuracy."]
        for label, members in (("SLURP native English", [row for row in selected if row["native_english"] is True]),
                               ("SLURP non-native English", [row for row in selected if row["native_english"] is False])):
            if members:
                wer=sum(row["word_errors"] for row in members)/sum(row["reference_words"] for row in members)
                lines += [f"{label}: {len(members)} clips; WER={wer:.2%}. Tiny metadata-defined group; not a specific accent or balanced demographic comparison."]
        values = [value for value in summaries if value["model"] == name]
        lines += ["", f"Measured strength: best condition WER is {min(v['wer'] for v in values):.2%}; measured weakness: worst condition WER is {max(v['wer'] for v in values):.2%}. These are relative condition results, not guarantees on unseen cafe speakers.", ""]
    lines += ["## Limits", ""] + ["- "+item for item in report["limitations"]+manifest["limitations"]]
    (REPORTS / "EVALUATION.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()
