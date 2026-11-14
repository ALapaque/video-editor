"""Images et sons dérivés des clips, pour l'interface : vignettes, bande d'images, forme d'onde.

Tout est calculé à la demande puis gardé dans le dossier des aperçus du projet. Rien de tout
cela ne sert au rendu.
"""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import av
import cv2
import numpy as np

from . import project as pj
from .proxy import proxy_dir, proxy_path
from .video import iter_frames


def _cache(project: pj.Project, name: str) -> Path:
    folder = proxy_dir(project) / "cache"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name


def source_frame(project: pj.Project, clip: pj.Clip, pts: int) -> av.VideoFrame | None:
    """Image du fichier source la plus proche de `pts` (à une demi-image près)."""
    v = clip.video
    half = round(v.frame_pts / 2)
    with av.open(str(project.clip_path(clip))) as container:
        return next(iter_frames(container, int(pts) - half, v.end_pts + 1), None)


def track_thumbnail(project: pj.Project, clip: pj.Clip, track: pj.Track, width: int = 220) -> bytes:
    """Vignette (JPEG) de ce qu'une piste floute : l'image où la détection est la plus nette,
    cadrée autour de l'objet, non floutée, avec un cadre fin. Sert à juger d'un coup d'œil."""
    best = max(track.obs, key=lambda o: o[5])
    key = f"vignette-{clip.fingerprint}-{track.id}-{int(best[0])}-{int(best[1])}-{int(best[3])}-{width}.jpg"
    cached = _cache(project, key)
    if cached.exists():
        return cached.read_bytes()
    frame = source_frame(project, clip, int(best[0]))
    if frame is None:
        raise ValueError("image introuvable.")
    image = frame.to_ndarray(format="bgr24")
    h, w = image.shape[:2]
    x1, y1, x2, y2 = best[1:5]
    # Cadrage : l'objet et son entourage (pour reconnaître ce que c'est), au format 4:3.
    side = max((x2 - x1) * 2.2, (y2 - y1) * 2.2 * 4 / 3, 96.0)
    cw, ch = min(w, side), min(h, side * 3 / 4)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    left = int(min(max(0, cx - cw / 2), w - cw))
    top = int(min(max(0, cy - ch / 2), h - ch))
    crop = image[top : top + int(ch), left : left + int(cw)].copy()
    k = width / crop.shape[1]
    crop = cv2.resize(crop, (width, max(1, round(crop.shape[0] * k))), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_LINEAR)
    p1 = (round((x1 - left) * k), round((y1 - top) * k))
    p2 = (round((x2 - left) * k), round((y2 - top) * k))
    cv2.rectangle(crop, p1, p2, (0, 0, 0), 3)
    cv2.rectangle(crop, p1, p2, (255, 255, 255), 1)
    ok, data = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok:
        raise ValueError("encodage de la vignette impossible.")
    cached.write_bytes(data.tobytes())
    return data.tobytes()


def _preview_source(project: pj.Project, clip: pj.Clip) -> Path:
    """Fichier le plus rapide à lire pour un aperçu : la copie légère si elle existe."""
    proxy = proxy_path(project, clip)
    return proxy if proxy.exists() else project.clip_path(clip)


def filmstrip(project: pj.Project, clip: pj.Clip, count: int = 40, height: int = 54) -> bytes:
    """Bande d'images (JPEG) : `count` images régulièrement espacées du clip, côte à côte."""
    count, height = max(2, min(int(count), 200)), max(24, min(int(height), 120))
    cached = _cache(project, f"bande-{clip.id}-{clip.fingerprint}-{count}x{height}.jpg")
    if cached.exists():
        return cached.read_bytes()
    v = clip.video
    width = max(2, round(height * v.width / v.height))
    tiles = []
    with av.open(str(_preview_source(project, clip))) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        start = stream.start_time or 0
        duration = Fraction(v.end_pts - v.start_pts) * v.tb
        for i in range(count):
            t = duration * Fraction(2 * i + 1, 2 * count)
            container.seek(int(start + t / stream.time_base), stream=stream, backward=True, any_frame=False)
            frame = next(container.decode(stream), None)  # image clé précédente : suffisant ici
            if frame is None:
                tiles.append(np.zeros((height, width, 3), dtype=np.uint8))
                continue
            tiles.append(frame.reformat(width=width, height=height, format="bgr24").to_ndarray())
    ok, data = cv2.imencode(".jpg", np.hstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 70])
    if not ok:
        raise ValueError("encodage de la bande d'images impossible.")
    cached.write_bytes(data.tobytes())
    return data.tobytes()


def waveform(project: pj.Project, clip: pj.Clip, points: int = 1500) -> list[float]:
    """Niveau sonore du clip : `points` valeurs entre 0 et 1 (crête par tranche de temps)."""
    points = max(50, min(int(points), 6000))
    cached = _cache(project, f"son-{clip.id}-{clip.fingerprint}-{points}.json")
    if cached.exists():
        return json.loads(cached.read_text())
    if clip.audio is None:
        return []
    peaks = np.zeros(points, dtype=np.float32)
    duration = float((clip.video.end_pts - clip.video.start_pts) * clip.video.tb)
    with av.open(str(_preview_source(project, clip))) as container:
        if not container.streams.audio:
            return []
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="flt", layout="mono", rate=8000)
        position = 0
        for frame in container.decode(stream):
            for chunk in resampler.resample(frame):
                samples = np.abs(chunk.to_ndarray().reshape(-1))
                index = ((position + np.arange(len(samples))) / 8000 / max(duration, 1e-6) * points).astype(np.int64)
                keep = index < points
                np.maximum.at(peaks, index[keep], samples[keep])
                position += len(samples)
    top = float(peaks.max())
    values = [round(float(x), 3) for x in (peaks / top if top > 0 else peaks)]
    cached.write_text(json.dumps(values))
    return values
