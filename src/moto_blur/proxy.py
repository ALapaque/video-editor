"""Fichiers proxy : copies légères des clips pour une lecture fluide dans l'interface.

Un navigateur ne lit pas de façon fluide (ni parfois pas du tout) du H.265 4K 10 bits. Pour
la timeline, chaque clip est donc converti une fois en H.264 8 bits, avec une image clé
toutes les demi-secondes et sans images B : on peut ainsi se placer instantanément sur
n'importe quelle image. Le proxy a exactement les mêmes images, aux mêmes instants, que la
source. Il ne sert qu'à l'affichage : l'analyse et le rendu lisent toujours les originaux.

Deux qualités, mesurées sur le PETIT côté de l'image (une vidéo verticale garde ainsi autant de
détail qu'une horizontale) :
- aperçu rapide : 540 pixels, fabriqué d'office ;
- aperçu net : 1080 pixels, fabriqué à la demande (plus long à préparer, fichier plus gros).
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import av

from . import project as pj
from .progress import Progress

PROXY_HEIGHT = 540  # aperçu rapide : petit côté de l'image
FINE_HEIGHT = 1080  # aperçu net


def proxy_dir(project: pj.Project) -> Path:
    name = project.path.name
    stem = name[: -len(".json")] if name.endswith(".json") else name
    return project.path.with_name(stem + ".proxies")


def proxy_path(project: pj.Project, clip: pj.Clip, fine: bool = False) -> Path:
    # Le nom dit la qualité. Une vidéo verticale a son propre nom d'aperçu rapide : les anciens
    # aperçus, mesurés sur la hauteur, étaient trop étroits (304 pixels de large) pour elle.
    vertical = clip.video.height > clip.video.width
    tag = f"-{FINE_HEIGHT}" if fine else ("-v540" if vertical else "")
    return proxy_dir(project) / f"{clip.id}-{clip.fingerprint}{tag}.mp4"


def proxy_size(video: pj.VideoInfo, fine: bool = False) -> tuple[int, int]:
    """Taille de l'aperçu : le petit côté ramené à 540 (ou 1080) pixels, jamais agrandi."""
    short = FINE_HEIGHT if fine else PROXY_HEIGHT
    small = min(video.width, video.height)
    if small <= short:
        return (video.width // 2 * 2, video.height // 2 * 2)
    k = short / small
    return (round(video.width * k / 2) * 2, round(video.height * k / 2) * 2)


def fine_is_useful(video: pj.VideoInfo) -> bool:
    """L'aperçu net n'apporte quelque chose que si la source est plus grande que l'aperçu rapide."""
    return min(video.width, video.height) > PROXY_HEIGHT


def build_proxy(
    project: pj.Project, clip: pj.Clip, progress: Progress | None = None, fine: bool = False
) -> Path:
    """Crée le proxy d'un clip s'il n'existe pas déjà. Renvoie son chemin."""
    target = proxy_path(project, clip, fine)
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partiel.mp4")
    v = clip.video
    width, height = proxy_size(v, fine)
    fps = v.fps_fraction
    try:
        with av.open(str(project.clip_path(clip))) as src, av.open(str(partial), "w") as out:
            vin = src.streams.video[0]
            vin.thread_type = "AUTO"
            vout = out.add_stream("libx264", rate=fps)
            vout.width, vout.height, vout.pix_fmt = width, height, "yuv420p"
            vout.codec_context.time_base = v.tb
            vout.codec_context.options = {
                "preset": "ultrafast", "crf": "21" if fine else "24",
                "g": str(max(1, round(fps / 2))), "bf": "0",
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
