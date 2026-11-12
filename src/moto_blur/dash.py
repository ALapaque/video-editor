"""Floutage du compteur et de tout autre objet désigné à la main (téléphone, GPS…).

Chaque objet porte un nom ; plusieurs objets peuvent être suivis dans le même clip, chacun
indépendamment des autres. Deux modes sont implémentés :

1. Zone fixe : un rectangle donné par ses coordonnées, valable pour tout le clip. Convient à
   une caméra fixée à la moto avec un cadrage constant.
2. Suivi à partir d'un clic : on désigne le compteur sur une image, un modèle de suivi le suit
   image par image, vers l'avant et vers l'arrière. Le modèle est VitTrack (OpenCV, Apache-2.0,
   0,7 Mo) : il renvoie à chaque image un rectangle et un score de confiance. Quand le score
   chute, le suivi est considéré comme perdu : la dernière position sûre est maintenue et le
   passage est signalé pour vérification.

Le suivi travaille sur une image réduite (640 px de large) : le compteur est un objet grand,
la pleine résolution n'apporterait rien et coûterait du temps.
"""

from __future__ import annotations

from collections.abc import Iterator

import av
import cv2
import numpy as np

from . import intervals
from . import project as pj
from .models import ensure_model, get_spec
from .progress import Progress
from .timeline import kept_intervals, resolve_segments
from .video import iter_frames

TRACK_WIDTH = 640
LOST_SCORE = 0.30
BACKWARD_CHUNK_S = 3.0
PAD_S = 1.0


def dash_span(project: pj.Project, clip: pj.Clip) -> tuple[int, int] | None:
    """Intervalle de PTS du clip sur lequel le compteur doit être suivi.

    Du début du premier passage gardé à la fin du dernier, avec une marge. Les passages coupés
    situés entre deux passages gardés sont suivis aussi : le suivi a besoin de continuité.
    """
    kept = kept_intervals(resolve_segments(project), clip.id)
    if not kept:
        return None
    pad = round(PAD_S / clip.video.tb)
    return (max(clip.video.start_pts, kept[0][0] - pad), min(clip.video.end_pts, kept[-1][1] + pad))


def object_name(label: str | None) -> str:
    """Nom normalisé d'un objet suivi (« compteur » par défaut)."""
    return (label or "").strip()[:40] or pj.DEFAULT_OBJECT


def dash_tracks(
    project: pj.Project, clip: pj.Clip, source: str | None = None, label: str | None = None
) -> list[pj.Track]:
    """Pistes d'objets désignés à la main dans un clip, filtrées par origine et par nom."""
    state = project.analysis.get(clip.id)
    if not state:
        return []
    return [
        t for t in state.tracks
        if t.kind == "dash"
        and (source is None or t.source == source)
        and (label is None or t.name == object_name(label))
    ]


def object_names(project: pj.Project, clip: pj.Clip, source: str | None = None) -> list[str]:
    return list(dict.fromkeys(t.name for t in dash_tracks(project, clip, source)))


def missing_span(
    project: pj.Project, clip: pj.Clip, label: str | None = None
) -> list[intervals.Interval]:
    """Parties à suivre que le suivi ne couvre pas encore, pour un objet ou pour tous."""
    span = dash_span(project, clip)
    if span is None:
        return []
    names = [object_name(label)] if label is not None else object_names(project, clip, "tracker")
    step = round(clip.video.frame_pts * 1.5) + 1
    missing: list[intervals.Interval] = []
    for name in names:
        tracked = [t for t in dash_tracks(project, clip, "tracker", name) if t.obs]
        if not tracked:
            continue
        covered = [(int(t.obs[0][0]) - step, int(t.obs[-1][0]) + step) for t in tracked]
        missing += intervals.subtract([span], covered)
    return intervals.normalize(missing)


