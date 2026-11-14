"""Images d'entraînement tirées d'un projet vérifié (modèle de plaques personnalisé).

Quand un projet a été vérifié dans l'interface, il contient exactement ce qu'il faut pour
apprendre à un modèle les plaques telles qu'elles apparaissent dans VOS vidéos :
- les plaques gardées (floutées) sont des exemples positifs ;
- les détections retirées à la main (« ce n'est pas à flouter ») sont des contre-exemples.

Ce module découpe les images en tuiles, comme le fait la détection, et écrit un jeu de données
au format YOLO. Rien n'est envoyé nulle part : les images restent sur cette machine. Elles
montrent des plaques NON floutées : ne les publiez pas.

L'entraînement lui-même ne fait pas partie de moto-blur : voir docs/ENTRAINEMENT.md.
"""

from __future__ import annotations

import bisect
from pathlib import Path

import av
import cv2
import numpy as np

from . import project as pj
from .detect import make_crops
from .models import get_spec
from .postprocess import is_blurred
from .progress import Progress
from .video import iter_frames

MIN_VISIBLE = 0.4  # part d'une plaque qui doit être dans la tuile pour y être étiquetée
README = """Images d'entraînement moto-blur (plaques)
===========================================

images/  tuiles découpées dans vos vidéos (plaques NON floutées : ne publiez pas ce dossier)
labels/  une ligne par plaque : « 0 centre_x centre_y largeur hauteur » (valeurs de 0 à 1)
train.txt, val.txt  images servant à entraîner le modèle, et images gardées pour le mesurer
data.yaml  description du jeu de données pour l'entraînement

Les tuiles sans plaque sont voulues : elles apprennent au modèle ce qui n'est PAS une plaque
(dont les fausses détections que vous avez retirées pendant la vérification).

Suite : docs/ENTRAINEMENT.md dans le dépôt de moto-blur.
"""


def _box_at(track: pj.Track, pts: int) -> tuple[float, float, float, float] | None:
    """Boîte observée de la piste à `pts` (interpolée entre deux observations), None hors piste."""
    times = [o[0] for o in track.obs]
    if not times or pts < times[0] or pts > times[-1]:
        return None
    i = bisect.bisect_right(times, pts) - 1
    a = track.obs[i]
    if a[0] == pts or i >= len(times) - 1:
        return (a[1], a[2], a[3], a[4])
    b = track.obs[i + 1]
    w = (pts - a[0]) / (b[0] - a[0])
    return tuple(a[k] + (b[k] - a[k]) * w for k in range(1, 5))  # type: ignore[return-value]


def _sample(track: pj.Track, step: float) -> list[int]:
    out, last = [], None
    for o in track.obs:
        if last is None or o[0] - last >= step:
            out.append(int(o[0]))
            last = o[0]
    return out


