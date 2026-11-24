pass
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import re
from pathlib import Path
import sys
import tarfile
import time
import urllib.request
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from countercheck_web.settings import DATA


def fetch(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return target
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=45) as response, target.with_suffix(target.suffix + ".part").open("wb") as out:
                while chunk := response.read(65536):
                    out.write(chunk)
            target.with_suffix(target.suffix + ".part").replace(target)
            return target
        except Exception:
            if attempt == 2:
                raise
    return target


def partition(group):
    value = int(hashlib.sha256(group.encode()).hexdigest(), 16) % 10
    return "train" if value < 8 else "validation" if value == 8 else "test"


def info(path, **fields):
    import soundfile as sf
    audio_info = sf.info(path)
    return dict(audio=str(path.relative_to(ROOT)).replace("\\", "/"),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(), duration=audio_info.duration,
                sample_rate=audio_info.samplerate, **fields)


def doordash(limits):
    source = "https://huggingface.co/datasets/doordash/doordash-audio-benchmarks"
    cache = DATA / "sources/doordash_rows.json"
    if not cache.exists():
        rows = []
        for offset in (0, 100, 200):
            query = urlencode(dict(dataset="doordash/doordash-audio-benchmarks", config="default", split="test", offset=offset, length=100))
            with urllib.request.urlopen("https://datasets-server.huggingface.co/rows?" + query, timeout=90) as response:
                rows += json.load(response)["rows"]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rows), encoding="utf-8")
    rows = json.loads(cache.read_text())
    
    
    old = json.loads((ROOT / "data/context_audio_manifest.json").read_text()) if (ROOT / "data/context_audio_manifest.json").exists() else {"samples": []}
    excluded_tasks = {sample["id"].split("_g")[0] for sample in old["samples"]}
    buckets = {split: [] for split in limits}
    for env in rows:
        row = env["row"]
        if row["task_id"] in excluded_tasks:
            continue
        split = partition("doordash:" + row["task_id"])
        for clip in row["clips"]:
            if (clip.get("clip_source") != "manifest" or clip.get("clip_kind") == "tts_filler" or
                clip.get("timing_unreliable") or not clip.get("transcript", "").strip()):
                continue
            buckets[split].append((row, clip))
    for split in buckets:
        buckets[split].sort(key=lambda pair: hashlib.sha256((pair[0]["task_id"] + pair[1]["clip_id"]).encode()).hexdigest())
        buckets[split] = buckets[split][:limits[split]]
    failures = []
    def download(pair):
        row, clip = pair
        uid = row["task_id"] + "_" + clip["clip_id"]
        try:
            path = fetch(clip["audio"][0]["src"], DATA / "audio/doordash" / (uid + ".wav"))
            sample = info(path, id=uid, reference=clip["transcript"].strip(), dataset="doordash", source=source,
                          group="doordash:" + row["task_id"], speaker=None, split=partition("doordash:" + row["task_id"]),
                          restaurant=row["restaurant"], clip_kind=clip["clip_kind"], original_split="test")
            if not .25 <= sample["duration"] <= 25:
                raise ValueError("Duration outside .25–25 seconds")
            return sample
        except Exception as error:
            failures.append(dict(id=uid, reason=type(error).__name__, message=str(error)[:160]))
            return None
    selected = [pair for bucket in buckets.values() for pair in bucket]
    with ThreadPoolExecutor(max_workers=6) as pool:
        samples = []
        for index, sample in enumerate(pool.map(download, selected)):
            if sample:
                samples.append(sample)
            if index % 20 == 0:
                print(f"DoorDash {index+1}/{len(selected)}, usable={len(samples)}", flush=True)
    
    
    for old_sample in old["samples"]:
        path = ROOT / old_sample["audio"]
        if path.exists():
            task = old_sample["id"].split("_g")[0]
            samples.append(info(path, id=old_sample["id"], reference=old_sample["reference"], dataset="doordash",
                                source=source, group="doordash:" + task, speaker=None, split="train",
                                restaurant=old_sample["restaurant"], clip_kind=old_sample["clip_kind"], original_split="test",
                                selection_note="Previously used coffee-development example; training only, excluded from new validation/test"))
    fetch(source + "/resolve/main/LICENSE", DATA / "sources/DoorDash_LICENSE.txt")
    return samples, failures


