"""Contour précis d'un objet suivi : le flou épouse l'objet au lieu d'un rectangle.

Le suivi d'objet donne, image par image, une boîte autour de l'objet. Ici, un modèle de
segmentation (SAM 2.1, version « tiny », licence Apache-2.0) reçoit cette boîte et renvoie la
silhouette exacte de ce qu'elle contient. Tout tourne sur cette machine ; le seul appel réseau
est le téléchargement du modèle, une fois.

Prudence (exigence « zéro fuite ») : un contour serré protège moins qu'un rectangle large.
- La silhouette n'est gardée que si le modèle est sûr de lui et si elle occupe bien la boîte
  suivie ; sinon, autour de cette image, la forme habituelle (plus large) continue de s'appliquer.
- Entre deux images de référence, on floute la réunion des deux silhouettes voisines, grossie
  d'une marge et du déplacement de l'objet (voir `postprocess.TrackZones.contour`).
- Avant et après la partie suivie (prolongation du flou), la forme habituelle s'applique.
"""

from __future__ import annotations

import sys

import av
import cv2
import numpy as np

from . import names
from . import project as pj
from .models import ensure_files
from .progress import Progress
from .video import iter_frames

_HF = "https://huggingface.co/onnx-community/sam2.1-hiera-tiny-ONNX/resolve/814a066640debee5a91e70aa401fb8e17e030503/onnx"
SAM_FILES = [
    ("vision_encoder.onnx", f"{_HF}/vision_encoder.onnx",
     "4f30aacd3aaefbca81a0b7fe4c1fc96345570ea0a6f80ced599493d1b3be2e8c", 0.4),
    ("vision_encoder.onnx_data", f"{_HF}/vision_encoder.onnx_data",
     "e83df9866a5afe68ea7f0f721f18f65137fc3acbf0da1c74e946d363e09c69cc", 134.1),
    ("prompt_encoder_mask_decoder.onnx", f"{_HF}/prompt_encoder_mask_decoder.onnx",
     "874414704c5d686db7d206a35f6e15d26563d50c8c4468fccc6739bd7e491dcf", 0.2),
    ("prompt_encoder_mask_decoder.onnx_data", f"{_HF}/prompt_encoder_mask_decoder.onnx_data",
     "e9874d900dd4134ed60eab1e97910327c2419e0b2954485d8fd6e7f1a1470f47", 21.0),
]

