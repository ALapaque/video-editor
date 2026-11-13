"""Contre-vérification des objets douteux : moins de flous absurdes (arbre, reflet, sac…).

Pendant l'analyse, l'image est découpée en tuiles et chaque objet y est petit. Une texture
quelconque peut alors ressembler, de loin, à un visage ou à une plaque. Ici, chaque objet
DOUTEUX (score habituel faible) est remontré au même détecteur, en plus grand, sur ses
meilleures images : un vrai visage est retrouvé, une fausse détection ne l'est en général pas.

Prudence (exigence « zéro fuite ») :
- seuls les objets douteux sont réexaminés ; un objet sûr n'est jamais remis en cause ;
- le grossissement reste modéré (l'objet est ramené à 32 puis 56 pixels) : trop grossi, un
  visage flou n'est plus reconnu non plus ;
- il suffit d'UNE vue où l'objet est retrouvé, même faiblement, pour le garder ;
- un objet non retrouvé n'est pas supprimé : il n'est plus flouté, mais reste listé parmi les
  objets non floutés, d'où l'on peut le flouter quand même.

Mesure sur la scène de rue d'essai (124 objets floutés, surtout de petits visages) : 5 fausses
détections écartées (pare-brise, vitre, intérieur de voiture, nuque), aucun vrai visage perdu.
Jamais mesuré sur de vraies vidéos de moto : le réglage se coupe dans les réglages de flou.
"""

from __future__ import annotations

import av
import cv2
import numpy as np

from . import project as pj
from .detect import Detector
from .progress import Progress
from .video import iter_frames

DOUBT_SCORE = 0.40  # score habituel (médian) en dessous duquel un objet est réexaminé
SAMPLES = 3  # meilleures images de l'objet à revoir
ZOOMS = (32, 56)  # taille donnée à l'objet, en pixels, dans la vue remontrée au modèle
MIN_OVERLAP = 0.3  # recouvrement exigé entre ce qui est retrouvé et l'objet d'origine
RUN_GAP_S = 2.0  # au-delà, on saute dans la vidéo au lieu de décoder tout l'intervalle


def is_doubtful(track: pj.Track) -> bool:
    if track.source != "auto" or not track.obs:
        return False
    return float(np.median([o[5] for o in track.obs])) < DOUBT_SCORE


def pending(project: pj.Project, clip: pj.Clip) -> list[pj.Track]:
    """Objets douteux du clip à réexaminer : pas encore revus, et qui seraient floutés.

    Un objet déjà écarté par son score, ou retiré à la main, n'a pas à être revu."""
    from .postprocess import is_blurred

    state = project.analysis.get(clip.id)
    if not state or not project.blur_settings.confirm:
        return []
    kinds = project.analysis_settings.kinds  # une catégorie qu'on n'analyse plus n'est pas revue
    blur = project.blur_settings
    return [
        t for t in state.tracks
        if t.confirm is None and t.kind in kinds and is_doubtful(t) and is_blurred(t, blur)
    ]


def due(project: pj.Project, clip: pj.Clip) -> bool:
    """Vrai si la contre-vérification n'a encore jamais été faite pour ce clip et qu'il y a de
    quoi faire. Une fois faite, on ne redemande pas d'analyse pour un réglage de flou modifié :
    les objets qui deviendraient floutés sans avoir été revus le sont à la prochaine analyse."""
    state = project.analysis.get(clip.id)
    return bool(state) and not state.confirmed and bool(pending(project, clip))


def zoom_view(rgb: np.ndarray, box, size: int, target: int) -> tuple[np.ndarray, tuple[float, ...]]:
    """Vue carrée centrée sur l'objet, où il mesure `target` pixels, et sa boîte dans cette vue."""
    x1, y1, x2, y2 = (float(v) for v in box)
    side = max(size / target * max(x2 - x1, y2 - y1), 64.0)
    ax, ay = (x1 + x2) / 2 - side / 2, (y1 + y2) / 2 - side / 2
    k = size / side
    view = cv2.warpAffine(
        rgb, np.float32([[k, 0, -ax * k], [0, k, -ay * k]]), (size, size),
        flags=cv2.INTER_CUBIC if k > 1 else cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE,
    )
    return view, ((x1 - ax) * k, (y1 - ay) * k, (x2 - ax) * k, (y2 - ay) * k)


def _found(detections: np.ndarray, box: tuple[float, ...]) -> float:
    """Meilleur score d'une détection qui recouvre l'objet d'origine dans la vue."""
    best = 0.0
    for d in detections:
        w = min(d[2], box[2]) - max(d[0], box[0])
        h = min(d[3], box[3]) - max(d[1], box[1])
        if w <= 0 or h <= 0:
            continue
        union = (d[2] - d[0]) * (d[3] - d[1]) + (box[2] - box[0]) * (box[3] - box[1]) - w * h
        if w * h / union >= MIN_OVERLAP:
            best = max(best, float(d[4]))
    return best


def confirm_tracks(
    project: pj.Project, clip: pj.Clip, detectors: dict[str, Detector],
    progress: Progress | None = None,
) -> dict:
    """Réexamine les objets douteux du clip. Renvoie {"examined", "unconfirmed"}."""
    video = clip.video
    tracks = [t for t in pending(project, clip) if t.kind in detectors]
    if not tracks:
        return {"examined": 0, "unconfirmed": 0}
    wanted: dict[int, list[tuple[pj.Track, list[float]]]] = {}
    for t in tracks:
        for o in sorted(t.obs, key=lambda o: -o[5])[:SAMPLES]:
            wanted.setdefault(int(o[0]), []).append((t, o))
    moments = sorted(wanted)
    progress = progress or Progress(f"Contre-vérification {clip.id}", len(moments), 0, unit="images")
    best: dict[int, float] = {t.id: 0.0 for t in tracks}
    half = round(float(video.frame_pts) / 2)
    gap = RUN_GAP_S / float(video.tb)
    runs: list[list[int]] = []
    for m in moments:
        if runs and m - runs[-1][-1] <= gap:
            runs[-1].append(m)
        else:
            runs.append([m])
    with av.open(str(project.clip_path(clip))) as container:
        for run in runs:
            index = 0
            for frame in iter_frames(container, run[0] - half, run[-1] + half + 1):
                while index < len(run) and run[index] < frame.pts - half:
                    index += 1
                    progress.step()
                if index >= len(run):
                    break
                if abs(frame.pts - run[index]) > half:
                    continue
                rgb = frame.to_ndarray(format="rgb24")
                by_kind: dict[str, list] = {}
                for t, o in wanted[run[index]]:
                    for target in ZOOMS:
                        view, box = zoom_view(rgb, o[1:5], detectors[t.kind].spec.input_size, target)
                        by_kind.setdefault(t.kind, []).append((t, view, box))
                for kind, items in by_kind.items():
                    results = detectors[kind].examine([view for _, view, _ in items])
                    for (t, _, box), detections in zip(items, results, strict=True):
                        best[t.id] = max(best[t.id], _found(detections, box))
                index += 1
                progress.step()
    minimum = project.blur_settings.confirm_min
    for t in tracks:
        t.confirm = round(best[t.id], 3)
    return {"examined": len(tracks), "unconfirmed": sum(1 for t in tracks if best[t.id] < minimum)}
