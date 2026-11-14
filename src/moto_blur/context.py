"""Vérification par le contexte : un visage est sur une personne, une plaque sur un véhicule.

Un détecteur d'objets courants (YOLOX-S, licence Apache-2.0, entraîné sur COCO) regarde
autour de chaque objet DOUTEUX. Un « visage » sans personne dessous (arbre, mur, reflet, sac),
une « plaque » hors de tout véhicule : ce sont très probablement de fausses détections.

Ce n'est qu'un indice, jamais une preuve : une personne en partie cachée peut ne pas être vue.
La décision (voir `postprocess.below_min_score`) n'écarte donc un objet sans contexte que si
la contre-vérification en gros plan ne l'a pas, de son côté, nettement retrouvé.

Mesure sur la scène de rue d'essai (45 visages douteux) : 6 fausses détections sans personne
(pare-brise, vitre, sac, bus, zone sombre), un seul vrai visage sans personne détectée, et
celui-là est nettement retrouvé en gros plan, donc gardé. Jamais mesuré sur des vidéos de moto.
"""

from __future__ import annotations

import cv2
import numpy as np

from .models import ensure_model, get_spec

SIZE = 640
PERSON = 0
VEHICLES = (2, 3, 5, 7)  # voiture, moto, bus, camion
MIN_SCORE = 0.2
ENOUGH = 0.5  # contexte trouvé avec cette assurance : inutile de regarder d'autres images


class ContextDetector:
    """Détecteur de personnes et de véhicules, appliqué à une vue de 640 pixels de côté."""

    def __init__(self):
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.log_severity_level = 3
        # Sur le processeur : peu d'images à regarder, et une session de plus sur la carte
        # graphique n'apporterait qu'un risque d'échec de chargement.
        path = ensure_model(get_spec("yolox-s", "context"))
        self.session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name
        grids, strides = [], []
        for stride in (8, 16, 32):
            n = SIZE // stride
            xv, yv = np.meshgrid(np.arange(n), np.arange(n))
            grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
            strides.append(np.full((n * n, 1), stride))
        self.grids = np.concatenate(grids).astype(np.float32)
        self.strides = np.concatenate(strides).astype(np.float32)

    def detect(self, view: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Boîtes (n, 4), scores (n,) et classes COCO (n,) dans une vue RVB de 640 x 640."""
        blob = view.astype(np.float32).transpose(2, 0, 1)[None]
        out = self.session.run(None, {self.input: blob})[0][0]
        centre = (out[:, :2] + self.grids) * self.strides
        size = np.exp(np.clip(out[:, 2:4], -10, 10)) * self.strides
        scores = out[:, 4:5] * out[:, 5:]
        classes = scores.argmax(axis=1)
        best = scores.max(axis=1)
        keep = best >= MIN_SCORE
        boxes = np.concatenate([centre - size / 2, centre + size / 2], axis=1)
        return boxes[keep], best[keep], classes[keep]


_detector: ContextDetector | None = None


def load() -> ContextDetector:
    global _detector
    if _detector is None:
        _detector = ContextDetector()
    return _detector


def _view(rgb: np.ndarray, cx: float, cy: float, side: float) -> tuple[np.ndarray, float, float, float]:
    ax, ay, k = cx - side / 2, cy - side / 2, SIZE / side
    view = cv2.warpAffine(
        rgb, np.float32([[k, 0, -ax * k], [0, k, -ay * k]]), (SIZE, SIZE),
        flags=cv2.INTER_LINEAR if k > 0.5 else cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE,
    )
    return view, ax, ay, k


def score(detector: ContextDetector, rgb: np.ndarray, kind: str, box) -> float:
    """Assurance (0 à 1) qu'un visage est sur une personne, ou une plaque sur un véhicule."""
    x1, y1, x2, y2 = (float(v) for v in box)
    w, h = x2 - x1, y2 - y1
    best = 0.0
    if kind == "face":
        # Vue assez large pour montrer le buste et les jambes, centrée un peu sous le visage.
        view, ax, ay, k = _view(rgb, (x1 + x2) / 2, (y1 + y2) / 2 + 1.5 * h, max(9.0 * max(w, h), 200.0))
        fx, fy, fh = ((x1 + x2) / 2 - ax) * k, ((y1 + y2) / 2 - ay) * k, h * k
        boxes, scores, classes = detector.detect(view)
        for b, s, c in zip(boxes, scores, classes, strict=True):
            tall = b[3] - b[1]
            # Le visage est dans le haut de la personne, nettement plus grande que lui.
            if c == PERSON and b[0] <= fx <= b[2] and b[1] - 0.3 * fh <= fy <= b[1] + 0.45 * tall and tall >= 2.0 * fh:
                best = max(best, float(s))
    else:
        view, ax, ay, k = _view(rgb, (x1 + x2) / 2, (y1 + y2) / 2, max(8.0 * max(w, h), 300.0))
        px, py, pw = ((x1 + x2) / 2 - ax) * k, ((y1 + y2) / 2 - ay) * k, w * k
        boxes, scores, classes = detector.detect(view)
        for b, s, c in zip(boxes, scores, classes, strict=True):
            if int(c) in VEHICLES and b[0] <= px <= b[2] and b[1] <= py <= b[3] and b[2] - b[0] >= 1.3 * pw:
                best = max(best, float(s))
    return best
