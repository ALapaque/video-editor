"""Conversion entre le temps des fichiers source et le temps du montage.

Le montage est une suite de passages gardés. Chaque passage est résolu en un
intervalle de PTS source [in, out) : une image est gardée si in <= pts < out.
Le temps du montage est la somme des durées des passages précédents.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from .intervals import Interval, normalize
from .project import Clip, Project, VideoInfo


@dataclass(frozen=True)
class ResolvedSegment:
    clip_id: str
    in_pts: int
    out_pts: int
    tb: Fraction  # base de temps du flux vidéo du clip
    offset: Fraction  # position de début dans le montage, en secondes

    @property
    def duration(self) -> Fraction:
        return (self.out_pts - self.in_pts) * self.tb

    @property
    def end(self) -> Fraction:
        return self.offset + self.duration


def sec_to_pts(seconds: float | Fraction, video: VideoInfo, snap: str = "ceil") -> int:
    """Secondes depuis la première image du clip -> PTS, aligné sur la grille des images.

    L'alignement (quand la durée d'une image est un nombre entier de PTS, ce qui
    est le cas des vidéos à fréquence constante) garantit que la durée d'un
    passage est exactement son nombre d'images multiplié par la durée d'une image.
    """
    exact = Fraction(seconds).limit_denominator(1_000_000) / video.tb
    step = video.frame_pts
    if step.denominator == 1 and step > 0:
        k = exact / step
        # Tolérance : 12.5 s tapé à la main doit tomber sur l'image voulue malgré l'arrondi.
        nearest = round(k)
        if abs(k - nearest) < Fraction(1, 1000):
            k = nearest
        else:
            k = math.ceil(k) if snap == "ceil" else math.floor(k)
        rel = int(k * step)
    else:
        rel = math.ceil(exact) if snap == "ceil" else math.floor(exact)
    rel = max(0, min(rel, video.end_pts - video.start_pts))
    return video.start_pts + rel


def pts_to_sec(pts: int, video: VideoInfo) -> float:
    """PTS -> secondes depuis la première image du clip."""
    return float((pts - video.start_pts) * video.tb)


def resolve_segments(project: Project) -> list[ResolvedSegment]:
    """Passages gardés du montage, dans l'ordre, en PTS source exacts."""
    out: list[ResolvedSegment] = []
    offset = Fraction(0)
    for seg in project.timeline:
        clip = project.clip(seg.clip)
        in_pts = sec_to_pts(seg.start, clip.video)
        out_pts = sec_to_pts(seg.end, clip.video)
        if out_pts <= in_pts:
            continue
        r = ResolvedSegment(clip.id, in_pts, out_pts, clip.video.tb, offset)
        out.append(r)
        offset = r.end
    return out


def timeline_duration(segments: list[ResolvedSegment]) -> Fraction:
    return segments[-1].end if segments else Fraction(0)


def source_to_timeline(segments: list[ResolvedSegment], clip_id: str, pts: int) -> list[Fraction]:
    """Positions dans le montage (secondes) d'une image source.

    Liste vide si l'image est coupée ; plusieurs valeurs si le même passage
    source apparaît plusieurs fois dans le montage.
    """
    return [
        s.offset + (pts - s.in_pts) * s.tb
        for s in segments
        if s.clip_id == clip_id and s.in_pts <= pts < s.out_pts
    ]


def timeline_to_source(segments: list[ResolvedSegment], t: float | Fraction) -> tuple[str, int] | None:
    """Temps du montage (secondes) -> (clip, PTS source). None si hors montage."""
    t = Fraction(t)
    for s in segments:
        if s.offset <= t < s.end:
            return s.clip_id, s.in_pts + math.floor((t - s.offset) / s.tb)
    return None


def kept_intervals(segments: list[ResolvedSegment], clip_id: str) -> list[Interval]:
    """Intervalles de PTS d'un clip présents dans le montage."""
    return normalize([(s.in_pts, s.out_pts) for s in segments if s.clip_id == clip_id])


def mute_intervals(project: Project, clip: Clip) -> list[Interval]:
    """Passages muets d'un clip, en PTS vidéo source."""
    return normalize(
        [
            (sec_to_pts(m.start, clip.video, "floor"), sec_to_pts(m.end, clip.video, "ceil"))
            for m in project.mutes
            if m.clip == clip.id
        ]
    )


def format_time(seconds: float | Fraction) -> str:
    seconds = float(seconds)
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def parse_time(text: str) -> float:
    """Accepte "83.5", "1:23.5" ou "0:01:23.5"."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"durée invalide : {text!r}")
    value = 0.0
    for p in parts:
        value = value * 60 + float(p)
    return value
