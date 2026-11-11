"""Fichiers proxy : copies légères des clips pour une lecture fluide dans l'interface.

Un navigateur ne lit pas de façon fluide (ni parfois pas du tout) du H.265 4K 10 bits. Pour
la timeline, chaque clip est donc converti une fois en H.264 540p 8 bits, avec une image clé
toutes les demi-secondes et sans images B : on peut ainsi se placer instantanément sur
n'importe quelle image. Le proxy a exactement les mêmes images, aux mêmes instants, que la
source. Il ne sert qu'à l'affichage : l'analyse et le rendu lisent toujours les originaux.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import av

from . import project as pj
from .progress import Progress

PROXY_HEIGHT = 540


def proxy_dir(project: pj.Project) -> Path:
    name = project.path.name
    stem = name[: -len(".json")] if name.endswith(".json") else name
    return project.path.with_name(stem + ".proxies")


def proxy_path(project: pj.Project, clip: pj.Clip) -> Path:
    return proxy_dir(project) / f"{clip.id}-{clip.fingerprint}.mp4"


def proxy_size(video: pj.VideoInfo) -> tuple[int, int]:
    if video.height <= PROXY_HEIGHT:
        return (video.width // 2 * 2, video.height // 2 * 2)
    return (round(video.width * PROXY_HEIGHT / video.height / 2) * 2, PROXY_HEIGHT)


def build_proxy(project: pj.Project, clip: pj.Clip, progress: Progress | None = None) -> Path:
    """Crée le proxy d'un clip s'il n'existe pas déjà. Renvoie son chemin."""
    target = proxy_path(project, clip)
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partiel.mp4")
    v = clip.video
    width, height = proxy_size(v)
    fps = v.fps_fraction
    try:
        with av.open(str(project.clip_path(clip))) as src, av.open(str(partial), "w") as out:
            vin = src.streams.video[0]
            vin.thread_type = "AUTO"
            vout = out.add_stream("libx264", rate=fps)
            vout.width, vout.height, vout.pix_fmt = width, height, "yuv420p"
            vout.codec_context.time_base = v.tb
            vout.codec_context.options = {
                "preset": "ultrafast", "crf": "24", "g": str(max(1, round(fps / 2))), "bf": "0",
            }
            streams = [vin]
            ain = aout = resampler = None
            if src.streams.audio:
                ain = src.streams.audio[0]
                aout = out.add_stream("aac", rate=48000)
                aout.layout = "stereo"
                aout.bit_rate = 128_000
                resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000)
                streams.append(ain)
            samples = 0
            for packet in src.demux(*streams):
                if packet.stream is vin:
                    for frame in packet.decode():
                        if frame.pts is None:
                            continue
                        small = frame.reformat(width=width, height=height, format="yuv420p")
                        small.pts = frame.pts - v.start_pts
                        small.time_base = v.tb
                        small.pict_type = av.video.frame.PictureType.NONE
                        for p in vout.encode(small):
                            out.mux(p)
                        if progress:
                            progress.step()
                elif ain is not None:
                    for frame in packet.decode():
                        for chunk in resampler.resample(frame):
                            chunk.pts = samples
                            chunk.time_base = Fraction(1, 48000)
                            samples += chunk.samples
                            for p in aout.encode(chunk):
                                out.mux(p)
            for p in vout.encode(None):
                out.mux(p)
            if aout is not None:
                for p in aout.encode(None):
                    out.mux(p)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(target)
    return target
