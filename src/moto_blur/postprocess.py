"""Des pistes aux zones de flou, et repérage des passages douteux.

C'est ici que se joue le « zéro fuite ». À partir des observations brutes
d'une piste, la zone floutée à un instant donné est construite ainsi :

1. Interpolation : entre deux observations, la boîte est interpolée
   linéairement. Les trous de détection sont donc toujours comblés.
2. Prolongation : avant la première et après la dernière observation, le flou
   est maintenu pendant `extend_s`. La zone couvre à la fois la dernière
   position connue et la position extrapolée par le mouvement, et grossit avec
   le temps pour absorber l'incertitude.
3. Voisinage temporel : union avec les zones des images voisines, pour couvrir
   les vibrations et les déplacements rapides entre deux images.
4. Marge : la zone est élargie d'une fraction de sa taille.

Tout est exprimé en PTS source : rien ne dépend du montage.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, replace
from fractions import Fraction

import numpy as np

from .project import BlurSettings, Track, VideoInfo

Box = tuple[float, float, float, float]


def _union(a: Box, b: Box) -> Box:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _scale(box: Box, factor: float) -> Box:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    hw, hh = (box[2] - box[0]) / 2 * factor, (box[3] - box[1]) / 2 * factor
    return (cx - hw, cy - hh, cx + hw, cy + hh)


def _shift(box: Box, dx: float, dy: float) -> Box:
    return (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)


@dataclass(frozen=True)
class Zone:
    """Zone à flouter sur une image. La boîte peut dépasser de l'image : le flou la rogne."""

    kind: str
    box: tuple[int, int, int, int]
    shape: str = "rect"  # "rect", "rounded" ou "ellipse"
    radius: int = 0  # rayon des coins pour "rounded"
    track_id: int = 0


