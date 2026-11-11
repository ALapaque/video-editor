"""Passe 1 — analyse : détection et suivi, résultat écrit dans le fichier projet.

Seuls les passages gardés par le montage sont analysés (avec une petite marge
autour), et seulement s'ils ne l'ont pas déjà été : rallonger un passage après
coup ne réanalyse que la partie manquante. L'analyse étant indexée sur le temps
source, modifier une coupe ne l'invalide jamais.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable

import av
import numpy as np

from . import intervals
from . import project as pj
from .detect import Detector
from .models import ensure_model, get_spec
from .postprocess import build_review
from .progress import Progress
from .timeline import kept_intervals, resolve_segments
from .tracking import Tracker
from .video import iter_frames

# Marge analysée autour de chaque passage gardé : les flous sont prolongés avant et
# après chaque objet, il faut donc connaître les objets juste au-delà des coupes.
ANALYSIS_PAD_S = 1.0

DetectFn = Callable[[np.ndarray], dict[str, np.ndarray]]


def needed_intervals(project: pj.Project, clip: pj.Clip) -> list[intervals.Interval]:
    """Intervalles de PTS du clip qui doivent être analysés et ne le sont pas encore."""
    video = clip.video
    kept = kept_intervals(resolve_segments(project), clip.id)
    pad = round(ANALYSIS_PAD_S / video.tb)
    wanted = intervals.pad(kept, pad, video.start_pts, video.end_pts)
    state = project.analysis.get(clip.id)
    if state is None or state.signature != project.analysis_settings.signature():
        return wanted
    return intervals.subtract(wanted, [tuple(i) for i in state.analyzed])


def make_detect_fn(project: pj.Project, clip: pj.Clip, device: str) -> tuple[DetectFn, list[str]]:
    """Charge les modèles demandés et renvoie la fonction image -> détections par catégorie."""
    s = project.analysis_settings
    size = (clip.video.width, clip.video.height)
    detectors: dict[str, Detector] = {}
    notes = []
    for kind in s.kinds:
        spec = get_spec(s.face_model if kind == "face" else s.plate_model, kind)
        detectors[kind] = Detector(
            spec, ensure_model(spec), size, s.conf_keep, s.tile_size, s.tile_overlap, s.full_pass, device
        )
        d = detectors[kind]
        notes.append(
            f"  {pj.KIND_LABELS[kind]} : {spec.name}, {len(d.crops)} zones par image, "
            f"exécution {d.provider.replace('ExecutionProvider', '')}"
        )

    def detect(rgb: np.ndarray) -> dict[str, np.ndarray]:
        return {kind: d.detect(rgb) for kind, d in detectors.items()}

    return detect, notes


def analyze_range(
    project: pj.Project,
    clip: pj.Clip,
    start_pts: int,
    end_pts: int,
    detect: DetectFn,
    progress: Progress | None = None,
) -> tuple[list[pj.Track], int, dict[str, float]]:
    """Analyse [start_pts, end_pts) d'un clip.

    Renvoie les pistes, le PTS de fin réellement atteint (inférieur à `end_pts`
    en cas d'interruption au clavier) et les temps cumulés par étape.
    """
    s = project.analysis_settings
    video = clip.video
    max_gap = round(s.max_gap_s / video.tb)
    next_id = project.next_track_id()
    trackers = {k: Tracker(k, s.conf_new, max_gap, first_id=0) for k in s.kinds}
    timings = {"decode": 0.0, "detect": 0.0}
    reached = end_pts
    index = 0
    frame = None
    with av.open(str(project.clip_path(clip))) as container:
        frames = iter_frames(container, start_pts, end_pts)
        try:
            while True:
                t0 = time.perf_counter()
                frame = next(frames, None)
                if frame is None:
                    break
                run = index % max(1, s.detect_every) == 0
                rgb = frame.to_ndarray(format="rgb24") if run else None
                t1 = time.perf_counter()
                if run:
                    for kind, dets in detect(rgb).items():
                        trackers[kind].update(frame.pts, dets)
                timings["decode"] += t1 - t0
                timings["detect"] += time.perf_counter() - t1
                index += 1
                if progress:
                    progress.step()
        except KeyboardInterrupt:
            # On garde ce qui est fait : la zone analysée s'arrête à la dernière image traitée.
            reached = frame.pts if frame is not None else start_pts
            print("\nInterruption : l'analyse déjà faite est conservée.", file=sys.stderr)
    tracks = []
    for tracker in trackers.values():
        for live in tracker.finish():
            tracks.append(pj.Track(next_id, live.kind, live.obs))
            next_id += 1
    return tracks, reached, timings


def analyze_project(project: pj.Project, device: str = "auto", detect_fn: DetectFn | None = None) -> bool:
    """Analyse tout ce qui manque dans le projet. Renvoie False si l'analyse a été interrompue."""
    settings = project.analysis_settings
    signature = settings.signature()
    complete = True
    for clip in project.clips:
        state = project.analysis.get(clip.id)
        if state is None or state.signature != signature:
            if state is not None and state.tracks:
                print(f"{clip.id} : réglages d'analyse modifiés, l'analyse précédente est refaite.")
            state = pj.ClipAnalysis(signature=signature)
            project.analysis[clip.id] = state
        todo = needed_intervals(project, clip)
        if not todo:
            print(f"{clip.id} ({clip.path}) : déjà analysé, rien à faire.")
            continue
        video = clip.video
        if video.rotation:
            print(
                f"Attention : {clip.path} porte une rotation de {video.rotation}° dans ses "
                "métadonnées ; la détection travaille sur l'image non tournée et sera moins bonne.",
                file=sys.stderr,
            )
        detect = detect_fn
        if detect is None:
            detect, notes = make_detect_fn(project, clip, device)
            print(f"{clip.id} ({clip.path}) : {video.width}x{video.height}, {float(video.fps_fraction):.2f} i/s")
            print("\n".join(notes))
        total = round(intervals.total(todo) * video.tb * video.fps_fraction)
        progress = Progress(f"Analyse {clip.id}", total, float(video.fps_fraction))
        timings = {"decode": 0.0, "detect": 0.0}
        for start, end in todo:
            tracks, reached, t = analyze_range(project, clip, start, end, detect, progress)
            for k in timings:
                timings[k] += t[k]
            state.tracks.extend(tracks)
            if reached > start:
                state.analyzed = [list(i) for i in intervals.union(
                    [tuple(i) for i in state.analyzed], [(start, reached)]
                )]
            _refresh_review(project)
            pj.save(project)  # sauvegarde après chaque passage : reprise possible
            if reached < end:
                complete = False
                break
        print(progress.close())
        if progress.done:
            n = progress.done
            print(
                f"  détail par image : décodage {1000 * timings['decode'] / n:.1f} ms, "
                f"détection et suivi {1000 * timings['detect'] / n:.1f} ms"
            )
        if not complete:
            break
    _refresh_review(project)
    pj.save(project)
    return complete


def _refresh_review(project: pj.Project) -> None:
    review = []
    for clip in project.clips:
        state = project.analysis.get(clip.id)
        if state:
            review.extend(
                build_review(clip.id, state.tracks, clip.video, project.analysis_settings.detect_every)
            )
    project.review = review
