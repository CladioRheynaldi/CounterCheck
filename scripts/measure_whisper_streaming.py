pass
import asyncio
import argparse
import gc
import json
from pathlib import Path
import sys
import time
import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from countercheck_web.engine import WhisperEngine
from countercheck_web.audio import load_audio
from countercheck_web.settings import DATA, REPORTS, RATE, CHECKPOINTS
from countercheck_web.stream import Session


async def measure(engine, sample):
    events=[]
    async def emit(event):
        event["receiver_wall_seconds"] = time.perf_counter()-started
        events.append(event)
    audio=load_audio(ROOT/sample["audio"])
    audio=np.concatenate([audio,np.zeros(int(1.1*RATE),dtype=np.float32)])
    session=Session(engine,emit)
    worker=asyncio.create_task(session.run())
    started=time.perf_counter()
    for offset in range(0,len(audio),1600):
        await asyncio.sleep(max(0,started+offset/RATE-time.perf_counter()))
        session.push(audio[offset:offset+1600])
    await asyncio.sleep(max(0,started+len(audio)/RATE-time.perf_counter()))
    await session.queue.put(None)
    await worker
    measured=dict(id=sample["id"],model=engine.name,dataset=sample["dataset"],
                  actual_wall_seconds=time.perf_counter()-started,audio_seconds=len(audio)/RATE,
                  rtf=session.compute_seconds/(len(audio)/RATE),queue_peak=session.queue_peak,
                  dropped_frames=session.dropped,events=events)
    print(engine.name,sample["id"],f"RTF={measured['rtf']:.3f} dropped={session.dropped}",flush=True)
    return measured


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--models',nargs='+',choices=list(CHECKPOINTS),default=list(CHECKPOINTS))
    parser.add_argument('--output',type=Path,default=REPORTS/'streaming.json')
    args=parser.parse_args()
    manifest=json.loads((DATA/'manifest.json').read_text())
    tests=[s for s in manifest['samples'] if s['split']=='test' and 1.5<s['duration']<6]
    
    samples=[s for dataset in ('doordash','slurp') for s in [r for r in tests if r['dataset']==dataset][:2]]
    rows=[]
    for name in args.models:
        engine=WhisperEngine(name)
        warm=next(s for s in manifest['samples'] if s['split']=='train')
        engine.transcribe(load_audio(ROOT/warm['audio']),True)
        for sample in samples:
            rows.append(asyncio.run(measure(engine,sample)))
        del engine
        gc.collect()
        torch.cuda.empty_cache()
    summaries=[]
    for name in args.models:
        group=[r for r in rows if r['model']==name]
        events=[event for row in group for event in row['events']]
        first=[event['metrics']['first_text_latency_ms'] for event in events if event['metrics']['first_text_latency_ms'] is not None]
        final=[event['metrics']['speech_end_to_final_proxy_ms'] for event in events if event['metrics']['speech_end_to_final_proxy_ms'] is not None]
        def stats(values):
            return dict(p50_ms=float(np.median(values)),p95_ms=float(np.percentile(values,95))) if values else None
        summaries.append(dict(model=name,clips=len(group),first_text=stats(first),speech_end_to_final=stats(final),
                              processing_rtf=sum(r['rtf']*r['audio_seconds'] for r in group)/sum(r['audio_seconds'] for r in group),
                              dropped_frames=sum(r['dropped_frames'] for r in group),queue_peak=max(r['queue_peak'] for r in group),
                              missing_text_clips=sum(not any(e.get('text') for e in r['events']) for r in group)))
    report=dict(description='Actual wall-clock paced real held-out recordings through the website worker including partials, VAD, preprocessing, word alignment and menu scoring. Server event receiver, not visible UI.',
                latency_definition='From VAD-positive 100ms frame ingress to first text; from last VAD-positive frame ingress to final. Includes queueing but not microphone ADC or browser paint. Energy/VAD boundaries, not hand labels.',
                summaries=summaries,results=rows)
    REPORTS.mkdir(parents=True,exist_ok=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(summaries,indent=2))


if __name__=='__main__':
    main()