class TrackZones:
    """Calcule la zone de flou d'une piste à n'importe quel instant."""

    def __init__(self, track: Track, settings: BlurSettings, video: VideoInfo):
        self.track = track
        if track.source == "manual":
            # Zone tracée à la main : exactement ce qui a été dessiné, sans marge ni prolongation.
            settings = replace(
                settings, margin=0.0, min_margin_px=0, extend_s=0.0, temporal_pad_frames=0
            )
        elif track.kind == "dash":
            settings = replace(settings, margin=settings.dash_margin)
        self.settings = settings
        self.width, self.height = video.width, video.height
        obs = sorted(track.obs, key=lambda o: o[0])
        self.pts = [int(o[0]) for o in obs]
        self.boxes: list[Box] = [(o[1], o[2], o[3], o[4]) for o in obs]
        self.scores = [float(o[5]) for o in obs]
        self.frame_pts = float(video.frame_pts)
        self.pts_per_s = float(1 / video.tb)
        before = settings.extend_s if track.extend_before_s is None else track.extend_before_s
        after = settings.extend_s if track.extend_after_s is None else track.extend_after_s
        self.extend_before = before * self.pts_per_s
        self.extend_after = after * self.pts_per_s
        self.pad_pts = settings.temporal_pad_frames * self.frame_pts
        self.v_start = self._velocity(0, min(len(obs) - 1, 4))
        self.v_end = self._velocity(max(0, len(obs) - 5), len(obs) - 1)

    def _velocity(self, i: int, j: int) -> tuple[float, float]:
        """Vitesse du centre (pixels par PTS) entre les observations i et j."""
        dt = self.pts[j] - self.pts[i]
        if dt <= 0:
            return (0.0, 0.0)
        a, b = self.boxes[i], self.boxes[j]
        return (
            ((b[0] + b[2]) - (a[0] + a[2])) / 2 / dt,
            ((b[1] + b[3]) - (a[1] + a[3])) / 2 / dt,
        )

    @property
    def start(self) -> float:
        """Début de la zone de flou (prolongation et voisinage compris), en PTS."""
        return self.pts[0] - self.extend_before - self.pad_pts

    @property
    def end(self) -> float:
        return self.pts[-1] + self.extend_after + self.pad_pts

    def raw_box(self, pts: float) -> Box | None:
        """Boîte interpolée ou prolongée à l'instant `pts`, sans marge. None hors piste."""
        first, last = self.pts[0], self.pts[-1]
        if pts < first:
            dt = first - pts
            if dt > self.extend_before:
                return None
            return self._extended(self.boxes[0], self.v_start, -dt)
        if pts > last:
            dt = pts - last
            if dt > self.extend_after:
                return None
            return self._extended(self.boxes[-1], self.v_end, dt)
        i = bisect.bisect_right(self.pts, pts) - 1
        if i >= len(self.pts) - 1 or self.pts[i] == pts:
            return self.boxes[i]
        t0, t1 = self.pts[i], self.pts[i + 1]
        w = (pts - t0) / (t1 - t0)
        a, b = self.boxes[i], self.boxes[i + 1]
        return tuple(a[k] + (b[k] - a[k]) * w for k in range(4))  # type: ignore[return-value]

    def _extended(self, box: Box, velocity: tuple[float, float], dt: float) -> Box:
        """Prolongation : union de la position figée et de la position extrapolée, grossie."""
        moved = _shift(box, velocity[0] * dt, velocity[1] * dt)
        growth = 1 + self.settings.extend_growth * abs(dt) / self.pts_per_s
        return _scale(_union(box, moved), growth)

    def _padded(self, pts: float) -> tuple[Box, float, float] | None:
        """Boîte avec voisinage temporel et marge (non bornée à l'image), et les deux marges."""
        box: Box | None = None
        offsets = [0.0]
        for k in range(1, self.settings.temporal_pad_frames + 1):
            offsets += [-k * self.frame_pts, k * self.frame_pts]
        for off in offsets:
            b = self.raw_box(pts + off)
            if b is not None:
                box = b if box is None else _union(box, b)
        if box is None:
            return None
        s = self.settings
        mx = max((box[2] - box[0]) * s.margin, s.min_margin_px)
        my = max((box[3] - box[1]) * s.margin, s.min_margin_px)
        return (box[0] - mx, box[1] - my, box[2] + mx, box[3] + my), mx, my

    def zone(self, pts: float) -> tuple[int, int, int, int] | None:
        """Rectangle englobant de la zone de flou (entiers, borné à l'image) à l'instant `pts`."""
        padded = self._padded(pts)
        if padded is None:
            return None
        box = padded[0]
        x1, y1 = max(0, int(np.floor(box[0]))), max(0, int(np.floor(box[1])))
        x2, y2 = min(self.width, int(np.ceil(box[2]))), min(self.height, int(np.ceil(box[3])))
        if x2 <= x1 or y2 <= y1:
            return None
        return (x1, y1, x2, y2)

    def shaped(self, pts: float) -> Zone | None:
        """Zone de flou avec sa forme. La forme contient toujours la boîte détectée entière.

        - Ellipse : une ellipse inscrite dans un rectangle n'en couvre pas les coins. Pour
          qu'elle contienne toute la boîte détectée, ses axes valent au moins racine de 2 fois
          ceux de la boîte (plus la largeur du bord adouci).
        - Rectangle arrondi : le rayon des coins est limité par la marge, de sorte que les coins
          de la boîte détectée restent couverts.
        """
        padded = self._padded(pts)
        if padded is None or self.zone(pts) is None:
            return None
        (x1, y1, x2, y2), mx, my = padded
        shape = "rect" if self.track.source == "manual" else self.settings.shape_for(self.track.kind)
        radius = 0
        if shape == "ellipse":
            factor = 1.4143 / (1 - min(max(self.settings.feather, 0.0), 0.5))
            hw = max((x2 - x1) / 2, ((x2 - x1) / 2 - mx) * factor)
            hh = max((y2 - y1) / 2, ((y2 - y1) / 2 - my) * factor)
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            x1, y1, x2, y2 = cx - hw, cy - hh, cx + hw, cy + hh
        elif shape == "rounded":
            radius = int(min(2 * min(mx, my), (x2 - x1) / 2, (y2 - y1) / 2))
        box = (int(np.floor(x1)), int(np.floor(y1)), int(np.ceil(x2)), int(np.ceil(y2)))
        return Zone(self.track.kind, box, shape, radius, self.track.id)

    def observation(self, pts: int) -> list[float] | None:
        """Observation brute exactement à cet instant (pour le mode debug)."""
        i = bisect.bisect_left(self.pts, pts)
        if i < len(self.pts) and self.pts[i] == pts:
            return [*self.boxes[i], self.scores[i]]
        return None


