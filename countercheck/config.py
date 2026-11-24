from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE_MODEL_PATH = ROOT / "models" / "vosk-model-small-en-us-0.15"
MODEL_PATH = ROOT / "models" / "vosk-model-en-us-0.22-lgraph"
SAMPLE_RATE = 16000
FRAME_SECONDS = 0.2
MENU = ("latte", "cappuccino", "americano", "espresso", "mocha", "tea", "hot chocolate")
SIZES = ("small", "medium", "large")
TEMPERATURES = ("hot", "iced")
MILKS = ("unspecified", "oat milk", "whole milk", "skim milk", "soy milk", "almond milk")
