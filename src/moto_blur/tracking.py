"""Suivi des objets d'une image à l'autre.

Principe (inspiré de ByteTrack) :
- Chaque objet suivi est une « piste ». À chaque image analysée, on prédit où
  devrait se trouver chaque piste (dernière position + vitesse estimée), puis
  on lui associe la détection la plus proche.
- Deux seuils de confiance. Une détection sûre (>= conf_new) peut créer une
  piste. Une détection faible (>= conf_keep, très bas) ne crée rien, mais peut
  PROLONGER une piste existante : un objet déjà vu continue donc d'être suivi
  même quand le détecteur hésite (flou de mouvement, éloignement, contre-jour).
- Une piste sans détection reste en attente pendant `max_gap` : si l'objet
  réapparaît, la même piste reprend et le trou sera comblé par interpolation.

L'identité exacte des pistes importe peu ici : ce qui compte est qu'aucune
détection ne soit perdue. Toute détection retenue finit dans une piste.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


def iou(a: np.ndarray, b: np.ndarray) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return float(inter / (area_a + area_b - inter))


def expand(box: np.ndarray, factor: float) -> np.ndarray:
    """Agrandit une boîte autour de son centre (factor = 1.5 : côtés multipliés par 1,5)."""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    hw, hh = (box[2] - box[0]) / 2 * factor, (box[3] - box[1]) / 2 * factor
    return np.array([cx - hw, cy - hh, cx + hw, cy + hh], dtype=np.float32)


@dataclass
class LiveTrack:
    id: int
    kind: str
    obs: list[list[float]] = field(default_factory=list)  # [pts, x1, y1, x2, y2, score]
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(2, dtype=np.float32))

    @property
    def last_pts(self) -> int:
        return int(self.obs[-1][0])

    @property
    def last_box(self) -> np.ndarray:
        return np.array(self.obs[-1][1:5], dtype=np.float32)

    def predict(self, pts: int) -> np.ndarray:
        """Position attendue à l'instant `pts` : dernière boîte déplacée par la vitesse."""
        shift = self.velocity * (pts - self.last_pts)
        box = self.last_box
        return box + np.array([shift[0], shift[1], shift[0], shift[1]], dtype=np.float32)

    def add(self, pts: int, det: np.ndarray) -> None:
        if self.obs:
            dt = pts - self.last_pts
            if dt > 0:
                old, new = self.last_box, det[:4]
                v = np.array(
                    [
                        ((new[0] + new[2]) - (old[0] + old[2])) / 2 / dt,
                        ((new[1] + new[3]) - (old[1] + old[3])) / 2 / dt,
                    ],
                    dtype=np.float32,
                )
                # Moyenne glissante : lisse le bruit des vibrations.
                self.velocity = v if len(self.obs) == 1 else 0.6 * self.velocity + 0.4 * v
        self.obs.append([int(pts), *[float(x) for x in det[:4]], float(det[4])])


class Tracker:
    """Suit les objets d'une seule catégorie (visages ou plaques) dans un passage."""

    def __init__(
        self,
        kind: str,
        conf_new: float,
        max_gap_pts: int,
        first_id: int = 1,
        match_iou: float = 0.1,
        match_expand: float = 1.6,
    ):
        self.kind = kind
        self.conf_new = conf_new
        self.max_gap_pts = max_gap_pts
        self.match_iou = match_iou
        # Les boîtes sont agrandies avant comparaison : sur un petit objet, quelques
        # pixels de vibration suffisent à annuler tout recouvrement.
        self.match_expand = match_expand
        self.next_id = first_id
        self.active: list[LiveTrack] = []
        self.finished: list[LiveTrack] = []

    def _match(self, tracks: list[LiveTrack], dets: np.ndarray, pts: int) -> list[tuple[int, int]]:
        """Associe pistes et détections par recouvrement décroissant."""
        pairs = []
        for ti, track in enumerate(tracks):
            # Plus la piste est perdue depuis longtemps, plus on cherche large.
            lost = (pts - track.last_pts) / max(self.max_gap_pts, 1)
            predicted = expand(track.predict(pts), self.match_expand * (1 + lost))
            for di in range(len(dets)):
                score = iou(predicted, expand(dets[di, :4], self.match_expand))
                if score >= self.match_iou:
                    pairs.append((score, ti, di))
        pairs.sort(reverse=True)
        used_t: set[int] = set()
        used_d: set[int] = set()
        matches = []
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            matches.append((ti, di))
        return matches

    def update(self, pts: int, dets: np.ndarray) -> None:
        """Intègre les détections (n, 5) de l'image d'horodatage `pts`."""
        # Pistes perdues depuis trop longtemps : terminées.
        still = []
        for t in self.active:
            (still if pts - t.last_pts <= self.max_gap_pts else self.finished).append(t)
        self.active = still

        high = dets[dets[:, 4] >= self.conf_new] if len(dets) else dets
        low = dets[dets[:, 4] < self.conf_new] if len(dets) else dets

        # 1) détections sûres contre toutes les pistes
        matched_tracks: set[int] = set()
        matched_high: set[int] = set()
        for ti, di in self._match(self.active, high, pts):
            self.active[ti].add(pts, high[di])
            matched_tracks.add(ti)
            matched_high.add(di)

        # 2) détections faibles contre les pistes restantes
        remaining = [i for i in range(len(self.active)) if i not in matched_tracks]
        if remaining and len(low):
            subset = [self.active[i] for i in remaining]
            for ti, di in self._match(subset, low, pts):
                subset[ti].add(pts, low[di])

        # 3) détections sûres sans piste : nouvelles pistes
        for di in range(len(high)):
            if di not in matched_high:
                track = LiveTrack(self.next_id, self.kind)
                self.next_id += 1
                track.add(pts, high[di])
                self.active.append(track)

    def finish(self) -> list[LiveTrack]:
        """Termine le passage et renvoie toutes les pistes."""
        self.finished.extend(self.active)
        self.active = []
        return sorted(self.finished, key=lambda t: t.obs[0][0])
