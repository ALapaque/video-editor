"""Floutage irréversible, appliqué directement sur les plans de l'image.

Une image vidéo est stockée en YUV : un plan de luminosité (Y) et deux plans
de couleur (U, V), souvent à demi-résolution. On modifie ces plans tels quels,
sans passer par le RVB : tout ce qui est hors des zones floutées reste donc
strictement identique à la source, et la profondeur (8 ou 10 bits) est
conservée.

Irréversibilité : un flou gaussien léger peut être partiellement inversé. Ici
la zone est réduite à quelques cellules de couleur moyenne (5 au plus sur le
grand côté), puis ré-agrandie en lissant. L'information d'origine est détruite :
il ne reste que quelques valeurs moyennes pour toute la zone.
"""

from __future__ import annotations

import av
import cv2
import numpy as np

Zone = tuple[int, int, int, int]

SUPPORTED_FORMATS = {
    "yuv420p", "yuvj420p", "yuv422p", "yuvj422p", "yuv444p", "yuvj444p",
    "yuv420p10le", "yuv422p10le", "yuv444p10le", "yuv420p12le",
}


def obscure_region(region: np.ndarray, cells: int, style: str) -> None:
    """Remplace en place le contenu d'une zone d'un plan (2D) par une version illisible."""
    h, w = region.shape[:2]
    if h == 0 or w == 0:
        return
    if style == "solid":
        region[...] = region.mean(axis=(0, 1)).astype(region.dtype)
        return
    if w >= h:
        cw, ch = cells, max(1, round(cells * h / w))
    else:
        cw, ch = max(1, round(cells * w / h)), cells
    cw, ch = min(cw, w), min(ch, h)
    small = cv2.resize(region, (cw, ch), interpolation=cv2.INTER_AREA)
    region[...] = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def plane_array(plane: av.video.plane.VideoPlane, bytes_per_sample: int) -> np.ndarray:
    """Vue modifiable sur un plan de l'image (sans copie)."""
    dtype = np.uint16 if bytes_per_sample == 2 else np.uint8
    full = np.frombuffer(plane, dtype=dtype).reshape(plane.height, plane.line_size // bytes_per_sample)
    return full[:, : plane.width]


def blur_frame(frame: av.VideoFrame, zones: list[Zone], cells: int = 5, style: str = "pixelate") -> None:
    """Floute en place les zones (x1, y1, x2, y2 en pixels de l'image) d'une image YUV planaire."""
    if not zones:
        return
    if frame.format.name not in SUPPORTED_FORMATS:
        raise ValueError(f"format de pixels non pris en charge pour le flou : {frame.format.name}")
    bps = 2 if frame.format.components[0].bits > 8 else 1
    for plane in frame.planes:
        data = plane_array(plane, bps)
        sx, sy = frame.width / plane.width, frame.height / plane.height
        for x1, y1, x2, y2 in zones:
            # Arrondi vers l'extérieur : un plan de couleur à demi-résolution couvre toute la zone.
            px1, py1 = int(x1 // sx), int(y1 // sy)
            px2 = min(plane.width, int(-(-x2 // sx)))
            py2 = min(plane.height, int(-(-y2 // sy)))
            if px2 > px1 and py2 > py1:
                obscure_region(data[py1:py2, px1:px2], cells, style)
