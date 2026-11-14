"""Lecture vidéo avec PyAV : inspection d'un fichier et décodage précis à l'image près.

On utilise PyAV (liaison Python de FFmpeg) plutôt qu'OpenCV parce qu'il donne
l'horodatage exact (PTS) de chaque image, indispensable pour des coupes
précises et pour indexer l'analyse sur le temps source.
"""

from __future__ import annotations

from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path

import av

from .project import AudioInfo, VideoInfo


def probe(path: Path) -> tuple[VideoInfo, AudioInfo | None]:
    """Lit les caractéristiques d'un fichier vidéo sans le décoder."""
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError(f"aucune piste vidéo dans {path}")
        v = container.streams.video[0]
        tb = Fraction(v.time_base)
        fps = Fraction(v.average_rate or v.guessed_rate or 30)
        start = v.start_time or 0
        if v.duration:
            end = start + v.duration
        elif container.duration:
            end = start + round(Fraction(container.duration, av.time_base) / tb)
        else:
            raise ValueError(f"durée inconnue pour {path}")
        frames = v.frames or round((end - start) * tb * fps)
        bit_rate = v.bit_rate or 0
        if not bit_rate and container.bit_rate:
            bit_rate = container.bit_rate
        rotation = 0
        try:
            first = next(container.decode(v))
            rotation = int(getattr(first, "rotation", 0) or 0)
        except (StopIteration, av.FFmpegError):
            pass
        video = VideoInfo(
            codec=v.codec_context.name,
            width=v.codec_context.width,
            height=v.codec_context.height,
            pix_fmt=v.codec_context.pix_fmt or "yuv420p",
            fps=str(fps),
            time_base=str(tb),
            start_pts=int(start),
            end_pts=int(end),
            frames=int(frames),
            bit_rate=int(bit_rate),
            rotation=rotation,
        )
        audio = None
        if container.streams.audio:
            a = container.streams.audio[0]
            audio = AudioInfo(
                codec=a.codec_context.name,
                rate=a.rate,
                layout=a.layout.name,
                channels=a.channels,
                bit_rate=int(a.bit_rate or 0),
            )
        return video, audio


def iter_frames(
    container: av.container.InputContainer, start_pts: int, end_pts: int
) -> Iterator[av.VideoFrame]:
    """Décode les images dont le PTS est dans [start_pts, end_pts), dans l'ordre d'affichage.

    On se place sur l'image clé qui précède `start_pts`, puis on décode en
    jetant les images antérieures : c'est la seule façon d'être exact, car on
    ne peut commencer à décoder que sur une image clé.
    """
    stream = container.streams.video[0]
    if not stream.codec_context.is_open:  # ne se règle qu'avant la première image décodée
        stream.thread_type = "AUTO"
    first_pts = stream.start_time or 0
    if start_pts > first_pts:
        container.seek(int(start_pts), stream=stream, backward=True, any_frame=False)
    else:
        container.seek(int(first_pts), stream=stream, backward=True, any_frame=False)
    for frame in container.decode(stream):
        pts = frame.pts
        if pts is None:
            continue
        if pts < start_pts:
            continue
        if pts >= end_pts:
            break
        yield frame
