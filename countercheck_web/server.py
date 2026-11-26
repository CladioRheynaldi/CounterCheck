pass
import asyncio
from contextlib import asynccontextmanager
import gc
import json
import logging
from pathlib import Path
import time
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from .settings import ROOT, CHECKPOINTS, MENU, REPORTS, DATA
from .stream import Session

STATIC = Path(__file__).parent / "static"
state = dict(engine=None, model="baseline", loading=False, error=None, active=False)


def warm_engine(engine):
    pass
    manifest=DATA/'manifest.json'
    if not manifest.exists():
        return False
    samples=[row for row in json.loads(manifest.read_text())['samples'] if row['split']=='train' and (ROOT/row['audio']).is_file()]
    if not samples:
        return False
    from .audio import load_audio,preprocess
    sample=next((row for row in samples if row['duration']<6),samples[0])
    audio=preprocess(load_audio(ROOT/sample['audio']))
    engine.transcribe(audio,True)
    engine.score_candidates(audio,[row['reference'] for row in samples[:8]])
    return True


async def load_model(name):
    state.update(loading=True, error=None)
    try:
        from .engine import WhisperEngine
        state["engine"] = None
        gc.collect()
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        engine = await asyncio.to_thread(WhisperEngine, name)
        warmed=await asyncio.to_thread(warm_engine,engine)
        state.update(engine=engine, model=name,warmed=warmed)
    except Exception as error:
        state["error"] = str(error)
        logging.exception("Local Whisper loading failed")
    finally:
        state["loading"] = False


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(load_model("baseline"))
    yield
    await task


app = FastAPI(title="CounterCheck local Whisper", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def fresh_assets(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-CounterCheck-Version"] = "2026.11.26"
    return response


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
async def status():
    return dict(ready=state["engine"] is not None and not state["loading"], loading=state["loading"],
                error=state["error"], model=state["model"], active=state["active"],
                device=getattr(state["engine"], "device", None),
                models={name: (path / "config.json").exists() and (name == "baseline" or (path / "training.json").exists()) for name, path in CHECKPOINTS.items()},
                menu=MENU, local_only=True,warmed=state.get('warmed',False), version="2026.11.26")


class ModelChoice(BaseModel):
    name: str


@app.post("/api/model")
async def model(choice: ModelChoice):
    if state["active"] or state["loading"]:
        raise HTTPException(409, "Stop the microphone before changing checkpoints.")
    if choice.name not in CHECKPOINTS or not (CHECKPOINTS[choice.name] / "config.json").exists():
        raise HTTPException(400, "Checkpoint is unavailable. Train the experiment first.")
    state["loading"] = True
    asyncio.create_task(load_model(choice.name))
    return dict(loading=True)


@app.get("/api/report")
async def report():
    path = REPORTS / "evaluation.json"
    return json.loads(path.read_text()) if path.exists() else dict(available=False, message="Measured results appear after evaluation. No scores are pre-filled.")


@app.websocket("/ws/audio")
async def audio_socket(ws: WebSocket):
    origin = ws.headers.get("origin")
    if origin and origin not in {"http://127.0.0.1:8866", "http://localhost:8866"}:
        await ws.close(code=1008)
        return
    await ws.accept()
    if state["active"] or state["loading"] or state["engine"] is None:
        await ws.send_json(dict(type="error", text="Model not ready or another microphone session is active."))
        await ws.close()
        return
    state["active"] = True
    send_lock = asyncio.Lock()
    async def emit(event):
        async with send_lock:
            await ws.send_json(event)
    session = Session(state["engine"], emit)
    worker = asyncio.create_task(session.run())
    client_ms=None
    try:
        await emit(dict(type="started", model=state["model"], sample_rate=16000))
        while not worker.done():
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if "bytes" in message and message["bytes"] is not None:
                payload = message["bytes"]
                if not payload or len(payload) % 4 or len(payload) > 32000:
                    await emit(dict(type="error", text="Expected <=0.5 seconds of mono 16 kHz Float32 PCM."))
                    continue
                audio = np.frombuffer(payload, dtype="<f4").copy()
                if not np.isfinite(audio).all():
                    await emit(dict(type="error", text="Non-finite audio rejected."))
                    continue
                if not session.push(audio, client_ms):
                    await emit(dict(type="audio_loss", text="Audio backlog exceeded the limit. Start a new order and repeat.", dropped=session.dropped))
            elif message.get("text"):
                command = json.loads(message["text"])
                if command.get("type") == "audio_clock":
                    value=command.get("captured_ms")
                    client_ms=float(value) if isinstance(value,(int,float)) and np.isfinite(value) else None
                elif command.get("type") == "new_order":
                    session.new_order()
                    await emit(dict(type="new_order", epoch=session.epoch))
                elif command.get("type") == "stop":
                    await session.queue.put(None)
                    await worker
                    await emit(dict(type="stopped", audio_seconds=session.audio_processed, dropped=session.dropped))
                    break
        if worker.done() and not worker.cancelled() and worker.exception():
            raise worker.exception()
    except WebSocketDisconnect:
        pass
    except Exception as error:
        logging.exception("Audio session failed")
        try:
            await emit(dict(type="error", text=str(error)))
        except Exception:
            pass
    finally:
        worker.cancel()
        try:
            await worker
        except (asyncio.CancelledError, Exception):
            pass
        state["active"] = False
        try:
            await ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8866)
