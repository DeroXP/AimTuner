"""Tiny synthesized feedback sounds through winsound.

PlaySound can take a few milliseconds to return, which is a dropped frame at
240 Hz, so it runs on a worker thread and the game only ever enqueues a name.
The WAVs are generated into assets/sfx, and again whenever their recipe changes.
"""
from __future__ import annotations

import math
import os
import queue
import struct
import threading
import wave

try:
    import winsound
except ImportError:      # not on Windows
    winsound = None

_RATE = 44100
# name: (start Hz, end Hz, milliseconds, volume)
_SOUNDS = {
    "hit": (1250, 1650, 45, 0.50),
    "kill": (900, 1900, 80, 0.55),
    "miss": (210, 150, 55, 0.40),
    "tick": (1500, 1500, 22, 0.28),
    "count": (660, 660, 70, 0.40),
    "go": (990, 1320, 150, 0.50),
    "done": (520, 1040, 260, 0.45),
}


# The warning a dummy that shoots back makes as it appears. Everything above is a single
# clean blip; this is two falling, slightly buzzy notes so it cannot be taken for a hit
# or a kill, and it is stereo: louder in the ear on the side the dummy appeared.
_THREAT_NOTES = ((784.0, 75), (0.0, 30), (587.0, 110))     # (Hz, ms); 0 Hz = a gap
_THREAT_VOL = 0.80          # peaks a little above the hit sounds: it is the one you must not miss
_THREAT_PAN = {"threat_l": (1.0, 0.16), "threat_c": (0.72, 0.72), "threat_r": (0.16, 1.0)}     # left, right gain

# The files are only written when missing or when anything above has changed since they were.
_STAMP = repr((_RATE, sorted(_SOUNDS.items()), _THREAT_NOTES, _THREAT_VOL, sorted(_THREAT_PAN.items())))


def _write(path, f0, f1, ms, vol):
    n = int(_RATE * ms / 1000)
    phase, frames = 0.0, bytearray()
    for i in range(n):
        k = i / n
        phase += 2 * math.pi * (f0 + (f1 - f0) * k) / _RATE
        env = min(i / (_RATE * 0.002), 1.0) * (1.0 - k) ** 1.6     # 2 ms attack, smooth decay
        frames += struct.pack("<h", int(32767 * vol * env * math.sin(phase)))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        w.writeframes(bytes(frames))


def _write_threat(path, left, right):
    frames = bytearray()
    for hz, ms in _THREAT_NOTES:
        n = int(_RATE * ms / 1000)
        for i in range(n):
            if hz <= 0.0:
                frames += struct.pack("<hh", 0, 0)
                continue
            k, a = i / n, 2 * math.pi * hz * i / _RATE
            # odd harmonics give the buzz; 3 ms attack and a tail that fades out without a click
            tone = (math.sin(a) + 0.45 * math.sin(3 * a) + 0.22 * math.sin(5 * a)) / 1.45
            env = min(i / (_RATE * 0.003), 1.0) * (1.0 - k) ** 0.6
            v = 32767 * _THREAT_VOL * env * tone
            frames += struct.pack("<hh", int(v * left), int(v * right))
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        w.writeframes(bytes(frames))


class Sound:
    def __init__(self, root: str, enabled: bool = True):
        self.enabled = enabled and winsound is not None
        self.paths: dict = {}
        self._q: queue.Queue = queue.Queue(maxsize=4)
        if winsound is None:
            return
        folder = os.path.join(root, "assets", "sfx")
        try:
            os.makedirs(folder, exist_ok=True)
            stamp = os.path.join(folder, "made-from.txt")
            try:
                fresh = open(stamp, encoding="utf-8").read() == _STAMP
            except OSError:
                fresh = False
            for name, spec in _SOUNDS.items():
                p = os.path.join(folder, name + ".wav")
                if not (fresh and os.path.exists(p)):
                    _write(p, *spec)
                self.paths[name] = p
            for name, (left, right) in _THREAT_PAN.items():
                p = os.path.join(folder, name + ".wav")
                if not (fresh and os.path.exists(p)):
                    _write_threat(p, left, right)
                self.paths[name] = p
            if not fresh:
                with open(stamp, "w", encoding="utf-8") as f:
                    f.write(_STAMP)
        except OSError:
            self.enabled = False
            return
        threading.Thread(target=self._run, name="sfx", daemon=True).start()

    def _run(self):
        flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
        while True:
            name = self._q.get()
            try:
                winsound.PlaySound(self.paths[name], flags)
            except (RuntimeError, KeyError):
                pass

    def play(self, name: str):
        if self.enabled:
            try:
                self._q.put_nowait(name)
            except queue.Full:
                pass
