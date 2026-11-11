"""Détection d'objets image par image avec ONNX Runtime, par tuiles.

Pourquoi des tuiles : les détecteurs travaillent sur une image d'environ
640 x 640 pixels. Une image 4K réduite à cette taille perd un facteur 6 : une
plaque lointaine de 40 pixels n'en fait plus que 7 et devient indétectable. On
découpe donc l'image en tuiles qui se chevauchent, chacune moins réduite, et
on ajoute une passe sur l'image entière pour les objets proches, plus grands
qu'une tuile. Les détections sont ensuite ramenées dans le repère de l'image
et les doublons fusionnés.
"""

from __future__ import annotations

import math
import platform
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .models import ModelSpec, models_dir


@dataclass(frozen=True)
class Crop:
    """Zone source (x0, y0, w, h) redimensionnée dans le carré d'entrée du modèle."""

    x0: int
    y0: int
    w: int
    h: int
    scale: float  # pixels modèle par pixel source
    pad_x: float = 0.0
    pad_y: float = 0.0


def auto_tile_size(width: int, input_size: int) -> int:
    """Taille de tuile par défaut : un tiers de la largeur, au moins l'entrée du modèle."""
    return max(input_size, round(width / 3))


def _positions(length: int, tile: int, overlap: float) -> list[int]:
    if tile >= length:
        return [0]
    n = math.ceil((length - tile * overlap) / (tile * (1 - overlap)))
    n = max(n, 2)
    return [round(i * (length - tile) / (n - 1)) for i in range(n)]


def make_crops(
    width: int, height: int, input_size: int, tile_size: int, overlap: float, full_pass: bool
) -> list[Crop]:
    """Liste des zones à passer au modèle pour une image de `width` x `height`.

    `tile_size` : côté d'une tuile en pixels source ; 0 = automatique ; -1 = pas de tuiles.
    """
    crops: list[Crop] = []
    if tile_size == 0:
        tile_size = auto_tile_size(width, input_size)
    covers_all = False
    if tile_size > 0:
        tile = min(tile_size, width, height)
        xs, ys = _positions(width, tile, overlap), _positions(height, tile, overlap)
        covers_all = len(xs) == 1 and len(ys) == 1 and tile == width == height
        for y in ys:
            for x in xs:
                crops.append(Crop(x, y, tile, tile, input_size / tile))
    if (full_pass or not crops) and not covers_all:
        scale = input_size / max(width, height)
        pad_x = (input_size - width * scale) / 2
        pad_y = (input_size - height * scale) / 2
        crops.append(Crop(0, 0, width, height, scale, pad_x, pad_y))
    return crops


def render_crop(rgb: np.ndarray, crop: Crop, input_size: int) -> np.ndarray:
    """Extrait et redimensionne une zone ; l'image entière est centrée sur fond gris."""
    src = rgb[crop.y0 : crop.y0 + crop.h, crop.x0 : crop.x0 + crop.w]
    new_w = max(1, round(crop.w * crop.scale))
    new_h = max(1, round(crop.h * crop.scale))
    interp = cv2.INTER_AREA if crop.scale < 1 else cv2.INTER_LINEAR
    if (new_w, new_h) != (crop.w, crop.h):
        src = cv2.resize(src, (new_w, new_h), interpolation=interp)
    if new_w == input_size and new_h == input_size:
        return src
    canvas = np.full((input_size, input_size, 3), 114, dtype=np.uint8)
    x, y = round(crop.pad_x), round(crop.pad_y)
    canvas[y : y + new_h, x : x + new_w] = src[: input_size - y, : input_size - x]
    return canvas


def to_frame_coords(boxes: np.ndarray, crop: Crop) -> np.ndarray:
    """Boîtes (n, 5) x1,y1,x2,y2,score du repère du modèle vers le repère de l'image."""
    out = boxes.astype(np.float32).copy()
    out[:, [0, 2]] = (out[:, [0, 2]] - crop.pad_x) / crop.scale + crop.x0
    out[:, [1, 3]] = (out[:, [1, 3]] - crop.pad_y) / crop.scale + crop.y0
    return out