class ZoneIndex:
    """Toutes les zones de flou d'un clip, avec recherche rapide par instant."""

    def __init__(self, tracks: list[Track], settings: BlurSettings, video: VideoInfo):
        self.items = [TrackZones(t, settings, video) for t in tracks if t.obs and not t.disabled]
        self.bucket = max(1.0, float(1 / video.tb))  # une case par seconde
        self.buckets: dict[int, list[TrackZones]] = {}
        for item in self.items:
            for b in range(int(item.start // self.bucket), int(item.end // self.bucket) + 1):
                self.buckets.setdefault(b, []).append(item)

    def active(self, pts: float) -> list[TrackZones]:
        return [z for z in self.buckets.get(int(pts // self.bucket), []) if z.start <= pts <= z.end]

    def zones(self, pts: float) -> list[tuple[str, tuple[int, int, int, int]]]:
        """Rectangles englobants à l'instant `pts` : liste de (catégorie, boîte bornée)."""
        out = []
        for item in self.active(pts):
            z = item.zone(pts)
            if z is not None:
                out.append((item.track.kind, z))
        return out

    def shaped(self, pts: float) -> list[Zone]:
        """Zones à flouter à l'instant `pts`, avec leur forme."""
        return [z for item in self.active(pts) if (z := item.shaped(pts)) is not None]


# --- Passages douteux -----------------------------------------------------


@dataclass
class ReviewThresholds:
    low_conf: float = 0.40  # score médian en dessous duquel la piste est douteuse
    small_px: float = 14.0  # plus petit côté médian, en pixels
    short_obs: int = 4  # nombre d'observations en dessous duquel la piste est « très courte »
    gap_frames: float = 3.0  # trou comblé, en nombre d'intervalles de détection
    border_px: int = 4


def track_reasons(
    track: Track, video: VideoInfo, detect_every: int = 1, thr: ReviewThresholds | None = None
) -> list[str]:
    """Raisons pour lesquelles une piste mérite une vérification humaine (liste vide si aucune)."""
    thr = thr or ReviewThresholds()
    obs = sorted(track.obs, key=lambda o: o[0])
    reasons = []
    scores = [o[5] for o in obs]
    median_score = float(np.median(scores))
    if median_score < thr.low_conf:
        reasons.append(f"confiance faible ({median_score:.2f})")
    step = float(video.frame_pts) * max(1, detect_every)
    gaps = [obs[i + 1][0] - obs[i][0] for i in range(len(obs) - 1)]
    big = [g for g in gaps if g > thr.gap_frames * step]
    if big:
        longest = float(Fraction(max(big)) * video.tb)
        reasons.append(f"suivi perdu puis retrouvé (trou de {longest:.2f} s comblé)")
    small = float(np.median([min(o[3] - o[1], o[4] - o[2]) for o in obs]))
    if small < thr.small_px:
        reasons.append(f"objet très petit ({small:.0f} px)")
    if len(obs) < thr.short_obs:
        reasons.append(f"piste très courte ({len(obs)} détection{'s' if len(obs) > 1 else ''})")
    b = thr.border_px
    touching = [
        o for o in obs
        if o[1] <= b or o[2] <= b or o[3] >= video.width - b or o[4] >= video.height - b
    ]
    if len(touching) > len(obs) / 2:
        reasons.append("objet partiel au bord de l'image")
    return reasons


MAX_PASSAGE_S = 6.0


def build_review(
    clip_id: str, tracks: list[Track], video: VideoInfo, detect_every: int = 1, merge_s: float = 1.0
) -> list[dict]:
    """Regroupe les pistes douteuses d'un clip en passages à vérifier (PTS source)."""
    flagged = []
    for t in tracks:
        if not t.obs or t.disabled or t.source != "auto":
            continue
        reasons = track_reasons(t, video, detect_every)
        if reasons:
            pts = [o[0] for o in t.obs]
            flagged.append((int(min(pts)), int(max(pts)), t.kind, reasons, t.id))
    flagged.sort()
    merge_pts = float(merge_s / video.tb)
    # Longueur maximale d'un passage : dans une scène chargée (foule), les pistes douteuses
    # s'enchaînent sans interruption ; on découpe pour garder des passages vérifiables.
    max_pts = float(MAX_PASSAGE_S / video.tb)
    passages: list[dict] = []
    for start, end, kind, reasons, track_id in flagged:
        last = passages[-1] if passages else None
        if last and start <= last["end_pts"] + merge_pts and start - last["start_pts"] < max_pts:
            last["end_pts"] = max(last["end_pts"], end)
            last["tracks"].append(track_id)
            if kind not in last["kinds"]:
                last["kinds"].append(kind)
            for r in reasons:
                label = r.split(" (")[0]
                if not any(x.split(" (")[0] == label for x in last["reasons"]):
                    last["reasons"].append(r)
        else:
            passages.append(
                {
                    "clip": clip_id,
                    "start_pts": start,
                    "end_pts": end,
                    "kinds": [kind],
                    "reasons": list(reasons),
                    "tracks": [track_id],
                }
            )
    return passages
