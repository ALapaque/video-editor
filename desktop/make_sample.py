"""Fabrique une courte vidéo de synthèse pour essayer le moteur (utilisé par le flux « Installeurs »)."""

import sys
from fractions import Fraction

import av
import numpy as np

path = sys.argv[1] if len(sys.argv) > 1 else "essai.mp4"
width, height, fps, frames = 1920, 1080, 30, 45
rng = np.random.default_rng(1)
background = np.repeat(np.repeat(rng.integers(40, 200, (height // 8, width // 8, 3), dtype=np.uint8), 8, 0), 8, 1)
with av.open(path, "w") as out:
    video = out.add_stream("libx264", rate=fps)
    video.width, video.height, video.pix_fmt = width, height, "yuv420p"
    audio = out.add_stream("aac", rate=48000)
    audio.layout = "stereo"
    for i in range(frames):
        image = background.copy()
        image[400:520, 300 + 8 * i : 700 + 8 * i] = 255
        frame = av.VideoFrame.from_ndarray(image, format="rgb24")
        frame.pts, frame.time_base = i, Fraction(1, fps)
        for packet in video.encode(frame):
            out.mux(packet)
    t = np.arange(frames * 48000 // fps) / 48000
    wave = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    sound = av.AudioFrame.from_ndarray(np.stack([wave, wave]), format="fltp", layout="stereo")
    sound.rate, sound.pts, sound.time_base = 48000, 0, Fraction(1, 48000)
    for packet in [*audio.encode(sound), *audio.encode(None), *video.encode(None)]:
        out.mux(packet)
print(f"vidéo d'essai écrite : {path}")
