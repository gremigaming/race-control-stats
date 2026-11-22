"""Makes the channel point sound effects (our own, no copyrighted audio).

python3 channel_points/make_sounds.py <out dir>  ->  radio.wav, box.wav, siren.wav, ding.wav, fanfare.wav
"""
import math
import random
import struct
import sys
import wave
from pathlib import Path

RATE = 44100


def save(path, samples):
    peak = max(1e-9, max(abs(s) for s in samples))
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s / peak * 0.8)) * 32767)) for s in samples))


def tone(freq, secs, vol=1.0, wave_kind="sine"):
    out = []
    n = int(RATE * secs)
    for i in range(n):
        t = i / RATE
        ph = 2 * math.pi * (freq(t) if callable(freq) else freq) * t
        s = math.sin(ph) if wave_kind == "sine" else (1 if math.sin(ph) > 0 else -1) * 0.6
        env = min(1, i / (RATE * 0.005), (n - i) / (RATE * 0.01))
        out.append(s * vol * env)
    return out


def silence(secs):
    return [0.0] * int(RATE * secs)


def radio():
    """Team radio squelch: short noise burst and a click beep."""
    rnd = random.Random(7)
    noise = [rnd.uniform(-1, 1) * 0.35 * (1 - i / (RATE * 0.12)) for i in range(int(RATE * 0.12))]
    return noise + tone(1400, 0.07, 0.8) + silence(0.03) + tone(1400, 0.07, 0.8)


def box():
    return (tone(880, 0.12, wave_kind="square") + silence(0.06) + tone(880, 0.12, wave_kind="square")
            + silence(0.18)) * 2


def siren():
    """Two-tone safety car siren, about 3 seconds."""
    out = []
    for _ in range(4):
        out += tone(660, 0.38, 0.9) + tone(880, 0.38, 0.9)
    return out


def ding():
    n = int(RATE * 1.2)
    return [(math.sin(2 * math.pi * 1320 * i / RATE) + 0.4 * math.sin(2 * math.pi * 2640 * i / RATE))
            * math.exp(-4 * i / RATE) for i in range(n)]


def fanfare():
    notes = [(523, 0.14), (659, 0.14), (784, 0.14), (1047, 0.45)]
    out = []
    for f, d in notes:
        out += tone(f, d, 0.9, "square") + silence(0.02)
    return out


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for name, make in (("radio", radio), ("box", box), ("siren", siren), ("ding", ding), ("fanfare", fanfare)):
        save(out / f"{name}.wav", make())
        print("wrote", out / f"{name}.wav")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sounds")
