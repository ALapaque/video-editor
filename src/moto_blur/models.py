"""Registre des modèles de détection et téléchargement vérifié.

C'est le SEUL endroit du logiciel qui fait un appel réseau : le téléchargement
d'un modèle absent, avec vérification de son empreinte SHA-256. L'analyse et le
rendu ne font aucun appel réseau. Licences : voir MODELS.md.
"""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_HF = "https://huggingface.co/deepghs/yolo-face/resolve/e3662574830c534dfcc9c3b7ea4d89272f8aae4e"
_OIM = "https://github.com/ankandrew/open-image-models/releases/download/assets"


@dataclass(frozen=True)
class ModelSpec:
    name: str
    kind: str  # "face" | "plate"
    url: str
    sha256: str
    size_mb: float
    input_size: int
    # "ultralytics" : sortie brute (lot, 4+classes, N), boîtes cx,cy,w,h ; NMS fait par nous.
    # "end2end"     : NMS intégré au modèle, sortie (N, 7) = lot,x1,y1,x2,y2,classe,score.
    output: str
    dynamic: bool  # dimensions d'entrée libres (lot, hauteur, largeur)
    license: str


MODELS: dict[str, ModelSpec] = {
    m.name: m
    for m in [
        ModelSpec(
            "yolo11n-face", "face", f"{_HF}/yolov11n-face/model.onnx",
            "7b4b3a098ac15a5ccd0d048e7949b63275e4a9657c374ad9e4730632d9f01829",
            10.0, 640, "ultralytics", True, "AGPL-3.0",
        ),
        ModelSpec(
            "yolo11s-face", "face", f"{_HF}/yolov11s-face/model.onnx",
            "de2d0582451a323af9a0fb05da70f32b4f26fcf411657c2dd491037ecd18efe6",
            36.1, 640, "ultralytics", True, "AGPL-3.0",
        ),
        ModelSpec(
            "yolo11m-face", "face", f"{_HF}/yolov11m-face/model.onnx",
            "0136c7938193241ebde248abc0678455c01120976dacea425af53d72fc4a70a3",
            76.6, 640, "ultralytics", True, "AGPL-3.0",
        ),
        ModelSpec(
            "yolo-v9-t-640", "plate", f"{_OIM}/yolo-v9-t-640-license-plates-end2end.onnx",
            "c3c1026ca7d0585dd88084d68182dd897113712fa734ae1557ca70174440c076",
            7.5, 640, "end2end", False, "MIT (dépôt) — licence des poids à confirmer",
        ),
        ModelSpec(
            "yolo-v9-s-608", "plate", f"{_OIM}/yolo-v9-s-608-license-plates-end2end.onnx",
            "2b878b38d9aa07b6ddc3ea75c4ffcb39869bc5c218e0a14002f60ab2f7b0be9a",
            27.3, 608, "end2end", False, "MIT (dépôt) — licence des poids à confirmer",
        ),
    ]
}


def models_dir() -> Path:
    env = os.environ.get("MOTO_BLUR_MODELS")
    return Path(env) if env else Path.home() / ".moto-blur" / "models"


def get_spec(name: str, kind: str) -> ModelSpec:
    spec = MODELS.get(name)
    if spec is None or spec.kind != kind:
        choices = ", ".join(m.name for m in MODELS.values() if m.kind == kind)
        raise ValueError(f"modèle inconnu pour « {kind} » : {name}. Choix possibles : {choices}")
    return spec


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_model(spec: ModelSpec) -> Path:
    """Renvoie le chemin local du modèle ; le télécharge s'il est absent."""
    path = models_dir() / f"{spec.name}.onnx"
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Téléchargement du modèle {spec.name} ({spec.size_mb:.0f} Mo)…", file=sys.stderr)
    tmp = path.with_suffix(".part")
    request = urllib.request.Request(spec.url, headers={"User-Agent": "moto-blur"})
    with urllib.request.urlopen(request) as response, open(tmp, "wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    digest = _sha256(tmp)
    if digest != spec.sha256:
        tmp.unlink()
        raise RuntimeError(
            f"empreinte inattendue pour {spec.name} : {digest} (attendu {spec.sha256}). "
            "Le fichier téléchargé a été supprimé."
        )
    os.replace(tmp, path)
    return path