def slurp(limits):
    source = "https://github.com/pswietojanski/slurp"
    base = "https://raw.githubusercontent.com/pswietojanski/slurp/master/"
    metadata = json.loads(fetch(base + "dataset/slurp/metadata.json", DATA / "sources/slurp_metadata.json").read_text())
    lookup = {}
    for split in ("train", "devel", "test"):
        path = fetch(base + f"dataset/slurp/{split}.jsonl", DATA / f"sources/slurp_{split}.jsonl")
        for line in path.read_text().splitlines():
            row = json.loads(line)
            for recording in row["recordings"]:
                name = recording["file"]
                details = metadata.get(str(row["slurp_id"]), {}).get("recordings", {}).get(name, {})
                speaker = details.get("usrid")
                if not speaker:
                    continue
                lookup[name] = dict(reference=row["sentence"], speaker=speaker,
                                    group="slurp:" + speaker, split=partition("slurp:" + speaker),
                                    scenario=row["scenario"], original_split=split,
                                    native_english=True if re.match(r"^[FM]E-", speaker) else False if re.match(r"^[FM]O-", speaker) else None,
                                    microphone="close" if "headset" in name else "distant")
    fetch(base + "LICENSE.txt", DATA / "sources/SLURP_LICENSE.txt")
    counts = {split: 0 for split in limits}
    samples, paired = [], set()
    
    
    url = "https://zenodo.org/records/4274930/files/slurp_real.tar.gz?download=1"
    with urllib.request.urlopen(url, timeout=90) as response, tarfile.open(fileobj=response, mode="r|gz") as archive:
        for member in archive:
            name = Path(member.name).name
            row = lookup.get(name)
            if not member.isfile() or not row or counts[row["split"]] >= limits[row["split"]]:
                continue
            pair_id = name.replace("-headset", "")
            if pair_id in paired:
                continue
            stream = archive.extractfile(member)
            if stream is None:
                continue
            target = DATA / "audio/slurp" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            content = stream.read()
            import soundfile as sf
            details = sf.info(io.BytesIO(content))
            if not .5 <= details.duration <= 15:
                continue
            target.write_bytes(content)
            sample = info(target, id=name, dataset="slurp", source=source, **row)
            samples.append(sample)
            paired.add(pair_id)
            counts[row["split"]] += 1
            if len(samples) % 20 == 0:
                print(f"SLURP streamed real audio: {counts}", flush=True)
            if all(counts[key] >= limits[key] for key in limits):
                break
    return samples


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", type=int, default=96)
    parser.add_argument("--validation", type=int, default=16)
    parser.add_argument("--test", type=int, default=24)
    parser.add_argument("--refresh", action="store_true", help="Rebuild the dataset; existing split manifest is otherwise preserved")
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    if (DATA / "manifest.json").exists() and not args.refresh:
        print("Using the existing fixed human-only dataset manifest. Use --refresh to rebuild.", flush=True)
        return
    limits = dict(train=args.train, validation=args.validation, test=args.test)
    dd, failures = doordash(limits)
    interim = dict(samples=dd, download_failures=failures)
    (DATA / "doordash_manifest.json").write_text(json.dumps(interim, indent=2), encoding="utf-8")
    sl = slurp(limits)
    samples = dd + sl
    manifest = dict(description="Pilot human-only paired audio/transcript dataset; no TTS or dummy data", samples=samples,
                    split_rule="SHA256(dataset + task for DoorDash, dataset + anonymous provided speaker ID for SLURP) mod 10: train 0–7, validation 8, test 9. Earlier coffee-development tasks are excluded from new random buckets; their existing clips are assigned only to train.",
                    limitations=["DoorDash speaker IDs unavailable: task-disjoint, not proven speaker-disjoint.",
                                 "SLURP speaker-disjoint subset, one recording per headset/distant pair.",
                                 "Subset selection before inference; not a complete benchmark; limited cafe-menu coverage.",
                                 "DoorDash original benchmark test split repurposed into custom research splits; do not compare to its official benchmark scores."],
                    download_failures=failures)
    (DATA / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Dataset ready", {split: sum(s["split"] == split for s in samples) for split in limits}, flush=True)


if __name__ == "__main__":
    main()
