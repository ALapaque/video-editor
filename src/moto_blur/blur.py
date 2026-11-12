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


def contour_mask(h: int, w: int, polygons, grow: float, feather: float) -> np.ndarray:
    """Masque (h, w) entre 0 et 1 de la réunion de polygones (repère du masque), grossie de `grow`.

    Le bord adouci est ajouté À L'EXTÉRIEUR de la forme grossie : l'intérieur reste couvert
    à 100 %, l'adoucissement ne découvre rien.
    """
    mask = np.zeros((h, w), dtype=np.uint8)
    for poly in polygons:
        points = np.rint(np.asarray(poly, dtype=np.float64) * 16).astype(np.int32)
        cv2.fillPoly(mask, [points], 255, cv2.LINE_AA, 4)
    mask = (mask >= 96).astype(np.uint8) * 255  # un pixel à peine touché compte comme couvert

    def grown(image: np.ndarray, reach: float) -> np.ndarray:
        r = int(np.ceil(reach))
        if r < 1:
            return image
        return cv2.dilate(image, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))

    core = grown(mask, grow)
    sigma = min(6.0, max(0.0, feather) * 12)
    out = core.astype(np.float32) / 255
    if sigma >= 0.6:
        soft = cv2.GaussianBlur(grown(core, 2 * sigma).astype(np.float32) / 255, (0, 0), sigma)
        out = np.maximum(out, soft)
    return out


def zone_outline(zone, max_side: int = 320) -> list[list[int]]:
    """Tracé (points en pixels de l'image) de ce que floute réellement une zone "contour"."""
    x1, y1, x2, y2 = zone.box
    scale = min(1.0, max_side / max(1, x2 - x1, y2 - y1))
    w, h = max(2, round((x2 - x1) * scale)), max(2, round((y2 - y1) * scale))
    local = [[((px - x1) * scale, (py - y1) * scale) for px, py in poly] for poly in zone.polygons]
    mask = contour_mask(h, w, local, zone.grow * scale, 0.0)
    found, _ = cv2.findContours((mask > 0.5).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not found:
        return []
    biggest = max(found, key=cv2.contourArea)
    simple = cv2.approxPolyDP(biggest, 0.6, True).reshape(-1, 2)
    return [[round(x1 + px / scale), round(y1 + py / scale)] for px, py in simple]


def shape_mask(
    h: int, w: int, shape: str, box: tuple[float, float, float, float], radius: float, feather: float
) -> np.ndarray | None:
    """Masque (h, w) entre 0 et 1 d'une forme dont `box` est donnée dans le repère du masque.

    `box` peut dépasser du masque (zone au bord de l'image). None signifie « tout le rectangle ».
    """
    if shape == "rect":
        return None
    x1, y1, x2, y2 = box
    mask = np.zeros((h, w), dtype=np.uint8)
    if shape == "ellipse":
        center = (round((x1 + x2) / 2 * 16), round((y1 + y2) / 2 * 16))
        axes = (max(1, round((x2 - x1) / 2 * 16)), max(1, round((y2 - y1) / 2 * 16)))
        cv2.ellipse(mask, center, axes, 0, 0, 360, 255, -1, cv2.LINE_AA, 4)
        sigma = feather * min(x2 - x1, y2 - y1) / 2 / 2.5
        out = mask.astype(np.float32) / 255
        if sigma >= 0.6:
            # Bord adouci, calculé sur un masque élargi pour ne pas créer d'arête au bord.
            pad = int(3 * sigma) + 1
            big = np.zeros((h + 2 * pad, w + 2 * pad), dtype=np.uint8)
            cv2.ellipse(big, (center[0] + pad * 16, center[1] + pad * 16), axes, 0, 0, 360, 255, -1, cv2.LINE_AA, 4)
            out = cv2.GaussianBlur(big.astype(np.float32) / 255, (0, 0), sigma)[pad:-pad, pad:-pad]
        return out
    # rectangle arrondi : un rectangle plein en croix, plus quatre disques dans les coins
    r = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    if r < 1:
        return None
    ix1, iy1, ix2, iy2 = round(x1), round(y1), round(x2), round(y2)
    ir = round(r)
    cv2.rectangle(mask, (ix1 + ir, iy1), (ix2 - ir - 1, iy2 - 1), 255, -1)
    cv2.rectangle(mask, (ix1, iy1 + ir), (ix2 - 1, iy2 - ir - 1), 255, -1)
    for cx, cy in ((ix1 + ir, iy1 + ir), (ix2 - ir - 1, iy1 + ir), (ix1 + ir, iy2 - ir - 1), (ix2 - ir - 1, iy2 - ir - 1)):
        cv2.circle(mask, (cx, cy), ir, 255, -1, cv2.LINE_AA)
    return mask.astype(np.float32) / 255


def blur_frame(
    frame: av.VideoFrame, zones: list, cells: int = 5, style: str = "pixelate", feather: float = 0.12
) -> None:
    """Floute en place les zones d'une image YUV planaire.

    `zones` : objets `Zone` (boîte, forme, rayon), ou simples boîtes (x1, y1, x2, y2) traitées
    comme des rectangles. Les boîtes sont en pixels de l'image et peuvent en dépasser.
    """
    if not zones:
        return
    if frame.format.name not in SUPPORTED_FORMATS:
        raise ValueError(f"format de pixels non pris en charge pour le flou : {frame.format.name}")
    bps = 2 if frame.format.components[0].bits > 8 else 1
    for plane in frame.planes:
        data = plane_array(plane, bps)
        sx, sy = frame.width / plane.width, frame.height / plane.height
        for zone in zones:
            box = getattr(zone, "box", zone)
            shape = getattr(zone, "shape", "rect")
            radius = getattr(zone, "radius", 0)
            x1, y1, x2, y2 = box
            # Arrondi vers l'extérieur : un plan de couleur à demi-résolution couvre toute la zone.
            px1, py1 = max(0, int(x1 // sx)), max(0, int(y1 // sy))
            px2 = min(plane.width, int(-(-x2 // sx)))
            py2 = min(plane.height, int(-(-y2 // sy)))
            if px2 <= px1 or py2 <= py1:
                continue
            region = data[py1:py2, px1:px2]
            local = (x1 / sx - px1, y1 / sy - py1, x2 / sx - px1, y2 / sy - py1)
            if shape == "contour":
                polygons = [
                    [(qx / sx - px1, qy / sy - py1) for qx, qy in poly] for poly in zone.polygons
                ]
                mask = contour_mask(py2 - py1, px2 - px1, polygons, zone.grow / sx, feather)
            else:
                mask = shape_mask(py2 - py1, px2 - px1, shape, local, radius / sx, feather)
            if mask is None:
                obscure_region(region, cells, style)
                continue
            hidden = region.copy()
            obscure_region(hidden, cells, style)
            mixed = hidden.astype(np.float32) * mask + region.astype(np.float32) * (1 - mask)
            region[...] = np.rint(mixed).astype(region.dtype)