INPUT = 1024  # côté de l'image attendue par le modèle
GROW = 2.2  # côté de la vue envoyée au modèle, en multiple du grand côté de la boîte suivie
STEP_S = 0.25  # écart entre deux images de référence
MIN_OBJECT_LOGIT = 0.0  # « il y a bien un objet dans la boîte »
MIN_QUALITY = 0.6  # qualité que le modèle prédit pour sa propre silhouette
MIN_BOX_IOU = 0.45  # la silhouette doit occuper la boîte suivie, ni un détail ni tout le décor
MAX_POINTS = 48
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class Segmenter:
    """Le modèle de segmentation chargé (encodeur d'image + décodeur de masque)."""

    def __init__(self, device: str = "auto"):
        import onnxruntime as ort

        folder = ensure_files("sam2.1-tiny", SAM_FILES)
        device = (names.env("CONTOUR_DEVICE") or device)
        options = ort.SessionOptions()
        options.log_severity_level = 3
        providers = ["CPUExecutionProvider"]
        if device != "cpu" and "DmlExecutionProvider" in ort.get_available_providers():
            # DirectML (Windows) : mêmes exigences que pour la détection.
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
        self.note = ""
        try:
            self.encoder = ort.InferenceSession(str(folder / "vision_encoder.onnx"), options, providers=providers)
            self._encode(np.zeros((INPUT, INPUT, 3), dtype=np.uint8))  # un essai, pour échouer ici
        except Exception as exc:  # noqa: BLE001 - la carte graphique refuse le modèle : processeur
            if providers == ["CPUExecutionProvider"]:
                raise
            self.note = f"carte graphique refusée ({type(exc).__name__}), calcul sur le processeur"
            print(f"Contour précis : {self.note}.", file=sys.stderr)
            plain = ort.SessionOptions()
            plain.log_severity_level = 3
            self.encoder = ort.InferenceSession(
                str(folder / "vision_encoder.onnx"), plain, providers=["CPUExecutionProvider"]
            )
        cpu = ort.SessionOptions()
        cpu.log_severity_level = 3
        self.decoder = ort.InferenceSession(
            str(folder / "prompt_encoder_mask_decoder.onnx"), cpu, providers=["CPUExecutionProvider"]
        )
        self.provider = self.encoder.get_providers()[0]

    def _encode(self, view: np.ndarray) -> list[np.ndarray]:
        x = ((view.astype(np.float32) / 255 - _MEAN) / _STD).transpose(2, 0, 1)[None]
        return self.encoder.run(None, {"pixel_values": x})

    def silhouette(self, rgb: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray | None:
        """Contour (n, 2) en pixels de l'image de ce que contient `box`, ou None s'il est douteux."""
        x1, y1, x2, y2 = (float(v) for v in box)
        if x2 - x1 < 4 or y2 - y1 < 4:
            return None
        side = max(x2 - x1, y2 - y1) * GROW
        ax, ay = (x1 + x2) / 2 - side / 2, (y1 + y2) / 2 - side / 2
        k = INPUT / side
        matrix = np.float32([[k, 0, -ax * k], [0, k, -ay * k]])
        view = cv2.warpAffine(
            rgb, matrix, (INPUT, INPUT),
            flags=cv2.INTER_AREA if k < 1 else cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE,
        )
        emb = self._encode(view)
        prompt = np.float32([[[(x1 - ax) * k, (y1 - ay) * k, (x2 - ax) * k, (y2 - ay) * k]]])
        quality, masks, present = self.decoder.run(None, {
            "input_points": np.zeros((1, 1, 0, 2), dtype=np.float32),
            "input_labels": np.zeros((1, 1, 0), dtype=np.int64),
            "input_boxes": prompt,
            "image_embeddings.0": emb[0], "image_embeddings.1": emb[1], "image_embeddings.2": emb[2],
        })
        quality, masks = quality[0, 0], masks[0, 0]
        if float(present.ravel()[0]) < MIN_OBJECT_LOGIT or float(quality.max()) < MIN_QUALITY:
            return None
        # Parmi les propositions de bonne qualité, la plus étendue : l'objet entier plutôt
        # qu'une de ses parties (l'écran seul d'un compteur, par exemple).
        good = [i for i in range(len(quality)) if quality[i] >= quality.max() - 0.1]
        best = max(good, key=lambda i: int((masks[i] > 0).sum()))
        logits = cv2.resize(masks[best], (512, 512), interpolation=cv2.INTER_LINEAR)
        binary = (logits > 0).astype(np.uint8)
        found, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not found:
            return None
        biggest = max(cv2.contourArea(c) for c in found)
        parts = [c for c in found if cv2.contourArea(c) >= 0.05 * biggest]
        if biggest < 16:
            return None
        if len(parts) == 1:
            shape = parts[0]
            epsilon = 0.003 * cv2.arcLength(shape, True)
            while True:
                simple = cv2.approxPolyDP(shape, epsilon, True)
                if len(simple) <= MAX_POINTS:
                    break
                epsilon *= 1.5
            # Une simplification peut rogner un peu la forme : on prend son enveloppe si elle
            # perd plus de 2 % de la surface.
            if cv2.contourArea(simple) < 0.98 * cv2.contourArea(shape):
                simple = cv2.convexHull(shape)
        else:
            simple = cv2.convexHull(np.concatenate(parts))  # plusieurs morceaux : leur enveloppe
        points = simple.reshape(-1, 2).astype(np.float64) * (side / 512) + (ax, ay)
        # La silhouette doit correspondre à la boîte suivie.
        mx1, my1, mx2, my2 = points[:, 0].min(), points[:, 1].min(), points[:, 0].max(), points[:, 1].max()
        iw, ih = min(x2, mx2) - max(x1, mx1), min(y2, my2) - max(y1, my1)
        if iw <= 0 or ih <= 0:
            return None
        inter = iw * ih
        union = (x2 - x1) * (y2 - y1) + (mx2 - mx1) * (my2 - my1) - inter
        if inter / union < MIN_BOX_IOU or len(points) < 3:
            return None
        return points


def reference_pts(track: pj.Track, video: pj.VideoInfo) -> list[int]:
    """Instants (PTS d'observations) où calculer la silhouette : un tous les quarts de seconde,
    plus la première et la dernière image suivies."""
    pts = sorted(int(o[0]) for o in track.obs)
    if not pts:
        return []
    step = STEP_S / float(video.tb)
    chosen, last = [pts[0]], pts[0]
    for p in pts[1:-1]:
        if p - last >= step - 0.5 * float(video.frame_pts):
            chosen.append(p)
            last = p
    if pts[-1] != chosen[-1]:
        chosen.append(pts[-1])
    return chosen


def refine(
    project: pj.Project, clip: pj.Clip, tracks: list[pj.Track], progress: Progress | None = None,
    segmenter: Segmenter | None = None,
) -> dict:
    """Calcule le contour précis de pistes d'objet suivi d'un clip. Renvoie {"kept", "total"}.

    Les pistes ne sont modifiées qu'à la fin : une annulation en cours de route ne laisse rien
    à moitié fait.
    """
    video = clip.video
    wanted: dict[int, list[pj.Track]] = {}
    for track in tracks:
        for p in reference_pts(track, video):
            wanted.setdefault(p, []).append(track)
    total = sum(len(v) for v in wanted.values())
    if not total:
        return {"kept": 0, "total": 0}
    progress = progress or Progress("Contour précis", total, 0, unit="silhouettes")
    segmenter = segmenter or Segmenter()
    boxes = {t.id: {int(o[0]): (o[1], o[2], o[3], o[4]) for o in t.obs} for t in tracks}
    rows: dict[int, list[list[float]]] = {t.id: [] for t in tracks}
    moments = sorted(wanted)
    half = round(float(video.frame_pts) / 2)
    with av.open(str(project.clip_path(clip))) as container:
        index = 0
        for frame in iter_frames(container, moments[0] - half, moments[-1] + half + 1):
            while index < len(moments) and moments[index] < frame.pts - half:
                progress.step(len(wanted[moments[index]]))  # image absente du fichier
                index += 1
            if index >= len(moments):
                break
            if abs(frame.pts - moments[index]) > half:
                continue
            rgb = frame.to_ndarray(format="rgb24")
            for track in wanted[moments[index]]:
                x1, y1, x2, y2 = boxes[track.id][moments[index]]
                points = segmenter.silhouette(rgb, (x1, y1, x2, y2))
                if points is not None:
                    w, h = x2 - x1, y2 - y1
                    relative = np.column_stack(((points[:, 0] - x1) / w, (points[:, 1] - y1) / h))
                    rows[track.id].append([moments[index], *relative.ravel().tolist()])
                progress.step()
            index += 1
    kept = 0
    for track in tracks:
        track.outline = rows[track.id] or None
        kept += len(rows[track.id])
    return {"kept": kept, "total": total}


def clear(tracks: list[pj.Track]) -> None:
    for track in tracks:
        track.outline = None