def set_fixed_zone(
    project: pj.Project, clip: pj.Clip, box: tuple[int, int, int, int], label: str | None = None
) -> pj.Track:
    """Mode 1 : zone fixe sur tout le clip. Remplace les zones existantes du même objet."""
    name = object_name(label)
    state = project.analysis.setdefault(clip.id, pj.ClipAnalysis())
    state.tracks = [t for t in state.tracks if not (t.kind == "dash" and t.name == name)]
    x1, y1, x2, y2 = box
    v = clip.video
    track = pj.Track(
        project.next_track_id(), "dash",
        [[v.start_pts, x1, y1, x2, y2, 1.0], [v.end_pts, x1, y1, x2, y2, 1.0]],
        source="manual", label=name,
    )
    state.tracks.append(track)
    return track


def clear_dash(project: pj.Project, clip: pj.Clip, label: str | None = None) -> int:
    """Supprime les zones d'un objet (ou de tous les objets si aucun nom n'est donné)."""
    state = project.analysis.get(clip.id)
    if not state:
        return 0
    before = len(state.tracks)
    state.tracks = [
        t for t in state.tracks
        if not (t.kind == "dash" and (label is None or t.name == object_name(label)))
    ]
    return before - len(state.tracks)


def _small(frame: av.VideoFrame, size: tuple[int, int]) -> np.ndarray:
    return frame.reformat(width=size[0], height=size[1], format="bgr24").to_ndarray()


def _new_tracker():
    params = cv2.TrackerVit_Params()
    params.net = str(ensure_model(get_spec("vittrack", "dash")))
    return cv2.TrackerVit_create(params)


def _reverse_frames(
    container, start_pts: int, end_pts: int, size: tuple[int, int], tb
) -> Iterator[tuple[int, np.ndarray]]:
    """Images réduites de [start_pts, end_pts), de la dernière à la première.

    Un fichier vidéo ne se décode que vers l'avant. On lit donc par morceaux de quelques
    secondes, en partant de la fin, et on renverse chaque morceau en mémoire.
    """
    chunk = max(1, round(BACKWARD_CHUNK_S / tb))
    hi = end_pts
    while hi > start_pts:
        lo = max(start_pts, hi - chunk)
        frames = [(f.pts, _small(f, size)) for f in iter_frames(container, lo, hi)]
        yield from reversed(frames)
        hi = lo


def _follow(tracker, frames, scale: float, clip: pj.Clip, progress: Progress | None) -> list[list[float]]:
    """Fait avancer le suivi sur une suite d'images réduites ; renvoie les observations."""
    obs = []
    last_good: list[float] | None = None
    w, h = clip.video.width, clip.video.height
    for pts, image in frames:
        _, (x, y, bw, bh) = tracker.update(image)
        score = float(tracker.getTrackingScore())
        box = [x / scale, y / scale, (x + bw) / scale, (y + bh) / scale]
        box = [min(max(box[0], 0), w), min(max(box[1], 0), h), min(max(box[2], 0), w), min(max(box[3], 0), h)]
        valid = box[2] - box[0] >= 2 and box[3] - box[1] >= 2
        if score >= LOST_SCORE and valid:
            last_good = box
        elif last_good is not None:
            box = last_good  # suivi perdu : on maintient la dernière position sûre
        if valid or last_good is not None:
            obs.append([int(pts), *box, score])
        if progress:
            progress.step()
    return obs


