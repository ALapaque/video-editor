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
    out = Path(out_dir) if out_dir else project.path.parent / f"{name}_entrainement"
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
                    images += 1
                    labels += len(lines)
                    negatives += not lines
    (out / "data.yaml").write_text(
        f"path: {out.as_posix()}\ntrain: images\nval: images\nnames:\n  0: plaque\n", encoding="utf-8"
    )
    (out / "LISEZMOI.txt").write_text(README, encoding="utf-8")
    return {"path": str(out), "images": images, "labels": labels, "negatives": negatives}