def export_dataset(
    project: pj.Project, out_dir: Path | None = None, every_s: float = 0.5,
    progress: Progress | None = None,
) -> dict:
    """Écrit le jeu de données. Renvoie {"path", "images", "labels", "negatives"}."""
    if project.path is None:
        raise ValueError("le projet doit être enregistré.")
    name = project.path.name.replace(".motoblur.json", "").replace(".json", "")
    out = (Path(out_dir) if out_dir else project.path.parent / f"{name}_entrainement").resolve()
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    spec = get_spec(project.analysis_settings.plate_model, "plate")
    blur = project.blur_settings
    plan = []
    for clip in project.clips:
        state = project.analysis.get(clip.id)
        if not state or not project.clip_path(clip).exists():
            continue
        plates = [t for t in state.tracks if t.kind == "plate" and t.source == "auto" and t.obs]
        kept = [t for t in plates if is_blurred(t, blur)]
        rejected = [t for t in plates if t.disabled]
        # Ni gardées ni retirées (score trop bas, jamais examinées), et zones ajoutées à la main
        # dont on ignore ce qu'elles cachent : on n'apprend rien des tuiles qui les contiennent.
        unsure = [t for t in plates if not t.disabled and not is_blurred(t, blur)]
        unsure += [t for t in state.tracks if t.kind == "manual" and t.obs]
        step = every_s / float(clip.video.tb)
        moments = sorted({p for t in kept + rejected for p in _sample(t, step)})
        if moments:
            plan.append((clip, kept, rejected, unsure, moments))
    total = sum(len(p[4]) for p in plan)
    if not total:
        raise ValueError(
            "aucune plaque vérifiée dans ce projet : analysez la vidéo et vérifiez les flous d'abord."
        )
    progress = progress or Progress("Images d'entraînement", total, 0, unit="images")
    images = labels = negatives = 0
    rng = np.random.default_rng(0)
    lists: dict[str, list[str]] = {"train": [], "val": []}
    moment_index = 0
    for clip, kept, rejected, unsure, moments in plan:
        v = clip.video
        # Mêmes tuiles que la détection (l'image entière si elle n'est pas découpée).
        crops = make_crops(v.width, v.height, spec.input_size, project.analysis_settings.tile_size, 0.2, False)
        half = round(float(v.frame_pts) / 2)
        with av.open(str(project.clip_path(clip))) as container:
            index = 0
            for frame in iter_frames(container, moments[0] - half, moments[-1] + half + 1):
                while index < len(moments) and moments[index] < frame.pts - half:
                    index += 1
                    progress.step()
                if index >= len(moments):
                    break
                if abs(frame.pts - moments[index]) > half:
                    continue
                pts = moments[index]
                index += 1
                progress.step()
                # Une image sur cinq sert à mesurer le modèle (jamais à l'entraîner) ; toutes
                # les tuiles d'une même image vont du même côté.
                subset = "val" if moment_index % 5 == 4 else "train"
                moment_index += 1
                bgr = frame.to_ndarray(format="bgr24")
                good = [b for t in kept if (b := _box_at(t, pts))]
                bad = [b for t in rejected if (b := _box_at(t, pts))]
                doubt = [b for t in unsure if (b := _box_at(t, pts))]
                chosen = []
                for crop in crops:
                    cx1, cy1, cx2, cy2 = crop.x0, crop.y0, crop.x0 + crop.w, crop.y0 + crop.h

                    def inside(b, cx1=cx1, cy1=cy1, cx2=cx2, cy2=cy2):
                        w = min(b[2], cx2) - max(b[0], cx1)
                        h = min(b[3], cy2) - max(b[1], cy1)
                        area = max(1.0, (b[2] - b[0]) * (b[3] - b[1]))
                        return max(0.0, w) * max(0.0, h) / area

                    if any(inside(b) > 0 for b in doubt):
                        continue
                    parts = [inside(b) for b in good]
                    if any(0 < p < MIN_VISIBLE for p in parts):
                        continue  # plaque coupée par le bord : ni exemple ni contre-exemple
                    lines = []
                    for b, p in zip(good, parts, strict=True):
                        if p >= MIN_VISIBLE:
                            x1, y1 = max(b[0], cx1) - cx1, max(b[1], cy1) - cy1
                            x2, y2 = min(b[2], cx2) - cx1, min(b[3], cy2) - cy1
                            lines.append(
                                f"0 {(x1 + x2) / 2 / crop.w:.6f} {(y1 + y2) / 2 / crop.h:.6f} "
                                f"{(x2 - x1) / crop.w:.6f} {(y2 - y1) / crop.h:.6f}"
                            )
                    hard = any(inside(b) >= MIN_VISIBLE for b in bad)
                    chosen.append((crop, lines, hard))
                positives = [c for c in chosen if c[1]]
                hard = [c for c in chosen if not c[1] and c[2]]
                plain = [c for c in chosen if not c[1] and not c[2]]
                # Autant de tuiles sans plaque que de tuiles avec, les fausses détections d'abord.
                quota = max(1, len(positives)) - len(hard)
                if quota > 0 and plain:
                    picks = rng.choice(len(plain), size=min(quota, len(plain)), replace=False)
                    hard += [plain[int(i)] for i in picks]
                for crop, lines, _ in positives + hard:
                    stem = f"{name}_{clip.id}_{pts}_{crop.x0}_{crop.y0}"
                    tile = bgr[crop.y0 : crop.y0 + crop.h, crop.x0 : crop.x0 + crop.w]
                    cv2.imwrite(str(out / "images" / f"{stem}.jpg"), tile, [cv2.IMWRITE_JPEG_QUALITY, 92])
                    (out / "labels" / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
                    lists[subset].append(f"./images/{stem}.jpg")
                    images += 1
                    labels += len(lines)
                    negatives += not lines
    for subset, paths in lists.items():
        # Un même dossier peut recevoir plusieurs projets : les listes se complètent.
        file = out / f"{subset}.txt"
        known = file.read_text(encoding="utf-8").split() if file.exists() else []
        lists[subset] = list(dict.fromkeys(known + paths))
    if not lists["val"]:  # trop peu d'images pour en réserver : mesure sur les mêmes
        lists["val"] = lists["train"]
    for subset, paths in lists.items():
        (out / f"{subset}.txt").write_text("\n".join(paths) + "\n", encoding="utf-8")
    (out / "data.yaml").write_text(
        f"path: {out.as_posix()}\ntrain: train.txt\nval: val.txt\nnames:\n  0: plaque\n", encoding="utf-8"
    )
    (out / "LISEZMOI.txt").write_text(README, encoding="utf-8")
    return {"path": str(out), "images": images, "labels": labels, "negatives": negatives}


# --- Entraînement : une seule commande -------------------------------------------------------
#
# L'entraînement utilise Ultralytics (licence AGPL-3.0), qui n'est pas une dépendance de
# moto-blur : il est lancé à part, par « uvx », qui l'installe dans un coin la première fois
# (téléchargement de plusieurs centaines de Mo, puis du modèle de départ). C'est un outil que
# vous lancez exprès : l'analyse et le rendu, eux, ne font toujours aucun appel réseau.

RUNNER = ["uvx", "--from", "ultralytics", "--with", "onnx", "--with", "onnxslim", "--with", "onnxruntime", "yolo"]
MIN_PLATES = 50  # en dessous, l'entraînement n'a aucune chance d'apporter quoi que ce soit


def _runner() -> list[str]:
    import os
    import shlex

    custom = os.environ.get("MOTO_BLUR_TRAIN_RUNNER")
    return shlex.split(custom) if custom else list(RUNNER)


def train_device() -> str:
    """Accélération pour l'entraînement : la puce Apple si elle est là, sinon le processeur
    (PyTorch ne sait pas utiliser simplement une Radeon)."""
    import platform

    return "mps" if platform.system() == "Darwin" and platform.machine() == "arm64" else "cpu"


def train(
    projects: list[pj.Project], name: str, epochs: int = 80, base: str = "yolo11s",
    device: str = "auto", imgsz: int = 640, out_dir: Path | None = None, force: bool = False,
) -> dict:
    """Exporte les images des projets vérifiés, entraîne un modèle de plaques, le convertit et
    l'installe comme modèle personnel. Renvoie {"model", "path", "dataset", "images", "labels"}."""
    import shutil
    import subprocess

    from .models import custom_dir, get_spec

    if not name or any(c in name for c in "/\\ ") or name.endswith(".onnx"):
        raise ValueError("donnez un nom simple au modèle, sans espace ni barre : mes-plaques, par exemple.")
    if not base.startswith("yolo11"):
        raise ValueError("modèle de départ : yolo11n, yolo11s ou yolo11m (moto-blur lit la sortie de cette famille).")
    if not projects or projects[0].path is None:
        raise ValueError("indiquez au moins un projet vérifié.")
    out = (Path(out_dir) if out_dir else projects[0].path.parent / f"entrainement_{name}").resolve()
    totals = {"images": 0, "labels": 0}
    for project in projects:
        result = export_dataset(project, out)
        totals["images"] += result["images"]
        totals["labels"] += result["labels"]
    if totals["labels"] < MIN_PLATES and not force:
        raise ValueError(
            f"seulement {totals['labels']} plaques vérifiées dans ces projets : c'est trop peu pour "
            f"entraîner un modèle (il en faut au moins {MIN_PLATES}, et plutôt plusieurs centaines, "
            "prises sur plusieurs sorties). Ajoutez des projets vérifiés."
        )
    device = train_device() if device == "auto" else device
    runs = out / "runs"
    common = _runner()
    print(f"\nEntraînement sur {totals['images']} images ({totals['labels']} plaques), {epochs} passes, sur « {device} ».")
    print("La première fois, l'outil d'entraînement est téléchargé (plusieurs centaines de Mo).\n")
    subprocess.run(
        [*common, "detect", "train", f"data={out / 'data.yaml'}", f"model={base}.pt", f"imgsz={imgsz}",
         f"epochs={epochs}", f"device={device}", f"project={runs}", f"name={name}", "exist_ok=True",
         "plots=False", "single_cls=True"],
        check=True, cwd=out,
    )
    best = runs / name / "weights" / "best.pt"
    if not best.exists():
        raise RuntimeError(f"l'entraînement n'a pas produit de modèle ({best}).")
    subprocess.run(
        [*common, "export", f"model={best}", "format=onnx", "dynamic=True", f"imgsz={imgsz}", "opset=17"],
        check=True, cwd=out,
    )
    exported = best.with_suffix(".onnx")
    if not exported.exists():
        raise RuntimeError(f"la conversion n'a pas produit de fichier ({exported}).")
    target = custom_dir("plate") / f"{name}.onnx"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(exported, target)
    get_spec(name, "plate")  # vérifie qu'il est bien reconnu comme modèle personnel
    return {"model": name, "path": str(target), "dataset": str(out), **totals}