def merge_detections(boxes: np.ndarray, iou_thr: float = 0.5, ios_thr: float = 0.7) -> np.ndarray:
    """Fusionne les doublons issus de tuiles voisines. Entrée et sortie : (n, 5).

    Variante de la suppression des non-maxima orientée rappel : quand une boîte
    est absorbée par une autre de meilleur score, la boîte gardée est agrandie
    pour couvrir les deux (on ne rétrécit jamais la zone à flouter). Le second
    critère (intersection rapportée à la plus petite boîte) traite les objets
    coupés par le bord d'une tuile, vus en entier dans la tuile voisine.
    """
    if len(boxes) == 0:
        return boxes.reshape(0, 5)
    order = np.argsort(-boxes[:, 4])
    boxes = boxes[order].copy()
    areas = (boxes[:, 2] - boxes[:, 0]).clip(0) * (boxes[:, 3] - boxes[:, 1]).clip(0)
    alive = np.ones(len(boxes), dtype=bool)
    keep = []
    for i in range(len(boxes)):
        if not alive[i]:
            continue
        keep.append(i)
        rest = np.where(alive[i + 1 :])[0] + i + 1
        if len(rest) == 0:
            continue
        x1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        y1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        x2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        y2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = (x2 - x1).clip(0) * (y2 - y1).clip(0)
        iou = inter / np.maximum(areas[i] + areas[rest] - inter, 1e-6)
        ios = inter / np.maximum(np.minimum(areas[i], areas[rest]), 1e-6)
        dup = rest[(iou > iou_thr) | (ios > ios_thr)]
        if len(dup):
            boxes[i, 0] = min(boxes[i, 0], boxes[dup, 0].min())
            boxes[i, 1] = min(boxes[i, 1], boxes[dup, 1].min())
            boxes[i, 2] = max(boxes[i, 2], boxes[dup, 2].max())
            boxes[i, 3] = max(boxes[i, 3], boxes[dup, 3].max())
            alive[dup] = False
    return boxes[keep]


def available_devices() -> list[str]:
    import onnxruntime as ort

    return ort.get_available_providers()


def _providers(device: str) -> list:
    """Fournisseurs d'exécution par ordre de préférence, selon la machine.

    - macOS : CoreML (GPU et Neural Engine d'Apple Silicon).
    - Windows : DirectML (tout GPU DirectX 12, dont les Radeon).
    - Sinon, ou en repli : processeur.
    """
    import onnxruntime as ort

    available = ort.get_available_providers()
    chosen: list = []
    if device != "cpu":
        if "CoreMLExecutionProvider" in available and platform.system() == "Darwin":
            cache = models_dir() / "coreml-cache"
            cache.mkdir(parents=True, exist_ok=True)
            chosen.append(
                (
                    "CoreMLExecutionProvider",
                    {
                        "ModelFormat": "MLProgram",
                        "MLComputeUnits": "ALL",
                        "RequireStaticInputShapes": "1",
                        # Sans cache, CoreML recompile le modèle à chaque lancement.
                        "ModelCacheDirectory": str(cache),
                    },
                )
            )
        if "DmlExecutionProvider" in available:
            chosen.append("DmlExecutionProvider")
        if not chosen and device == "gpu":
            raise RuntimeError(
                "aucune accélération matérielle disponible (ni CoreML ni DirectML) ; "
                f"fournisseurs présents : {', '.join(available)}"
            )
    chosen.append("CPUExecutionProvider")
    return chosen


