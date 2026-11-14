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
from dataclasses import dataclass, replace
from pathlib import Path

from . import names
from .progress import Progress

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
        ModelSpec(
            "vittrack", "dash",
            "https://huggingface.co/opencv/object_tracking_vittrack/resolve/"
            "868e4c941984c6363594ec34ee1c3ba0b75378a3/object_tracking_vittrack_2023sep.onnx",
            "2990f0b7cd44d92afa48cd97db6de7be113fc1d9594fddb74e2725c10478e91d",
            0.7, 0, "tracker", False, "Apache-2.0",
        ),
        ModelSpec(
            "yolox-s", "context",
            "https://huggingface.co/opencv/object_detection_yolox/resolve/"
            "78c368f74ce73ee28fc7a1be418a598c71b58b52/object_detection_yolox_2022nov.onnx",
            "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063",
            35.9, 640, "yolox", False, "Apache-2.0",
        ),
    ]
}


def models_dir() -> Path:
    custom = names.env("MODELS")
    return Path(custom) if custom else names.home() / "models"


def custom_dir(kind: str) -> Path:
    """Dossier des modèles ajoutés par l'utilisateur pour une catégorie (« plate », « face »)."""
    return models_dir() / "perso" / kind


def custom_models(kind: str) -> list[str]:
    try:
        return sorted(p.stem for p in custom_dir(kind).glob("*.onnx") if not p.name.endswith(".lot.onnx"))
    except OSError:
        return []


def available(kind: str) -> list[str]:
    """Modèles utilisables pour une catégorie : ceux de Flouto, puis ceux de l'utilisateur."""
    return [m.name for m in MODELS.values() if m.kind == kind] + custom_models(kind)


def _custom_spec(name: str, kind: str) -> ModelSpec | None:
    """Modèle personnel : un fichier ONNX exporté par Ultralytics (une seule classe), posé dans
    `~/.flouto/models/perso/<catégorie>/<nom>.onnx`. Jamais téléchargé."""
    path = custom_dir(kind) / f"{name}.onnx"
    if not path.is_file():
        return None
    size, dynamic = 640, True
    try:
        import onnx

        dims = onnx.load(str(path), load_external_data=False).graph.input[0].type.tensor_type.shape.dim
        dynamic = not all(d.HasField("dim_value") and d.dim_value > 0 for d in dims)
        if len(dims) == 4 and dims[2].HasField("dim_value") and dims[2].dim_value > 0:
            size = int(dims[2].dim_value)
    except Exception:  # noqa: BLE001 - en-tête illisible : valeurs habituelles
        pass
    return ModelSpec(name, kind, "", "", path.stat().st_size / 1e6, size, "ultralytics", dynamic, "modèle personnel")


