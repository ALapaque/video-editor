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

import cv2
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


STRONG_MARGIN = 0.25  # au-dessus de (score minimal + cette marge), une seule image suffit


def below_min_score(track: Track, settings: BlurSettings) -> bool:
    """Vrai si une piste détectée n'est pas assez sûre pour être floutée.

    Elle doit atteindre le score minimal de sa catégorie sur au moins `min_hits` images, ou
    une fois avec un score très net. Les fausses détections, qui clignotent sur une ou deux
    images, sont ainsi écartées sans toucher aux vrais objets, vus sur la durée.
    """
    if track.source != "auto" or track.forced or not track.obs:
        return False
    minimum = settings.min_score_for(track.kind)
    scores = [o[5] for o in track.obs]
    if max(scores) >= minimum + STRONG_MARGIN:
        return False
    hits = sum(1 for x in scores if x >= minimum)
    return hits < max(1, settings.min_hits)


def is_blurred(track: Track, settings: BlurSettings) -> bool:
    """Vrai si la piste donne lieu à un flou : ni ignorée à la main, ni écartée par son score."""
    return bool(track.obs) and not track.disabled and not below_min_score(track, settings)


@dataclass(frozen=True)
class Zone:
    """Zone à flouter sur une image. La boîte peut dépasser de l'image : le flou la rogne."""

    kind: str
    box: tuple[int, int, int, int]
    shape: str = "rect"  # "rect", "rounded", "ellipse" ou "contour"
    radius: int = 0  # rayon des coins pour "rounded"
    track_id: int = 0
    # Toutes les pistes réunies dans cette zone quand des flous ont été fusionnés.
    tracks: tuple[int, ...] = ()
    # Forme "contour" : polygones (pixels de l'image) dont la réunion, grossie de `grow` pixels,
    # est floutée. `box` en est le rectangle englobant.
    polygons: tuple[tuple[tuple[float, float], ...], ...] = ()
    grow: float = 0.0


# Catégorie gardée quand des zones de catégories différentes fusionnent : la plus « voulue ».
_KIND_PRIORITY = {"dash": 3, "manual": 3, "plate": 2, "face": 1}


