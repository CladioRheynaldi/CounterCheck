from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/whisper"
MODELS = ROOT / "models/whisper"
REPORTS = ROOT / "reports/whisper"
BASE = MODELS / "baseline"
CHECKPOINTS = {"baseline": BASE, "clean": MODELS / "clean", "overlap": MODELS / "overlap"}
RATE = 16000
MENU = {"drink": ["latte", "cappuccino", "americano", "espresso", "mocha", "tea", "hot chocolate"],
        "size": ["small", "medium", "large"], "temperature": ["hot", "iced"],
        "milk": ["oat milk", "whole milk", "skim milk", "soy milk", "almond milk"],
        "extras": ["decaf", "vanilla", "caramel", "no sugar", "extra shot"],
        "quantity": list(range(1, 21))}
