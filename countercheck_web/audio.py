import numpy as np
from scipy.signal import butter, sosfilt, resample_poly
from math import gcd
import soundfile as sf
from .settings import RATE


def load_audio(path):
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if rate != RATE:
        divisor = gcd(rate, RATE)
        audio = resample_poly(audio, RATE // divisor, rate // divisor)
    return np.asarray(audio, dtype=np.float32)


def preprocess(audio):
    audio = np.nan_to_num(np.asarray(audio, dtype=np.float32), nan=0, posinf=1, neginf=-1)
    filtered = sosfilt(butter(2, 60, btype="highpass", fs=RATE, output="sos"), audio)
    return np.clip(filtered, -1, 1).astype(np.float32)


def overlap(target, other, snr=10):
    pass
    other = np.resize(other, target.shape)
    energy = float(np.sqrt(np.mean(other ** 2)))
    if energy < 1e-7:
        return target.copy()
    gain = float(np.sqrt(np.mean(target ** 2))) / (energy * 10 ** (snr / 20))
    mixed = target + gain * other
    return (mixed / max(1, float(np.max(np.abs(mixed))) / .98)).astype(np.float32)