def _inside(zone: Zone, x: float, y: float) -> bool:
    """Vrai si le point est réellement couvert par la forme de la zone."""
    x1, y1, x2, y2 = zone.box
    if not (x1 <= x <= x2 and y1 <= y <= y2):
        return False
    if zone.shape == "contour":  # sans compter le grossissement : jamais optimiste
        return any(
            cv2.pointPolygonTest(np.asarray(poly, dtype=np.float32), (float(x), float(y)), False) >= 0
            for poly in zone.polygons
        )
    if zone.shape == "ellipse":
        a, b = (x2 - x1) / 2, (y2 - y1) / 2
        if a <= 0 or b <= 0:
            return False
        return ((x - (x1 + x2) / 2) / a) ** 2 + ((y - (y1 + y2) / 2) / b) ** 2 <= 1.0
    r = zone.radius if zone.shape == "rounded" else 0
    if r > 0:
        cx = x1 + r if x < x1 + r else x2 - r if x > x2 - r else None
        cy = y1 + r if y < y1 + r else y2 - r if y > y2 - r else None
        if cx is not None and cy is not None:  # dans un coin arrondi
            return (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    return True


def _covers(outer: Zone, inner: Zone) -> bool:
    """Vrai si `outer` couvre toute la boîte de `inner` (ses quatre coins)."""
    x1, y1, x2, y2 = inner.box
    return all(_inside(outer, x, y) for x, y in ((x1, y1), (x2, y1), (x1, y2), (x2, y2)))


def _overlap(a: Zone, b: Zone) -> float:
    """Part de la plus petite boîte recouverte par l'autre (0 à 1)."""
    w = min(a.box[2], b.box[2]) - max(a.box[0], b.box[0])
    h = min(a.box[3], b.box[3]) - max(a.box[1], b.box[1])
    if w <= 0 or h <= 0:
        return 0.0
    area = lambda z: max(1, (z.box[2] - z.box[0]) * (z.box[3] - z.box[1]))  # noqa: E731
    return w * h / min(area(a), area(b))


def _corner(zone: Zone) -> float:
    """Plus grand rayon de coin qu'une zone englobante peut prendre sans découvrir celle-ci."""
    if zone.shape == "ellipse":
        return min(zone.box[2] - zone.box[0], zone.box[3] - zone.box[1]) / 2
    return float(zone.radius) if zone.shape == "rounded" else 0.0


def _groups(zones: list[Zone]) -> list[list[int]]:
    """Regroupe les zones dont les boîtes se touchent, directement ou de proche en proche.

    Seules les zones d'un même groupe peuvent fusionner. Dans une scène chargée, presque toutes
    les zones sont isolées : les repérer d'un coup (calcul vectorisé) évite de comparer chaque
    paire une à une, ce qui coûtait plusieurs millisecondes par image.
    """
    boxes = np.array([z.box for z in zones], dtype=np.float64)
    touch = (
        (boxes[:, None, 0] < boxes[None, :, 2]) & (boxes[None, :, 0] < boxes[:, None, 2])
        & (boxes[:, None, 1] < boxes[None, :, 3]) & (boxes[None, :, 1] < boxes[:, None, 3])
    )
    np.fill_diagonal(touch, False)
    parent = list(range(len(zones)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in zip(*np.nonzero(np.triu(touch)), strict=True):
        parent[find(int(i))] = find(int(j))
    groups: dict[int, list[int]] = {}
    for i in range(len(zones)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _merge_group(zones: list[Zone], min_overlap: float) -> list[Zone]:
    """Fusionne un petit groupe de zones qui se touchent (voir `merge_zones`)."""
    out = sorted(zones, key=lambda z: -(z.box[2] - z.box[0]) * (z.box[3] - z.box[1]))
    changed = True
    while changed:
        changed = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                # La zone gardée porte la catégorie et la piste la plus « voulue » des deux :
                # un objet suivi recouvert par une détection plus large garde son nom.
                lead = a if _KIND_PRIORITY.get(a.kind, 0) >= _KIND_PRIORITY.get(b.kind, 0) else b
                if _covers(a, b):
                    out[i] = replace(a, kind=lead.kind, track_id=lead.track_id, tracks=a.tracks + b.tracks)
                elif _covers(b, a):
                    out[i] = replace(b, kind=lead.kind, track_id=lead.track_id, tracks=b.tracks + a.tracks)
                elif _overlap(a, b) >= min_overlap:
                    box = (
                        min(a.box[0], b.box[0]), min(a.box[1], b.box[1]),
                        max(a.box[2], b.box[2]), max(a.box[3], b.box[3]),
                    )
                    # Coins arrondis seulement dans la mesure où ils ne découvrent rien de ce
                    # que les deux zones d'origine couvraient près de leurs propres coins.
                    radius = int(min(0.12 * min(box[2] - box[0], box[3] - box[1]), _corner(a), _corner(b)))
                    shape = "rounded" if radius >= 1 else "rect"
                    out[i] = Zone(lead.kind, box, shape, radius, lead.track_id, a.tracks + b.tracks)
                else:
                    continue
                del out[j]
                changed = True
                break
            if changed:
                break
    return out


def merge_zones(zones: list[Zone], min_overlap: float = 0.5) -> list[Zone]:
    """Réunit les flous qui se recouvrent, sans jamais découvrir un pixel qui était flouté.

    - Une zone entièrement couverte par une autre disparaît : elle n'ajoutait rien.
    - Deux zones qui se recouvrent largement sont remplacées par un seul rectangle arrondi
      englobant les deux (donc un peu plus grand que leur réunion).
    Les zones qui se touchent à peine restent séparées.
    """
    if len(zones) < 2:
        return [replace(z, tracks=z.tracks or (z.track_id,)) for z in zones]
    tagged = [replace(z, tracks=z.tracks or (z.track_id,)) for z in zones]
    out: list[Zone] = []
    for group in _groups(tagged):
        if len(group) == 1:
            out.append(tagged[group[0]])
        else:
            out.extend(_merge_group([tagged[i] for i in group], min_overlap))
    # La boîte englobante créée par une fusion peut atteindre une zone d'un autre groupe :
    # on recommence tant que le nombre de zones diminue (rare, et toujours sur peu de zones).
    if len(out) < len(tagged) and len(out) > 1:
        again = merge_zones(out, min_overlap)
        if len(again) < len(out):
            return again
    return out


# Écart maximal entre deux images de référence d'un contour précis pour qu'il soit utilisé entre elles.
OUTLINE_REACH_S = 0.6


def smooth_boxes(boxes: list[Box], half: int = 2) -> list[Box]:
    """Stabilise une suite de boîtes sans jamais en découvrir une.

    Le centre est moyenné sur les images voisines (les vibrations se compensent) ; la taille
    prend le maximum du voisinage (elle ne « respire » plus), agrandie si nécessaire pour que
    la boîte lissée contienne toujours la boîte d'origine de la même image.
    """
    arr = np.asarray(boxes, dtype=np.float64)
    n = len(arr)
    cx, cy = (arr[:, 0] + arr[:, 2]) / 2, (arr[:, 1] + arr[:, 3]) / 2
    hw, hh = (arr[:, 2] - arr[:, 0]) / 2, (arr[:, 3] - arr[:, 1]) / 2
    windows = [(max(0, i - half), min(n, i + half + 1)) for i in range(n)]

    def steady(size: np.ndarray) -> np.ndarray:
        # Plus grande taille du voisinage, puis moyenne de ces maximums : la taille varie en
        # douceur, et reste au moins égale à celle de l'image (chaque maximum la contient).
        top = np.array([size[lo:hi].max() for lo, hi in windows])
        return np.array([top[lo:hi].mean() for lo, hi in windows])

    sw, sh = steady(hw), steady(hh)
    out: list[Box] = []
    for i, (lo, hi) in enumerate(windows):
        mx, my = cx[lo:hi].mean(), cy[lo:hi].mean()
        w = max(sw[i], abs(cx[i] - mx) + hw[i])
        h = max(sh[i], abs(cy[i] - my) + hh[i])
        out.append((mx - w, my - h, mx + w, my + h))
    return out


class TrackZones:
    """Calcule la zone de flou d'une piste à n'importe quel instant."""

    def __init__(self, track: Track, settings: BlurSettings, video: VideoInfo):
        self.track = track
        if track.source == "manual":
            # Zone tracée à la main : exactement ce qui a été dessiné, sans marge ni prolongation.
            settings = replace(
                settings, margin=0.0, min_margin_px=0, extend_s=0.0, temporal_pad_frames=0
            )
        elif track.source == "tracker":
            # Objet suivi à partir d'un rectangle tracé : boîte fiable, petite marge.
            settings = replace(settings, margin=settings.dash_margin)
        self.settings = settings
        self.width, self.height = video.width, video.height
        obs = sorted(track.obs, key=lambda o: o[0])
        self.pts = [int(o[0]) for o in obs]
        self.boxes: list[Box] = [(o[1], o[2], o[3], o[4]) for o in obs]
        self.scores = [float(o[5]) for o in obs]
        self.raw = self.boxes  # détections telles quelles (affichage, mode debug)
        # Contour précis : images de référence triées, (pts, points relatifs à la boîte observée).
        self.outline = sorted(
            (int(row[0]), np.asarray(row[1:], dtype=np.float64).reshape(-1, 2))
            for row in (track.outline or []) if len(row) >= 7
        ) if track.outline else []
        self.outline_pts = [k[0] for k in self.outline]
        if settings.smooth and track.source != "manual" and len(obs) >= 3:
            self.boxes = smooth_boxes(self.boxes)
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
        exact = self.contour(pts)
        if exact is not None:
            return exact
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

    def _observed_box(self, pts: float) -> Box:
        """Boîte observée (non stabilisée) interpolée à `pts`, pour first <= pts <= last."""
        i = min(max(bisect.bisect_right(self.pts, pts) - 1, 0), len(self.pts) - 1)
        if i >= len(self.pts) - 1 or self.pts[i] == pts:
            return self.raw[i]
        w = (pts - self.pts[i]) / (self.pts[i + 1] - self.pts[i])
        a, b = self.raw[i], self.raw[i + 1]
        return tuple(a[k] + (b[k] - a[k]) * w for k in range(4))  # type: ignore[return-value]

    def contour(self, pts: float) -> Zone | None:
        """Zone qui épouse l'objet, si un contour précis est connu autour de cet instant.

        Entre deux images de référence, on floute la réunion des deux contours voisins, reportés
        sur la position courante de l'objet : la forme ne peut donc pas se resserrer plus vite
        que l'objet ne change. Sans contour fiable des deux côtés (ou hors de la partie suivie),
        None : la forme habituelle, plus large, s'applique.
        """
        if not self.outline or not (self.pts[0] <= pts <= self.pts[-1]):
            return None
        reach = OUTLINE_REACH_S * self.pts_per_s
        i = bisect.bisect_right(self.outline_pts, pts) - 1
        if i >= 0 and self.outline_pts[i] == pts:
            keys = [self.outline[i]]
        elif 0 <= i < len(self.outline) - 1 and self.outline_pts[i + 1] - self.outline_pts[i] <= reach:
            keys = [self.outline[i], self.outline[i + 1]]
        else:
            return None
        x1, y1, x2, y2 = self._observed_box(pts)
        w, h = x2 - x1, y2 - y1
        if w < 2 or h < 2:
            return None
        polygons = tuple(
            tuple((x1 + float(u) * w, y1 + float(v) * h) for u, v in points) for _, points in keys
        )
        # Grossissement : petite marge, plus le déplacement de l'objet d'une image à l'autre.
        s = self.settings
        grow = max(min(w, h) * s.margin, float(s.min_margin_px))
        for off in (-self.frame_pts, self.frame_pts):
            near = min(max(pts + off, self.pts[0]), self.pts[-1])
            n = self._observed_box(near)
            grow += max(abs((n[0] + n[2]) - (x1 + x2)), abs((n[1] + n[3]) - (y1 + y2))) / 4
        xs = [p[0] for poly in polygons for p in poly]
        ys = [p[1] for poly in polygons for p in poly]
        pad = grow + 2
        box = (
            int(np.floor(min(xs) - pad)), int(np.floor(min(ys) - pad)),
            int(np.ceil(max(xs) + pad)), int(np.ceil(max(ys) + pad)),
        )
        if box[2] <= 0 or box[3] <= 0 or box[0] >= self.width or box[1] >= self.height:
            return None
        return Zone(self.track.kind, box, "contour", 0, self.track.id, polygons=polygons, grow=grow)

    def observation(self, pts: int) -> list[float] | None:
        """Observation brute exactement à cet instant (pour le mode debug)."""
        i = bisect.bisect_left(self.pts, pts)
        if i < len(self.pts) and self.pts[i] == pts:
            return [*self.raw[i], self.scores[i]]
        return None


class ZoneIndex:
    """Toutes les zones de flou d'un clip, avec recherche rapide par instant."""

    def __init__(self, tracks: list[Track], settings: BlurSettings, video: VideoInfo):
        self.merge = settings.merge_overlaps
        self.items = [TrackZones(t, settings, video) for t in tracks if is_blurred(t, settings)]
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
        """Zones à flouter à l'instant `pts`, avec leur forme, flous superposés réunis."""
        zones = [z for item in self.active(pts) if (z := item.shaped(pts)) is not None]
        return merge_zones(zones) if self.merge and len(zones) > 1 else zones


# --- Gêne, flous semblables, zones à ne jamais flouter ----------------------
#
# Un flou douteux qui reste en place ne fait courir aucun risque : il est au pire inutile. On ne
# demande donc pas de le confirmer. Ces fonctions servent à retirer vite ceux qui gênent.


def median_box(track: Track) -> Box:
    arr = np.asarray([o[1:5] for o in track.obs], dtype=np.float64)
    return tuple(float(x) for x in np.median(arr, axis=0))  # type: ignore[return-value]


def footprint(track: Track, video: VideoInfo) -> tuple[float, float]:
    """Place qu'un flou prend à l'écran : (part de l'image en %, durée en secondes)."""
    x1, y1, x2, y2 = median_box(track)
    area = 100.0 * max(0.0, x2 - x1) * max(0.0, y2 - y1) / max(1, video.width * video.height)
    seconds = float((track.obs[-1][0] - track.obs[0][0]) * video.tb) + float(video.frame_pts * video.tb)
    return area, seconds


def nuisance(track: Track, video: VideoInfo) -> float:
    """Gêne d'un flou s'il est inutile : sa taille (en % de l'image) multipliée par sa durée,
    comptée jusqu'à 5 s. Un petit flou bref ne se remarque pas ; un gros flou durable, si."""
    area, seconds = footprint(track, video)
    return area * min(seconds, 5.0)


def similar_tracks(tracks: list[Track], reference: Track) -> list[Track]:
    """Flous automatiques « comme celui-ci » : même catégorie, au même endroit de l'image et de
    taille comparable. C'est le cas d'une fausse détection qui revient (rétroviseur, autocollant,
    cadran pris pour un visage) quand la caméra est fixée à la moto."""
    rx1, ry1, rx2, ry2 = median_box(reference)
    rw, rh = rx2 - rx1, ry2 - ry1
    if rw <= 0 or rh <= 0:
        return []
    out = []
    for t in tracks:
        if t is reference or t.source != "auto" or t.kind != reference.kind or not t.obs:
            continue
        x1, y1, x2, y2 = median_box(t)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        inside = rx1 - rw / 4 <= cx <= rx2 + rw / 4 and ry1 - rh / 4 <= cy <= ry2 + rh / 4
        ratio = ((x2 - x1) * (y2 - y1)) / (rw * rh)
        if inside and 0.4 <= ratio <= 2.5:
            out.append(t)
    return out


def in_ignore_zone(track: Track, zones: list[list[int]], share: float = 0.8) -> bool:
    """Vrai si le flou se tient dans une zone à ne jamais flouter : sur la plupart de ses images,
    l'essentiel de sa boîte est dans la zone. Un objet qui ne fait que traverser la zone, ou
    qui en déborde largement, reste flouté."""
    if not zones or not track.obs:
        return False
    for zx1, zy1, zx2, zy2 in zones:
        hits = 0
        for o in track.obs:
            w = min(o[3], zx2) - max(o[1], zx1)
            h = min(o[4], zy2) - max(o[2], zy1)
            area = max(1.0, (o[3] - o[1]) * (o[4] - o[2]))
            if w > 0 and h > 0 and w * h >= 0.6 * area:
                hits += 1
        if hits >= share * len(track.obs):
            return True
    return False


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
# Un passage qui réunit de 4 à 8 objets douteux est présenté objet par objet : la question
# « faut-il flouter ceci ? » est plus simple que « tout est-il bien flouté ? ». Au-delà (foule),
# un par un serait interminable : le passage reste entier, et la planche de vignettes de
# l'interface permet de trier tous ses objets d'un coup.
MAX_OBJECTS_PER_PASSAGE = 3
MAX_SPLIT_OBJECTS = 8


def build_review(
    clip_id: str,
    tracks: list[Track],
    video: VideoInfo,
    detect_every: int = 1,
    merge_s: float = 1.0,
    settings: BlurSettings | None = None,
) -> list[dict]:
    """Regroupe les pistes douteuses d'un clip en passages à vérifier (PTS source)."""
    flagged = []
    for t in tracks:
        if not t.obs or t.disabled or t.source != "auto":
            continue
        if settings is not None and below_min_score(t, settings):
            continue  # piste écartée : rien à vérifier, elle n'est pas floutée
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
            last["_parts"].append((start, end, kind, reasons, track_id))
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
                    "risk": False,  # flouté de toute façon : le vérifier est facultatif
                    "_parts": [(start, end, kind, reasons, track_id)],
                }
            )
    # Passage chargé : une question par objet plutôt qu'une seule pour des dizaines de zones,
    # à laquelle on ne peut pas répondre d'un coup d'œil.
    out: list[dict] = []
    for p in passages:
        parts = p.pop("_parts")
        # Raisons de chaque flou du passage, pour que l'interface dise de quel objet elle parle.
        p["reasons_by_track"] = {str(track_id): list(reasons) for _s, _e, _k, reasons, track_id in parts}
        if len(parts) <= MAX_OBJECTS_PER_PASSAGE or len(parts) > MAX_SPLIT_OBJECTS:
            out.append(p)
            continue
        for start, end, kind, reasons, track_id in parts:
            out.append({"clip": clip_id, "start_pts": start, "end_pts": end, "kinds": [kind],
                        "reasons": list(reasons), "tracks": [track_id], "risk": False,
                        "reasons_by_track": {str(track_id): list(reasons)}})
    return sorted(out, key=lambda r: (r["start_pts"], r["end_pts"]))
