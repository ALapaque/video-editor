"""Passe 1 — analyse : détection et suivi, résultat écrit dans le fichier projet.

Seuls les passages gardés par le montage sont analysés (avec une petite marge
autour), et seulement s'ils ne l'ont pas déjà été : rallonger un passage après
coup ne réanalyse que la partie manquante. L'analyse étant indexée sur le temps
source, modifier une coupe ne l'invalide jamais.
"""

from __future__ import annotations

import gc
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import av
import numpy as np

from . import dash, intervals
from . import project as pj
from .detect import Detector, gpu_has_worked
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


_loaded: dict[tuple, Detector] = {}


def _load(spec, size, s: pj.AnalysisSettings, device: str, worked_before: bool) -> Detector:
    """Charge un modèle. Refuse de continuer si la carte graphique, déjà utilisée avec succès
    dans cette session, ne répond plus : mieux vaut une erreur claire qu'une analyse qui passe
    sans prévenir sur le processeur et dure des heures."""
    path = ensure_model(spec)
    args = (spec, path, size, s.conf_keep, s.tile_size, s.tile_overlap, s.full_pass, device)
    detector = Detector(*args)
    if detector.fell_back and worked_before:
        del detector
        gc.collect()
        detector = Detector(*args)  # second essai, une fois la mémoire de la carte libérée
        if detector.fell_back:
            raise RuntimeError(
                "la carte graphique ne répond plus (le chargement du modèle a échoué alors "
                "qu'il avait réussi auparavant). Fermez moto-blur et relancez-le. Pour analyser "
                "malgré tout sur le processeur, beaucoup plus lentement : option --device cpu."
            )
    return detector


def make_detect_fn(project: pj.Project, clip: pj.Clip, device: str) -> tuple[DetectFn, list[str]]:
    """Charge les modèles demandés et renvoie la fonction image -> détections par catégorie."""
    s = project.analysis_settings
    size = (clip.video.width, clip.video.height)
    detectors: dict[str, Detector] = {}
    notes = []
    wanted = {}
    for kind in s.kinds:
        spec = get_spec(s.face_model if kind == "face" else s.plate_model, kind)
        key = (kind, spec.name, size, s.conf_keep, s.tile_size, s.tile_overlap, s.full_pass, device)
        wanted[key] = (kind, spec)
    # Les modèles déjà chargés sont réutilisés d'une analyse à l'autre : c'est plus rapide, et
    # recharger un modèle sur la carte graphique alors que l'ancien y est encore peut échouer.
    for key in [k for k in _loaded if k not in wanted]:
        del _loaded[key]
    gc.collect()
    worked_before = gpu_has_worked()
    for key, (kind, spec) in wanted.items():
        if key not in _loaded:
            _loaded[key] = _load(spec, size, s, device, worked_before)
        d = detectors[kind] = _loaded[key]
        notes.append(
            f"  {pj.KIND_LABELS[kind]} : {spec.name}, {len(d.crops)} zones par image, "
            f"exécution {d.provider.replace('ExecutionProvider', '')}"
            + (f" ({d.note})" if d.note else "")
        )

    def one_by_one(rgb: np.ndarray) -> dict[str, np.ndarray]:
        return {kind: d.detect(rgb) for kind, d in detectors.items()}

    # Avec une carte graphique, faire travailler les deux modèles en même temps (chacun dans son
    # fil d'exécution) peut aller plus vite... ou moins vite, selon la carte et son pilote. On
    # chronomètre donc les deux façons sur une image d'essai et on garde la plus rapide.
    on_gpu = any(d.provider != "CPUExecutionProvider" for d in detectors.values())
    if not on_gpu or len(detectors) < 2:
        return one_by_one, notes
    pool = ThreadPoolExecutor(max_workers=len(detectors))

    def together(rgb: np.ndarray) -> dict[str, np.ndarray]:
        jobs = {kind: pool.submit(d.detect, rgb) for kind, d in detectors.items()}
        return {kind: job.result() for kind, job in jobs.items()}

    def timed(fn: DetectFn) -> float:
        best = float("inf")
        for i in range(3):
            t0 = time.perf_counter()
            fn(sample)
            if i:
                best = min(best, time.perf_counter() - t0)
        return best

    sample = np.random.default_rng(0).integers(0, 255, (size[1], size[0], 3), dtype=np.uint8)
    alone, both = timed(one_by_one), timed(together)
    detect = together if both < 0.95 * alone else one_by_one
    chosen = "en parallèle" if detect is together else "l'un après l'autre"
    notes.append(
        f"  par image : {1000 * alone:.0f} ms l'un après l'autre, {1000 * both:.0f} ms en parallèle "
        f"-> {chosen}"
    )
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
            if state is not None and any(t.source == "auto" for t in state.tracks):
                print(f"{clip.id} : réglages d'analyse modifiés, l'analyse précédente est refaite.")
            # Le compteur et les zones manuelles ne dépendent pas des réglages de détection.
            kept = [t for t in state.tracks if t.source != "auto"] if state else []
            state = pj.ClipAnalysis(signature=signature, tracks=kept)
            project.analysis[clip.id] = state
        todo = needed_intervals(project, clip)
        if dash.missing_span(project, clip):
            print(f"{clip.id} : prolongation du suivi du compteur sur les nouveaux passages.")
            dash.extend_tracking(project, clip)
            pj.save(project)
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
            refresh_review(project)
            pj.save(project)  # sauvegarde après chaque passage : reprise possible
            if reached < end:
                complete = False
                break
        print(progress.close())
        if progress.done and detect_fn is None:
            # Résumé relu par l'interface : dit notamment si l'accélération matérielle a servi.
            used = ", ".join(n.strip().replace(" zones par image", " zones") for n in notes)
            project.notes = [f"{progress.done} images à {progress.fps:.1f} i/s — {used}"]
        if progress.done:
            n = progress.done
            print(
                f"  détail par image : décodage {1000 * timings['decode'] / n:.1f} ms, "
                f"détection et suivi {1000 * timings['detect'] / n:.1f} ms"
            )
        if not complete:
            break
    refresh_review(project)
    pj.save(project)
    return complete


def refresh_review(project: pj.Project) -> None:
    review = []
    for clip in project.clips:
        state = project.analysis.get(clip.id)
        if state:
            review.extend(
                build_review(
                    clip.id, state.tracks, clip.video, project.analysis_settings.detect_every,
                    settings=project.blur_settings,
                )
            )
            review.extend(dash.build_dash_review(clip, state.tracks))
    project.review = review
