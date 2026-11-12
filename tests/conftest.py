"""Outils communs aux tests : vidéo de synthèse et faux détecteur."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest

from moto_blur import project as pj

W, H, FPS = 320, 180, 30


def object_box(i: int) -> tuple[int, int, int, int]:
    """Position du carré blanc dans l'image numéro i (il se déplace vers la droite)."""
    x = 20 + 2 * i
    return (x, 60, x + 40, 100)


def make_video(path: Path, frames: int = 90, audio: bool = True, size=(W, H), fps: int = FPS) -> Path:
    """Vidéo H.264 : fond texturé fixe, carré blanc rayé mobile, son sinusoïdal continu."""
    w, h = size
    rng = np.random.default_rng(1)
    background = rng.integers(40, 200, (h, w, 3), dtype=np.uint8)
    with av.open(str(path), "w") as out:
        v = out.add_stream("libx264", rate=fps)
        v.width, v.height, v.pix_fmt = w, h, "yuv420p"
        v.codec_context.options = {"crf": "12", "g": "15"}
        a = None
        if audio:
            a = out.add_stream("aac", rate=48000)
            a.layout = "stereo"
        for i in range(frames):
            img = background.copy()
            x1, y1, x2, y2 = object_box(i)
            img[y1:y2, x1 : min(x2, w)] = 255
            # Motif fin à l'intérieur (comme les caractères d'une plaque) : c'est lui qui doit disparaître.
            for sx in range(x1 + 6, min(x2, w) - 6, 8):
                img[y1 + 6 : y2 - 6, sx : sx + 4] = 0
            frame = av.VideoFrame.from_ndarray(img, format="rgb24")
            frame.pts, frame.time_base = i, Fraction(1, fps)
            for p in v.encode(frame):
                out.mux(p)
        if a is not None:
            n = frames * 48000 // fps
            t = np.arange(n) / 48000
            wave = (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
            samples = np.stack([wave, wave])
            af = av.AudioFrame.from_ndarray(samples, format="fltp", layout="stereo")
            af.rate, af.pts, af.time_base = 48000, 0, Fraction(1, 48000)
            for p in a.encode(af):
                out.mux(p)
            for p in a.encode(None):
                out.mux(p)
        for p in v.encode(None):
            out.mux(p)
    return path


class FakeDetector:
    """Détecte le carré blanc par seuillage ; compte les images analysées."""

    def __init__(self, kind: str = "plate", score: float = 0.9, skip: set[int] | None = None):
        self.kind = kind
        self.score = score
        self.calls = 0
        self.skip = skip or set()

    def __call__(self, rgb: np.ndarray) -> dict[str, np.ndarray]:
        index = self.calls
        self.calls += 1
        empty = np.zeros((0, 5), dtype=np.float32)
        out = {"face": empty, "plate": empty}
        mask = rgb.min(axis=2) > 235
        if mask.any() and index not in self.skip:
            ys, xs = np.where(mask)
            out[self.kind] = np.array(
                [[xs.min(), ys.min(), xs.max() + 1, ys.max() + 1, self.score]], dtype=np.float32
            )
        return out


@pytest.fixture
def video_info() -> pj.VideoInfo:
    """Clip fictif 1920x1080 à 30 i/s, base de temps 1/15360 (512 PTS par image)."""
    return pj.VideoInfo("h264", 1920, 1080, "yuv420p", "30", "1/15360", 0, 512 * 300, 300, 8_000_000)


def make_project(tmp_path: Path, *videos: Path) -> pj.Project:
    from moto_blur.video import probe

    project = pj.Project(path=tmp_path / "test.motoblur.json")
    for v in videos:
        video, audio = probe(v)
        clip = pj.add_clip(project, v, video, audio)
        project.timeline.append(pj.Segment(clip.id, 0.0, round(video.duration_s, 6)))
    return project


def detail(region: np.ndarray) -> float:
    """Quantité de détail fin dans une zone : variation moyenne entre pixels voisins."""
    return float(np.abs(np.diff(region.astype(int), axis=1)).mean())


def read_frames(path: Path) -> list[np.ndarray]:
    with av.open(str(path)) as c:
        return [f.to_ndarray(format="rgb24") for f in c.decode(video=0)]


def read_audio(path: Path) -> np.ndarray:
    with av.open(str(path)) as c:
        chunks = [f.to_ndarray() for f in c.decode(audio=0)]
    return np.concatenate(chunks, axis=1)


def gauge_image(w: int = 120, h: int = 80) -> np.ndarray:
    """Un « compteur » texturé : cadran, aiguille, chiffres (aspect stable, reconnaissable)."""
    import cv2

    img = np.full((h, w, 3), 30, dtype=np.uint8)
    cv2.rectangle(img, (2, 2), (w - 3, h - 3), (200, 200, 200), 2)
    cv2.circle(img, (w // 3, h // 2), h // 3, (230, 230, 230), 2)
    cv2.line(img, (w // 3, h // 2), (w // 3 + h // 4, h // 2 - h // 5), (0, 0, 255), 3)
    cv2.putText(img, "88", (w // 2 + 4, h // 2 + 10), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (80, 255, 255), 2)
    for k in range(5):
        cv2.rectangle(img, (w // 2 + 4 + 10 * k, h - 18), (w // 2 + 10 + 10 * k, h - 10), (0, 200, 0), -1)
    return img


def make_shots_video(path: Path) -> tuple[Path, dict]:
    """Trois plans de 1 s à 30 i/s, en 640x360, comme une vidéo recadrée depuis une caméra 360 :

    - plan A : le compteur en bas à gauche, qui bouge un peu (vibrations) ;
    - plan B : autre cadrage, le compteur plus grand à droite, et un « rétroviseur » à
      l'ancienne place du compteur (ce sur quoi un suivi naïf glisse) ;
    - plan C : le compteur n'est plus dans l'image.
    Renvoie aussi les positions vraies du compteur (x1, y1, x2, y2) par image.
    """
    import cv2

    rng = np.random.default_rng(3)
    def ground(color):
        base = rng.integers(0, 60, (45, 80, 3)).astype(np.uint8)
        img = cv2.resize(base, (640, 360), interpolation=cv2.INTER_CUBIC)
        return np.clip(img.astype(int) + np.array(color), 0, 255).astype(np.uint8)

    grounds = {"A": ground((60, 90, 70)), "B": ground((150, 90, 40)), "C": ground((40, 40, 140))}
    gauge = gauge_image()
    big = cv2.resize(gauge, (156, 104))
    truth = {}
    with av.open(str(path), "w") as out:
        v = out.add_stream("libx264", rate=30)
        v.width, v.height, v.pix_fmt = 640, 360, "yuv420p"
        v.codec_context.options = {"crf": "16", "g": "15"}
        for i in range(90):
            shot = "A" if i < 30 else "B" if i < 60 else "C"
            dx, dy = int(3 * np.sin(i * 1.7)), int(2 * np.cos(i * 2.3))
            img = np.roll(grounds[shot], (dy, dx), axis=(0, 1)).copy()
            if shot == "A":
                x, y = 60 + i, 240 + dy
                img[y : y + 80, x : x + 120] = gauge
                truth[i] = (x, y, x + 120, y + 80)
            elif shot == "B":
                # rétroviseur : ovale clair et sombre à l'ancienne place du compteur
                cv2.ellipse(img, (140 + dx, 280 + dy), (62, 38), 0, 0, 360, (210, 210, 210), -1)
                cv2.ellipse(img, (140 + dx, 280 + dy), (50, 28), 0, 0, 360, (70, 60, 50), -1)
                x, y = 430 + dx, 150 + dy
                img[y : y + 104, x : x + 156] = big
                truth[i] = (x, y, x + 156, y + 104)
            frame = av.VideoFrame.from_ndarray(img, format="rgb24")
            frame.pts, frame.time_base = i, Fraction(1, 30)
            for p in v.encode(frame):
                out.mux(p)
        for p in v.encode(None):
            out.mux(p)
    return path, truth