def track_from_click(
    project: pj.Project,
    clip: pj.Clip,
    seed_pts: int,
    box: tuple[float, float, float, float],
    direction: str = "both",
    progress: Progress | None = None,
    label: str | None = None,
) -> pj.Track:
    """Mode 2 : suit l'objet désigné par `box` (x1, y1, x2, y2) sur l'image `seed_pts`.

    `direction` : "both", "forward" (de ce point vers la fin) ou "backward". Les observations
    déjà présentes pour le MÊME objet sur la partie re-suivie sont remplacées ; les autres
    objets du clip ne sont pas touchés.
    """
    name = object_name(label)
    span = dash_span(project, clip)
    if span is None:
        raise ValueError(f"le clip {clip.id} n'a aucun passage gardé dans le montage.")
    seed_pts = min(max(int(seed_pts), span[0]), span[1] - 1)
    v = clip.video
    scale = TRACK_WIDTH / v.width
    size = (TRACK_WIDTH, max(2, round(v.height * scale / 2) * 2))
    init = (
        round(box[0] * scale), round(box[1] * scale),
        max(2, round((box[2] - box[0]) * scale)), max(2, round((box[3] - box[1]) * scale)),
    )
    obs: list[list[float]] = []
    with av.open(str(project.clip_path(clip))) as container:
        seed_frame = next(iter_frames(container, seed_pts, v.end_pts), None)
        if seed_frame is None:
            raise ValueError("image de départ introuvable.")
        seed_pts = seed_frame.pts
        seed_image = _small(seed_frame, size)
        obs.append([seed_pts, *[float(b) for b in box], 1.0])
        if direction in ("both", "forward"):
            tracker = _new_tracker()
            tracker.init(seed_image, init)
            step = round(v.frame_pts / 2) + 1
            forward = ((f.pts, _small(f, size)) for f in iter_frames(container, seed_pts + step, span[1]))
            obs += _follow(tracker, forward, scale, clip, progress)
        if direction in ("both", "backward"):
            tracker = _new_tracker()
            tracker.init(seed_image, init)
            obs += _follow(tracker, _reverse_frames(container, span[0], seed_pts, size, v.tb), scale, clip, progress)
    obs.sort(key=lambda o: o[0])
    lo, hi = obs[0][0], obs[-1][0]
    state = project.analysis.setdefault(clip.id, pj.ClipAnalysis())
    kept_tracks = []
    for t in state.tracks:
        same = t.kind == "dash" and t.name == name
        if same and t.source == "tracker":
            t.obs = [o for o in t.obs if o[0] < lo or o[0] > hi]
            if not t.obs:
                continue
        elif same and t.source == "manual" and len(t.obs) == 2 and t.obs[0][1:5] == t.obs[1][1:5]:
            continue  # une zone fixe est remplacée par le suivi
        kept_tracks.append(t)
    state.tracks = kept_tracks
    track = pj.Track(project.next_track_id(), "dash", obs, source="tracker", label=name)
    state.tracks.append(track)
    return track


def extend_tracking(project: pj.Project, clip: pj.Clip, progress: Progress | None = None) -> bool:
    """Prolonge le suivi de chaque objet sur les passages gardés ajoutés depuis. Vrai si prolongé."""
    done = False
    for name in object_names(project, clip, "tracker"):
        for lo, hi in missing_span(project, clip, name):
            tracked = [t for t in dash_tracks(project, clip, "tracker", name) if t.obs]
            first = min((t.obs[0] for t in tracked), key=lambda o: o[0])
            last = max((t.obs[-1] for t in tracked), key=lambda o: o[0])
            if lo >= last[0]:
                track_from_click(project, clip, int(last[0]), tuple(last[1:5]), "forward", progress, name)
            elif hi <= first[0] + clip.video.frame_pts * 2:
                track_from_click(project, clip, int(first[0]), tuple(first[1:5]), "backward", progress, name)
            else:
                continue
            done = True
    return done


def build_dash_review(clip: pj.Clip, tracks: list[pj.Track]) -> list[dict]:
    """Passages où le suivi du compteur a décroché (score sous le seuil)."""
    out = []
    for t in tracks:
        if t.kind != "dash" or t.source != "tracker" or t.disabled:
            continue
        run: list[list[float]] = []
        for o in [*t.obs, None]:
            if o is not None and o[5] < LOST_SCORE:
                run.append(o)
                continue
            if run:
                worst = min(r[5] for r in run)
                out.append(
                    {
                        "clip": clip.id,
                        "start_pts": int(run[0][0]),
                        "end_pts": int(run[-1][0]),
                        "kinds": ["dash"],
                        "reasons": [
                            f"suivi de l'objet « {t.name} » incertain (score {worst:.2f}) : "
                            "dernière position maintenue"
                        ],
                        "tracks": [t.id],
                    }
                )
                run = []
    return out