def get_spec(name: str, kind: str) -> ModelSpec:
    spec = MODELS.get(name)
    if spec is None or spec.kind != kind:
        spec = _custom_spec(name, kind)
    if spec is None:
        choices = ", ".join(available(kind))
        raise ValueError(f"modèle inconnu pour « {kind} » : {name}. Choix possibles : {choices}")
    return spec


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def batchable(spec: ModelSpec, path: Path) -> tuple[ModelSpec, Path]:
    """Version du modèle qui traite toutes les tuiles d'une image en un seul appel.

    Les modèles de plaques sont publiés avec la suppression des doublons intégrée et une seule
    image par appel : il fallait donc un appel par tuile. On en extrait ici la partie détection
    (sortie brute, avant suppression des doublons, faite ensuite par Flouto comme pour les
    visages) et on libère la taille du lot. La conversion est faite une fois, sur cette machine,
    à partir du fichier téléchargé ; le résultat est gardé à côté de lui.

    En cas d'échec, le modèle d'origine est renvoyé tel quel.
    """
    if spec.output != "end2end":
        return spec, path
    target = path.with_name(f"{spec.name}.lot.onnx")
    converted = replace(spec, output="ultralytics", dynamic=True)
    if target.exists():
        return converted, target
    try:
        import onnx
        from onnx import numpy_helper

        model = onnx.load(str(path))
        produced = {o: n for n in model.graph.node for o in n.output}
        # Sortie brute : le tenseur que le bloc « end2end » reçoit du détecteur.
        raw = [
            i for n in model.graph.node if n.name.startswith("/end2end") for i in n.input
            if i in produced and not produced[i].name.startswith("/end2end")
        ]
        if len(set(raw)) != 1:
            raise ValueError("sortie brute introuvable")
        partial = target.with_suffix(".part")
        onnx.utils.extract_model(str(path), str(partial), [model.graph.input[0].name], [raw[0]])
        model = onnx.load(str(partial))
        graph = model.graph
        graph.input[0].type.tensor_type.shape.dim[0].dim_param = "batch"
        for output in graph.output:
            output.type.tensor_type.shape.Clear()
        del graph.value_info[:]
        # Les « Reshape » figés sur un lot de 1 : 0 signifie « garder la taille d'entrée ».
        initializers = {i.name: i for i in graph.initializer}
        constants = {n.output[0]: n for n in graph.node if n.op_type == "Constant"}
        for node in graph.node:
            if node.op_type != "Reshape":
                continue
            name = node.input[1]
            if name in initializers:
                shape = numpy_helper.to_array(initializers[name]).copy()
                if shape.size and shape[0] == 1:
                    shape[0] = 0
                    initializers[name].CopyFrom(numpy_helper.from_array(shape, name))
            elif name in constants:
                shape = numpy_helper.to_array(constants[name].attribute[0].t).copy()
                if shape.size and shape[0] == 1:
                    shape[0] = 0
                    constants[name].attribute[0].t.CopyFrom(numpy_helper.from_array(shape))
        onnx.save(model, str(partial))
        os.replace(partial, target)
        return converted, target
    except Exception as exc:  # noqa: BLE001 - la conversion est une optimisation, jamais un blocage
        print(f"Conversion de {spec.name} impossible ({exc}) : une tuile par appel.", file=sys.stderr)
        return spec, path


def _download(url: str, target: Path, sha256: str, label: str, size_mb: float) -> None:
    """Télécharge un fichier et vérifie son empreinte ; rien n'est gardé si elle ne correspond pas."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "flouto"})
    with urllib.request.urlopen(request) as response, open(tmp, "wb") as out:
        total = int(response.headers.get("Content-Length") or size_mb * 1e6)
        progress = Progress(f"Téléchargement du modèle {label}", max(1, total // 100_000), 0, unit="x 100 ko")
        while chunk := response.read(100_000):
            out.write(chunk)
            progress.step()
    digest = _sha256(tmp)
    if digest != sha256:
        tmp.unlink()
        raise RuntimeError(
            f"empreinte inattendue pour {label} : {digest} (attendu {sha256}). "
            "Le fichier téléchargé a été supprimé."
        )
    os.replace(tmp, target)


def ensure_files(name: str, files: list[tuple[str, str, str, float]]) -> Path:
    """Modèle en plusieurs fichiers (nom, adresse, empreinte, taille en Mo) : renvoie son dossier,
    après avoir téléchargé et vérifié ce qui manque."""
    folder = models_dir() / name
    for filename, url, sha256, size_mb in files:
        if not (folder / filename).exists():
            _download(url, folder / filename, sha256, f"{name} ({filename})", size_mb)
    return folder


def ensure_model(spec: ModelSpec) -> Path:
    """Renvoie le chemin local du modèle ; le télécharge s'il est absent."""
    if not spec.url:  # modèle personnel : il est là où l'utilisateur l'a posé
        return custom_dir(spec.kind) / f"{spec.name}.onnx"
    path = models_dir() / f"{spec.name}.onnx"
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    request = urllib.request.Request(spec.url, headers={"User-Agent": "flouto"})
    with urllib.request.urlopen(request) as response, open(tmp, "wb") as out:
        total = int(response.headers.get("Content-Length") or spec.size_mb * 1e6)
        steps = max(1, total // 100_000)
        progress = Progress(f"Téléchargement du modèle {spec.name}", steps, 0, unit="x 100 ko")
        while chunk := response.read(100_000):
            out.write(chunk)
            progress.step()
    sys.stderr.write("\r" + " " * 100 + "\r")
    print(f"Modèle {spec.name} téléchargé ({spec.size_mb:.0f} Mo).", file=sys.stderr)
    digest = _sha256(tmp)
    if digest != spec.sha256:
        tmp.unlink()
        raise RuntimeError(
            f"empreinte inattendue pour {spec.name} : {digest} (attendu {spec.sha256}). "
            "Le fichier téléchargé a été supprimé."
        )
    os.replace(tmp, path)
    return path