class Detector:
    """Un modèle de détection chargé, appliqué à une image entière par tuiles."""

    def __init__(
        self,
        spec: ModelSpec,
        model_path: Path,
        frame_size: tuple[int, int],
        conf: float,
        tile_size: int = 0,
        tile_overlap: float = 0.2,
        full_pass: bool = True,
        device: str = "auto",
    ):
        import onnxruntime as ort

        self.spec = spec
        self.conf = conf
        width, height = frame_size
        self.crops = make_crops(width, height, spec.input_size, tile_size, tile_overlap, full_pass)
        self.batch = len(self.crops) if spec.dynamic else 1

        providers = _providers(device)
        accelerated = "DmlExecutionProvider" in providers
        try:
            self.session = ort.InferenceSession(
                str(model_path), self._options(accelerated), providers=providers
            )
        except Exception as exc:  # repli processeur si le GPU refuse le modèle
            if device == "gpu":
                raise
            print(
                f"Attention : accélération matérielle indisponible pour {spec.name} ({exc}). "
                "Repli sur le processeur.",
                file=sys.stderr,
            )
            self.session = ort.InferenceSession(
                str(model_path), self._options(False), providers=["CPUExecutionProvider"]
            )
        self.provider = self.session.get_providers()[0]
        self.input_name = self.session.get_inputs()[0].name
        self.note = ""
        if device == "auto" and self.provider != "CPUExecutionProvider":
            self._keep_fastest(model_path)

    def _options(self, directml: bool):
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.log_severity_level = 3
        if self.spec.dynamic:
            # Dimensions figées : CoreML et DirectML sont nettement plus rapides ainsi.
            options.add_free_dimension_override_by_name("batch", self.batch)
            options.add_free_dimension_override_by_name("height", self.spec.input_size)
            options.add_free_dimension_override_by_name("width", self.spec.input_size)
        if directml:  # exigences documentées de DirectML
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        return options

    def _time(self, session) -> float:
        """Durée d'une inférence (secondes), la meilleure de deux après une mise en route."""
        size = self.spec.input_size
        blob = np.random.default_rng(0).random((self.batch, 3, size, size), dtype=np.float32)
        best = float("inf")
        for i in range(3):
            t0 = time.perf_counter()
            session.run(None, {self.input_name: blob})
            if i:
                best = min(best, time.perf_counter() - t0)
        return best

    def _keep_fastest(self, model_path: Path) -> None:
        """Mesure l'accélération matérielle contre le processeur et garde le plus rapide.

        Une accélération annoncée n'est pas toujours un gain : si le modèle est mal pris en
        charge, une partie du calcul retombe sur le processeur avec des allers-retours coûteux.
        """
        import onnxruntime as ort

        try:
            cpu = ort.InferenceSession(
                str(model_path), self._options(False), providers=["CPUExecutionProvider"]
            )
            fast, slow = self._time(self.session), self._time(cpu)
        except Exception as exc:  # noqa: BLE001 - la mesure ne doit jamais empêcher l'analyse
            self.note = f"mesure impossible ({exc})"
            return
        name = self.provider.replace("ExecutionProvider", "")
        self.note = f"{name} {1000 * fast:.0f} ms, processeur {1000 * slow:.0f} ms par lot"
        if slow < 0.9 * fast:
            self.session, self.provider = cpu, "CPUExecutionProvider"
            self.note += " : processeur retenu"

    def _blob(self, images: list[np.ndarray]) -> np.ndarray:
        batch = np.stack(images).transpose(0, 3, 1, 2)
        return np.ascontiguousarray(batch, dtype=np.float32) / 255.0

    def _decode(self, output: np.ndarray) -> list[np.ndarray]:
        """Sortie du modèle -> une liste de boîtes (n, 5) par image du lot."""
        results = []
        if self.spec.output == "ultralytics":
            for pred in output:  # (4 + classes, N)
                scores = pred[4:].max(axis=0)
                mask = scores >= self.conf
                cx, cy, w, h = pred[0, mask], pred[1, mask], pred[2, mask], pred[3, mask]
                boxes = np.stack(
                    [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, scores[mask]], axis=1
                )
                results.append(boxes)
        else:  # end2end : (n, 7) = lot, x1, y1, x2, y2, classe, score
            rows = output[output[:, 6] >= self.conf]
            results.append(rows[:, [1, 2, 3, 4, 6]])
        return results

    def detect(self, rgb: np.ndarray) -> np.ndarray:
        """Détections (n, 5) x1,y1,x2,y2,score dans le repère de l'image entière."""
        size = self.spec.input_size
        images = [render_crop(rgb, c, size) for c in self.crops]
        per_crop: list[np.ndarray] = []
        if self.batch == len(images):
            output = self.session.run(None, {self.input_name: self._blob(images)})[0]
            per_crop = self._decode(output)
        else:
            for image in images:
                output = self.session.run(None, {self.input_name: self._blob([image])})[0]
                per_crop.extend(self._decode(output))
        found = [to_frame_coords(b, c) for b, c in zip(per_crop, self.crops, strict=False) if len(b)]
        if not found:
            return np.zeros((0, 5), dtype=np.float32)
        boxes = merge_detections(np.concatenate(found))
        h, w = rgb.shape[:2]
        boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, w)
        boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, h)
        valid = (boxes[:, 2] - boxes[:, 0] >= 1) & (boxes[:, 3] - boxes[:, 1] >= 1)
        return boxes[valid]
