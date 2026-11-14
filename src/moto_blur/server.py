"""Serveur local de l'interface de montage et de vérification.

`moto-blur ui` démarre ce serveur sur 127.0.0.1 (jamais accessible depuis le réseau) et ouvre
l'interface dans le navigateur. L'application de bureau lance le même serveur en arrière-plan.
L'interface ne fait que piloter le moteur : toute la logique (montage, zones, analyse, rendu)
reste dans le moteur Python, et le fichier projet reste la seule source de vérité.
"""

from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import av
import cv2

from . import batch, confirm, contour, dash, library, media, progress, training, tuning
from . import project as pj
from .analyze import (
    PRESETS,
    analyze_project,
    apply_ignore_zones,
    apply_preset,
    estimate,
    frames_to_analyze,
    matching_preset,
    needed_intervals,
    refresh_review,
)
from .blur import SUPPORTED_FORMATS, blur_frame, zone_outline
from .history import History, snapshot
from .models import available
from .postprocess import (
    ZoneIndex,
    below_min_score,
    footprint,
    in_ignore_zone,
    is_blurred,
    median_box,
    nuisance,
    similar_tracks,
)
from .proxy import build_proxy, proxy_path
from .render import RenderError, check_ready, formats_differ, render_project, used_clips
from .timeline import pts_to_sec, resolve_segments, sec_to_pts
from .video import iter_frames, probe

VIDEO_EXTENSIONS = batch.VIDEO_EXTENSIONS
UI_DIR = Path(__file__).parent / "ui"

# Préréglages d'export : valeurs par défaut, que la requête peut surcharger champ par champ.
EXPORT_PRESETS = {
    "source": {},
    "youtube": {"quality": 90},
    "social": {"max_side": 1920, "codec": "h264", "quality": 75},
    "leger": {"max_side": 1280, "codec": "h264", "quality": 60},
}


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Job:
    """Traitement long en arrière-plan (proxy, analyse, suivi, rendu). Un seul à la fois."""

    def __init__(self, kind: str, label: str):
        self.kind = kind
        self.label = label
        self.done = 0
        self.total = 1
        self.fps = 0.0
        self.state = "running"  # running | done | error | cancelled
        self.message = ""
        self.cancel = False
        self.created: int | None = None  # identifiant de la zone créée par le traitement
        # File d'attente : rang de la vidéo en cours, nombre de vidéos, son nom, et les résultats.
        self.item: list | None = None
        self.results: list[dict] | None = None

    def to_json(self) -> dict:
        return {
            "kind": self.kind, "label": self.label, "done": self.done, "total": self.total,
            "fps": round(self.fps, 1), "state": self.state, "message": self.message,
            "created": self.created, "item": self.item, "results": self.results,
        }


class Engine:
    """État partagé du serveur : le projet ouvert et le traitement en cours."""

    def __init__(self, project: pj.Project | None = None):
        self.project = project
        self.lock = threading.RLock()
        self.job: Job | None = None
        self.version = 0  # incrémenté à chaque changement des zones (cache de l'interface)
        self._indexes: dict[str, tuple[int, ZoneIndex]] = {}
        self._dropped: dict[str, tuple[int, ZoneIndex]] = {}
        self._problem: tuple[int, str | None] = (-1, None)
        self.last_output: Path | None = None  # dernière vidéo écrite par un export de cette session
        self.history = History()
        self._last_record = ("", 0.0)
        self._estimate: tuple[tuple, dict] | None = None  # (état mesuré, résultat)

    # --- Projet -----------------------------------------------------------

    def require(self) -> pj.Project:
        if self.project is None:
            raise ApiError("aucun projet ouvert.", 409)
        return self.project

    def touch(self) -> None:
        self.version += 1
        pj.save(self.require())

    def begin(self, label: str, coalesce: bool = False) -> int:
        """Mémorise l'état avant une modification (pour « Annuler »). Renvoie la taille de la
        pile avant l'appel, à passer à `settle` une fois la modification faite ou échouée."""
        with self.lock:
            size = len(self.history.undo)
            if self.project is None:
                return size
            now = time.monotonic()
            recent = coalesce and self._last_record[0] == label and now - self._last_record[1] < 1.5
            self._last_record = (label, now)
            if not (recent and self.history.undo):
                self.history.record(self.project, label)
            return size

    def settle(self, size: int, failed: bool = False) -> None:
        """Après une modification : oublie la mémorisation si rien n'a changé (ou si elle a échoué)."""
        with self.lock:
            undo = self.history.undo
            if len(undo) <= size or self.project is None:
                return
            if failed or undo[-1][1] == snapshot(self.project):
                del undo[size:]

    def step(self, forward: bool) -> str:
        """Annule (ou rétablit) la dernière modification. Renvoie son nom."""
        project = self.require()
        with self.lock:
            self._busy_check()
            label = self.history.step_forward(project) if forward else self.history.step_back(project)
            if label is None:
                raise ApiError("rien à rétablir." if forward else "rien à annuler.", 409)
            self._after_zone_change()
            return label

    def set_sweep(self, body: dict) -> None:
        """Avancement de la relecture complète : position atteinte et relecture terminée ou non."""
        project = self.require()
        with self.lock:
            if "position" in body:
                project.sweep["position"] = round(max(0.0, float(body["position"])), 3)
            if "done" in body:
                project.sweep["done"] = bool(body["done"])
            self.save_review()

    def save_review(self) -> None:
        """Enregistre l'état de la vérification. Les zones ne changent pas : `version` reste
        tel quel, l'interface garde donc son cache de zones (important pendant la lecture)."""
        pj.save(self.require())

    def open_project(self, path: Path) -> None:
        path = Path(path).expanduser()
        with self.lock:
            self._busy_check()
            if path.suffix.lower() == ".json":
                if path.exists():
                    self.project = pj.load(path)
                else:
                    self.project = pj.Project(path=path)
                    pj.save(self.project)
            elif path.suffix.lower() in VIDEO_EXTENSIONS:
                project_path = path.with_name(path.stem + ".motoblur.json")
                if project_path.exists():
                    self.project = pj.load(project_path)
                else:
                    self.project = pj.Project(path=project_path)
                    self.add_clip(path)
            else:
                raise ApiError("choisissez une vidéo ou un fichier projet .json.")
            refresh_review(self.project)
            self.history.clear()
            self._estimate = None
            library.remember(self.project.path)
            self.version += 1

    def add_clip(self, path: Path) -> None:
        project = self.require()
        path = Path(path).expanduser()
        if not path.exists():
            raise ApiError(f"fichier introuvable : {path}")
        with self.lock:
            for clip in project.clips:
                if project.clip_path(clip).resolve() == path.resolve():
                    project.timeline.append(pj.Segment(clip.id, 0.0, round(clip.video.duration_s, 6)))
                    self.touch()
                    return
            try:
                video, audio = probe(path)
            except (av.FFmpegError, ValueError) as exc:
                raise ApiError(f"vidéo illisible : {exc}") from exc
            clip = pj.add_clip(project, path, video, audio)
            project.timeline.append(pj.Segment(clip.id, 0.0, round(video.duration_s, 6)))
            self.touch()

    def zone_index(self, clip: pj.Clip) -> ZoneIndex:
        cached = self._indexes.get(clip.id)
        if cached and cached[0] == self.version:
            return cached[1]
        project = self.require()
        state = project.analysis.get(clip.id) or pj.ClipAnalysis()
        index = ZoneIndex(state.tracks, project.blur_settings, clip.video)
        self._indexes[clip.id] = (self.version, index)
        return index

    def dropped_index(self, clip: pj.Clip) -> ZoneIndex:
        """Index des détections écartées par le score minimal (non floutées), pour les montrer."""
        cached = self._dropped.get(clip.id)
        if cached and cached[0] == self.version:
            return cached[1]
        project = self.require()
        state = project.analysis.get(clip.id) or pj.ClipAnalysis()
        settings = project.blur_settings
        tracks = [t for t in state.tracks if not t.disabled and below_min_score(t, settings)]
        # mêmes durées que si elles étaient floutées, mais sans le filtre qui les écarte
        index = ZoneIndex(tracks, replace(settings, face_min_score=0.0, plate_min_score=0.0), clip.video)
        self._dropped[clip.id] = (self.version, index)
        return index

    # --- État envoyé à l'interface ----------------------------------------

    def state(self) -> dict:
        with self.lock:
            project = self.project
            job = self.job.to_json() if self.job else None
            if project is None:
                return {
                    "project": None, "job": job, "version": self.version,
                    "profile": library.profile_summary(), "features": {"contour": True},
                    "models": {"face": available("face"), "plate": available("plate")},
                }
            clips = []
            tracks = {}
            for clip in project.clips:
                v = clip.video
                fps = v.fps_fraction
                clips.append(
                    {
                        "id": clip.id,
                        "name": Path(clip.path).name,
                        "path": str(project.clip_path(clip)),
                        "width": v.width, "height": v.height,
                        "fps_num": fps.numerator, "fps_den": fps.denominator,
                        "frames": v.frames, "duration": v.duration_s,
                        "codec": v.codec, "pix_fmt": v.pix_fmt,
                        "has_audio": clip.audio is not None,
                        "proxy": proxy_path(project, clip).exists(),
                        "missing": not project.clip_path(clip).exists(),
                        "needs_analysis": bool(needed_intervals(project, clip))
                        or confirm.due(project, clip)
                        or bool(dash.missing_span(project, clip)),
                    }
                )
                state = project.analysis.get(clip.id)
                tracks[clip.id] = [self._track_json(t, clip) for t in (state.tracks if state else [])]
            done = set(project.review_done)
            review = [
                {
                    "key": pj.review_key(r),
                    "done": pj.review_key(r) in done,
                    "clip": r["clip"],
                    "start": pts_to_sec(r["start_pts"], project.clip(r["clip"]).video),
                    "end": pts_to_sec(r["end_pts"], project.clip(r["clip"]).video),
                    "kinds": r["kinds"], "reasons": r["reasons"], "tracks": r["tracks"],
                    "reasons_by_track": r.get("reasons_by_track", {}),
                    # Vrai : quelque chose peut rester visible ici (objet suivi perdu…). Faux :
                    # flou douteux mais appliqué, donc sans risque ; le vérifier est facultatif.
                    "risk": bool(r.get("risk", False)),
                }
                for r in project.review
            ]
            if self._problem[0] != self.version:
                problem = None
                try:
                    segments = resolve_segments(project)
                    if segments:
                        check_ready(project, segments, project.path.with_name("__sortie__.mp4"))
                except RenderError as exc:
                    problem = str(exc)
                self._problem = (self.version, problem)
            problem = self._problem[1]
            return {
                "project": {
                    "path": str(project.path),
                    "name": project.path.name,
                    "clips": clips,
                    "timeline": [asdict(s) for s in project.timeline],
                    "mutes": [asdict(m) for m in project.mutes],
                    "settings": {
                        "analysis": asdict(project.analysis_settings),
                        "blur": asdict(project.blur_settings),
                    },
                    "review": review,
                    "marks": [dict(m) for m in project.marks],
                    "conform": project.conform,
                    "preset": project.preset,
                    "sweep": dict(project.sweep),
                    "ignore": project.ignore,
                    "history": self.history.labels(),
                    "estimate": self._current_estimate(),
                    "mismatch": formats_differ(used_clips(project, resolve_segments(project))),
                    # Rien à analyser, seulement des flous douteux à revoir de plus près : court.
                    "recheck_only": frames_to_analyze(project) == 0
                    and any(confirm.due(project, c) for c in project.clips),
                    "tracks": tracks,
                    "problem": problem,
                },
                "job": job,
                "version": self.version,
                "profile": library.profile_summary(),
                "features": {"contour": True},
                "models": {"face": available("face"), "plate": available("plate")},
            }

    def _estimate_key(self) -> tuple:
        project = self.require()
        return (frames_to_analyze(project), project.analysis_settings.signature())

    def _current_estimate(self) -> dict | None:
        """Dernière estimation de durée, tant qu'elle correspond encore à ce qui reste à analyser."""
        if self._estimate is None or self._estimate[0] != self._estimate_key():
            return None
        return self._estimate[1]

    def _track_json(self, t: pj.Track, clip: pj.Clip) -> dict:
        v = clip.video
        scores = sorted(o[5] for o in t.obs)
        doc = {
            "id": t.id, "kind": t.kind, "name": t.name, "source": t.source, "disabled": t.disabled,
            "forced": t.forced, "below_min_score": below_min_score(t, self.require().blur_settings),
            "best_score": round(max(o[5] for o in t.obs), 2),
            "start": pts_to_sec(int(t.obs[0][0]), v), "end": pts_to_sec(int(t.obs[-1][0]), v),
            "extend_before_s": t.extend_before_s, "extend_after_s": t.extend_after_s,
            "score": round(scores[len(scores) // 2], 2), "count": len(t.obs),
            # Place à l'écran (% de l'image) et gêne si le flou est inutile (taille × durée).
            "area": round(footprint(t, v)[0], 3), "nuisance": round(nuisance(t, v), 3),
        }
        if t.source == "tracker":
            doc["contour"] = bool(t.outline)
        if t.source == "manual":
            doc["keyframes"] = [[pts_to_sec(int(o[0]), v), *[round(x) for x in o[1:5]]] for o in t.obs]
        return doc

    # --- Montage et zones --------------------------------------------------

    def set_montage(self, body: dict) -> None:
        project = self.require()
        with self.lock:
            timeline = []
            for s in body.get("timeline", []):
                clip = project.clip(s["clip"])
                start = max(0.0, float(s["start"]))
                end = min(float(s["end"]), clip.video.duration_s)
                if end > start:
                    timeline.append(pj.Segment(clip.id, round(start, 6), round(end, 6)))
            mutes = []
            for m in body.get("mutes", []):
                project.clip(m["clip"])
                if float(m["end"]) > float(m["start"]):
                    mutes.append(pj.Mute(m["clip"], round(float(m["start"]), 6), round(float(m["end"]), 6)))
            project.timeline, project.mutes = timeline, mutes
            used = {s.clip for s in timeline}
            if body.get("drop_unused_clips"):
                project.clips = [c for c in project.clips if c.id in used]
                pj.prune_review_state(project)
            self.touch()

    # --- Vérification guidée -------------------------------------------------

    def set_review_done(self, body: dict) -> None:
        """Valide (ou remet à revoir) un passage douteux, désigné par sa clé."""
        project = self.require()
        with self.lock:
            self._busy_check()
            key = str(body.get("key", ""))
            if key not in {pj.review_key(r) for r in project.review}:
                raise ApiError("passage à vérifier introuvable.", 404)
            kept = [k for k in project.review_done if k != key]
            project.review_done = [*kept, key] if body.get("done", True) else kept
            self.save_review()

    def add_mark(self, body: dict) -> int:
        """Pose un signalement de relecture : instant d'un clip, en secondes de temps source."""
        project = self.require()
        with self.lock:
            self._busy_check()
            clip = project.clip(body["clip"])
            time = max(0.0, min(float(body["time"]), clip.video.duration_s))
            mark_id = max((m["id"] for m in project.marks), default=0) + 1
            project.marks.append({"id": mark_id, "clip": clip.id, "time": round(time, 6)})
            self.save_review()
            return mark_id

    def delete_mark(self, mark_id: int) -> None:
        project = self.require()
        with self.lock:
            self._busy_check()
            kept = [m for m in project.marks if m["id"] != mark_id]
            if len(kept) == len(project.marks):
                raise ApiError("signalement introuvable.", 404)
            project.marks = kept
            self.save_review()

    def _find_track(self, clip_id: str, track_id: int) -> tuple[pj.ClipAnalysis, pj.Track]:
        state = self.require().analysis.get(clip_id)
        for t in state.tracks if state else []:
            if t.id == track_id:
                return state, t
        raise ApiError("zone introuvable.", 404)

    def patch_track(self, body: dict) -> None:
        with self.lock:
            self._busy_check()
            ids = body["ids"] if "ids" in body else [body["id"]]  # une zone, ou plusieurs d'un coup
            for track_id in ids:
                _, track = self._find_track(body["clip"], int(track_id))
                if "disabled" in body:
                    track.disabled = bool(body["disabled"])
                if "forced" in body:
                    track.forced = bool(body["forced"])
                for key in ("extend_before_s", "extend_after_s"):
                    if key in body:
                        value = body[key]
                        setattr(track, key, None if value is None else max(0.0, min(float(value), 600.0)))
                if "span" in body:
                    self._set_span(body["clip"], track, body["span"])
            self._after_zone_change()

    def _set_span(self, clip_id: str, track: pj.Track, span) -> None:
        """Début et fin d'un flou posé à la main, en secondes de temps source.

        - Flou fixe : le début et la fin se déplacent librement (le rectangle garde la position
          qu'il avait à ce bout-là) ; raccourci, il s'arrête à sa position interpolée.
        - Flou qui suit : on ne peut que le raccourcir ; il s'arrête alors net, sans prolongation.
        """
        if track.source == "auto":
            raise ApiError("le début et la fin ne se règlent que sur un flou posé à la main.")
        v = self.require().clip(clip_id).video
        start = sec_to_pts(max(0.0, float(span[0])), v, "floor")
        end = sec_to_pts(min(float(span[1]), v.duration_s), v, "ceil")
        obs = sorted(track.obs, key=lambda o: o[0])
        first, last = int(obs[0][0]), int(obs[-1][0])
        if track.source == "tracker":
            start, end = max(start, first), min(end, last)
        if end - start < v.frame_pts:
            raise ApiError("le flou doit durer au moins une image.")

        def at(pts: int) -> list[float]:
            """Observation à cet instant : interpolée, ou figée au-delà des extrémités."""
            if pts <= first:
                return [pts, *obs[0][1:]]
            if pts >= last:
                return [pts, *obs[-1][1:]]
            i = max(k for k, o in enumerate(obs) if o[0] <= pts)
            a, b = obs[i], obs[i + 1]
            w = (pts - a[0]) / (b[0] - a[0])
            return [pts, *[a[k] + (b[k] - a[k]) * w for k in range(1, 5)], a[5]]

        inside = [o for o in obs if start < o[0] < end]
        track.obs = [at(start), *inside, at(end)]
        if track.source == "tracker":
            if start > first:
                track.extend_before_s = 0.0
            if end < last:
                track.extend_after_s = 0.0
            if track.outline:
                track.outline = [row for row in track.outline if start <= row[0] <= end] or None

    def similar(self, clip_id: str, track_id: int) -> list[int]:
        """Flous automatiques encore actifs « comme celui-ci » (même endroit, même catégorie)."""
        with self.lock:
            state, track = self._find_track(clip_id, track_id)
            # Seulement des flous réellement appliqués : un objet non flouté n'a rien à retirer.
            blur = self.require().blur_settings
            return [t.id for t in similar_tracks(state.tracks, track) if is_blurred(t, blur)]

    def add_ignore_zone(self, body: dict) -> int:
        """Zone à ne jamais flouter dans un clip : retire les flous automatiques qui s'y tiennent,
        maintenant et après chaque analyse. Renvoie le nombre de flous retirés."""
        project = self.require()
        with self.lock:
            self._busy_check()
            clip = project.clip(body["clip"])
            v = clip.video
            if "id" in body:
                # « Ne plus jamais flouter cet endroit » : la place habituelle de ce flou, élargie.
                _, track = self._find_track(clip.id, int(body["id"]))
                bx1, by1, bx2, by2 = median_box(track)
                gx, gy = 0.25 * (bx2 - bx1), 0.25 * (by2 - by1)
                box = (bx1 - gx, by1 - gy, bx2 + gx, by2 + gy)
            else:
                box = body["box"]
            x1, y1, x2, y2 = (float(x) for x in box)
            x1, x2 = sorted((max(0.0, min(x1, v.width)), max(0.0, min(x2, v.width))))
            y1, y2 = sorted((max(0.0, min(y1, v.height)), max(0.0, min(y2, v.height))))
            if x2 - x1 < 8 or y2 - y1 < 8:
                raise ApiError("zone trop petite.")
            project.ignore.setdefault(clip.id, []).append([round(x1), round(y1), round(x2), round(y2)])
            count = apply_ignore_zones(project, clip.id)
            self._after_zone_change()
            return count

    def remove_ignore_zone(self, clip_id: str, index: int) -> int:
        """Retire une zone à ne jamais flouter et remet les flous qu'elle écartait."""
        project = self.require()
        with self.lock:
            self._busy_check()
            zones = project.ignore.get(clip_id, [])
            if not 0 <= index < len(zones):
                raise ApiError("zone introuvable.", 404)
            zone = zones.pop(index)
            if not zones:
                project.ignore.pop(clip_id, None)
            count = 0
            state = project.analysis.get(clip_id)
            for t in state.tracks if state else []:
                if t.source == "auto" and t.disabled and in_ignore_zone(t, [zone]) \
                        and not in_ignore_zone(t, project.ignore.get(clip_id, [])):
                    t.disabled = False
                    count += 1
            self._after_zone_change()
            return count

    def delete_track(self, clip_id: str, track_id: int) -> None:
        with self.lock:
            self._busy_check()
            state, track = self._find_track(clip_id, track_id)
            if track.source == "auto":
                track.disabled = True  # une détection n'est jamais effacée, seulement ignorée
            else:
                state.tracks.remove(track)
            self._after_zone_change()

    def save_manual(self, body: dict) -> int:
        """Crée ou remplace une zone manuelle : images clés [temps_s, x1, y1, x2, y2]."""
        project = self.require()
        with self.lock:
            self._busy_check()
            clip = project.clip(body["clip"])
            v = clip.video
            keyframes = sorted(body.get("keyframes", []), key=lambda k: k[0])
            if not keyframes:
                raise ApiError("une zone manuelle a besoin d'au moins une image clé.")
            obs = []
            for t, x1, y1, x2, y2 in keyframes:
                x1, x2 = sorted((max(0, min(float(x1), v.width)), max(0, min(float(x2), v.width))))
                y1, y2 = sorted((max(0, min(float(y1), v.height)), max(0, min(float(y2), v.height))))
                if x2 - x1 < 2 or y2 - y1 < 2:
                    raise ApiError("zone trop petite.")
                obs.append([sec_to_pts(float(t), v, "floor"), x1, y1, x2, y2, 1.0])
            if len(obs) == 1:
                raise ApiError("une zone manuelle a besoin d'un début et d'une fin.")
            state = project.analysis.setdefault(clip.id, pj.ClipAnalysis())
            kind = body.get("kind", "manual")
            if kind not in ("manual", "dash"):
                raise ApiError("type de zone inconnu.")
            if body.get("id") is not None:
                _, track = self._find_track(clip.id, int(body["id"]))
                if track.source != "manual":
                    raise ApiError("seules les zones manuelles peuvent être redessinées.")
                track.obs = obs
            else:
                track = pj.Track(project.next_track_id(), kind, obs, source="manual")
                state.tracks.append(track)
            self._after_zone_change()
            return track.id

    def set_settings(self, body: dict) -> None:
        project = self.require()
        with self.lock:
            self._busy_check()
            if "conform" in body:
                if body["conform"] not in ("", "first", "largest"):
                    raise ApiError("choix d'alignement inconnu.")
                project.conform = body["conform"]
            if "preset" in body:
                if body["preset"] not in PRESETS:
                    raise ApiError("préréglage inconnu.")
                apply_preset(project, body["preset"])
            manual = "preset" not in body and any(k != "kinds" for k in body.get("analysis", {}))
            for block, target in (("analysis", project.analysis_settings), ("blur", project.blur_settings)):
                for key, value in body.get(block, {}).items():
                    if not hasattr(target, key):
                        raise ApiError(f"réglage inconnu : {key}")
                    current = getattr(target, key)
                    if key == "kinds":
                        self._set_kinds(value)
                        continue
                    if isinstance(current, bool):
                        value = bool(value)
                    elif isinstance(current, int):
                        value = int(value)
                    elif isinstance(current, float):
                        value = float(value)
                    setattr(target, key, value)
            if manual:
                project.preset = matching_preset(project)  # « perso » si plus aucun ne correspond
            refresh_review(project)  # le score minimal change les passages à vérifier
            self.touch()

    def _set_kinds(self, value) -> None:
        """Catégories à détecter (visages, plaques) : liste validée, éventuellement vide.

        Quand le choix change, les détections des catégories retirées ne sont plus floutées et
        l'analyse est à refaire pour les catégories demandées. Les objets désignés à la main et
        les zones manuelles ne sont pas touchés."""
        project = self.require()
        if not isinstance(value, list) or any(k not in pj.KINDS for k in value):
            raise ApiError("catégories à détecter inconnues (attendu : face, plate).")
        kinds = [k for k in pj.KINDS if k in value]  # ordre fixe, sans doublon
        if kinds == project.analysis_settings.kinds:
            return
        project.analysis_settings.kinds = kinds
        for state in project.analysis.values():
            state.tracks = [t for t in state.tracks if t.source != "auto" or t.kind in kinds]
            state.signature, state.analyzed = "", []

    def reveal(self, body: dict) -> dict:
        """Montre un fichier dans le gestionnaire de fichiers du système.

        Seuls la dernière vidéo exportée et les fichiers du dossier du projet sont acceptés."""
        project = self.require()
        raw = str(body.get("path", "")).strip()
        if not raw:
            raise ApiError("indiquez un fichier.")
        path = Path(raw).expanduser().resolve()
        folder = project.path.parent.resolve()
        if path != self.last_output and folder not in path.parents:
            raise ApiError("ce fichier ne fait pas partie du projet.", 403)
        if not path.is_file():
            raise ApiError(f"fichier introuvable : {path}", 404)
        if sys.platform == "win32":
            command = ["explorer", f"/select,{path}"]
        elif sys.platform == "darwin":
            command = ["open", "-R", str(path)]
        else:
            if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
                raise ApiError(f"impossible d'ouvrir le dossier sur cette machine (pas de bureau graphique) : {path.parent}", 500)
            command = ["xdg-open", str(path.parent)]
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603
        except OSError as exc:
            raise ApiError(f"impossible d'ouvrir le dossier ({exc}) : {path.parent}", 500) from exc
        return {"ok": True, "folder": str(path.parent)}

    def _after_zone_change(self) -> None:
        refresh_review(self.require())
        self.touch()

    def zones(self, clip_id: str, first: int, last: int, dropped: bool = False) -> dict:
        """Zones de flou pour les images `first` à `last` (indices) d'un clip.

        Avec `dropped`, ajoute les détections écartées par le score minimal : pour chaque image,
        [x1, y1, x2, y2, catégorie, piste, score] avec la boîte brute interpolée, sans marge."""
        project = self.require()
        with self.lock:
            clip = project.clip(clip_id)
            v = clip.video
            index = self.zone_index(clip)
            last = min(last, first + 900, v.frames)
            frames = {}
            for k in range(max(0, first), last + 1):
                pts = v.start_pts + round(k * v.frame_pts)
                items = []
                by_track = {item.track.id: item for item in index.active(pts)}
                for z in index.shaped(pts):  # flous superposés déjà réunis
                    item = by_track.get(z.track_id)
                    obs = item.observation(pts) if item else None
                    score = None if obs is None else round(obs[4], 2)
                    # 10e valeur : toutes les pistes réunies dans cette zone.
                    entry = [*z.box, z.shape, z.radius, z.kind, z.track_id, score, list(z.tracks or (z.track_id,))]
                    if z.shape == "contour":
                        entry.append(zone_outline(z))  # 11e valeur : tracé de ce qui est flouté
                    items.append(entry)
                if items:
                    frames[k] = items
            out = {"version": self.version, "frames": frames}
            if dropped:
                extra = {}
                other = self.dropped_index(clip)
                for k in range(max(0, first), last + 1):
                    pts = v.start_pts + round(k * v.frame_pts)
                    items = []
                    for item in other.active(pts):
                        box = item.raw_box(pts)
                        if box is None:
                            continue
                        obs = item.observation(pts)
                        items.append(
                            [*[round(x) for x in box], item.track.kind, item.track.id,
                             None if obs is None else round(obs[4], 2)]
                        )
                    if items:
                        extra[k] = items
                out["dropped"] = extra
            return out

    def frame_image(self, clip_id: str, frame_index: int, blur: bool):
        """Image source exacte (tableau BGR, pleine résolution), avec ou sans le flou du rendu."""
        project = self.require()
        clip = project.clip(clip_id)
        v = clip.video
        pts = v.start_pts + round(frame_index * v.frame_pts)
        half = round(v.frame_pts / 2)
        with av.open(str(project.clip_path(clip))) as container:
            frame = next(iter_frames(container, pts - half, v.end_pts + 1), None)
        if frame is None:
            raise ApiError("image introuvable.", 404)
        if blur:
            with self.lock:
                zones = self.zone_index(clip).shaped(frame.pts)
            if zones:
                if frame.format.name not in SUPPORTED_FORMATS:
                    frame = frame.reformat(format="yuv420p")
                frame.make_writable()
                b = project.blur_settings
                blur_frame(frame, zones, b.cells, b.style, b.feather)
        return frame.to_ndarray(format="bgr24")

    def frame_jpeg(self, clip_id: str, frame_index: int, blur: bool, max_width: int) -> bytes:
        """Image source exacte, en pleine résolution, avec ou sans le flou du rendu final."""
        image = self.frame_image(clip_id, frame_index, blur)
        if 0 < max_width < image.shape[1]:
            height = round(image.shape[0] * max_width / image.shape[1])
            image = cv2.resize(image, (max_width, height), interpolation=cv2.INTER_AREA)
        ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise ApiError("encodage de l'image impossible.", 500)
        return data.tobytes()

    def snapshot_image(self, body: dict) -> dict:
        """Enregistre l'image affichée en PNG pleine résolution, dans le dossier du projet."""
        project = self.require()
        clip = project.clip(body["clip"])
        index = int(body["frame"])
        blur = bool(body.get("blur", True))
        image = self.frame_image(clip.id, index, blur)
        seconds = index / float(clip.video.fps_fraction)
        stamp = f"{int(seconds // 60):02d}m{seconds % 60:05.2f}s".replace(".", "-")
        name = project.path.name.replace(".motoblur.json", "").replace(".json", "")
        target = project.path.parent / f"{name}_image_{stamp}{'' if blur else '_nette'}.png"
        if not cv2.imwrite(str(target), image):
            raise ApiError("enregistrement de l'image impossible.", 500)
        return {"path": str(target), "width": int(image.shape[1]), "height": int(image.shape[0])}

    def thumbnail(self, clip_id: str, track_id: int, width: int) -> bytes:
        project = self.require()
        _, track = self._find_track(clip_id, track_id)
        return media.track_thumbnail(project, project.clip(clip_id), track, max(80, min(width, 480)))

    # --- Traitements en arrière-plan --------------------------------------

    def _busy_check(self) -> None:
        if self.job and self.job.state == "running":
            raise ApiError("un traitement est déjà en cours.", 409)

    def start(self, kind: str, label: str, work, undo: str | None = None) -> None:
        """Lance un traitement en arrière-plan. `undo` : nom sous lequel il pourra être annulé."""
        with self.lock:
            self._busy_check()
            undo_size = self.begin(undo) if undo else None
            job = Job(kind, label)
            self.job = job

        def on_progress(p: progress.Progress) -> None:
            job.done, job.total, job.label = p.done, p.total, p.label
            job.fps = p.fps if p.unit == "images" else 0.0
            if job.cancel:
                raise progress.Cancelled()

        def run() -> None:
            progress.hook = on_progress
            try:
                message = work(job)
                job.state = "cancelled" if job.cancel else "done"
                job.message = message or ""
            except progress.Cancelled:
                job.state, job.message = "cancelled", "Traitement annulé."
            except (RenderError, ApiError, ValueError, RuntimeError, OSError) as exc:
                job.state, job.message = "error", str(exc)
            except Exception as exc:  # noqa: BLE001 - toute erreur doit remonter à l'interface
                traceback.print_exc()
                job.state, job.message = "error", f"{type(exc).__name__} : {exc}"
            finally:
                progress.hook = None
                if undo_size is not None:
                    self.settle(undo_size, failed=job.state == "error")
                with self.lock:
                    self.version += 1

        threading.Thread(target=run, daemon=True).start()

    def start_proxies(self) -> None:
        project = self.require()

        def work(job: Job) -> str:
            for clip in project.clips:
                if proxy_path(project, clip).exists() or not project.clip_path(clip).exists():
                    continue
                p = progress.Progress(f"Préparation de l'aperçu de {Path(clip.path).name}", clip.video.frames, 0)
                build_proxy(project, clip, p)
            return "Aperçus prêts."

        self.start("proxy", "Préparation des aperçus", work)

    def start_analysis(self, device: str) -> None:
        project = self.require()

        def work(job: Job) -> str:
            job.label = "Chargement des modèles"
            if frames_to_analyze(project):
                # Nouvelles détections : l'historique ne peut plus être rejoué, et la relecture
                # complète est à refaire.
                self.history.clear()
                project.sweep = {"position": 0.0, "done": False}
            complete = analyze_project(project, device=device)
            if apply_ignore_zones(project):  # les nouvelles détections des zones à ne jamais flouter
                refresh_review(project)
                pj.save(project)
            note = " ".join(getattr(project, "notes", []))
            if not complete:
                return "Analyse interrompue : ce qui est fait est conservé."
            return f"Analyse terminée. {note}".strip()

        self.start("analyze", "Analyse", work)

    def start_dash(self, body: dict) -> None:
        """Désigne un objet à flouter (compteur, téléphone…) : zone fixe ou suivi."""
        project = self.require()
        clip = project.clip(body["clip"])
        name = dash.object_name(body.get("label"))
        if body.get("mode") == "clear":
            with self.lock:
                self._busy_check()
                dash.clear_dash(project, clip, name)
                self._after_zone_change()
            return
        box = tuple(float(x) for x in body["box"])
        if body.get("mode") == "fixed":
            with self.lock:
                self._busy_check()
                dash.set_fixed_zone(project, clip, tuple(int(x) for x in box), name)
                self._after_zone_change()
            return
        seed = sec_to_pts(float(body["time"]), clip.video, "floor")
        direction = body.get("direction", "both")
        scope = body.get("scope", "auto")
        if scope not in ("auto", "all", "shot"):
            raise ApiError("portée de suivi inconnue.")

        def work(job: Job) -> str:
            span = dash.dash_span(project, clip)
            if span is None:
                raise ApiError("ce clip n'a aucun passage gardé.")
            total = round((span[1] - span[0]) * clip.video.tb * clip.video.fps_fraction)
            p = progress.Progress(f"Suivi : {name}", total, float(clip.video.fps_fraction))
            tracks = dash.track_from_click(project, clip, seed, box, direction, p, name, scope)
            refresh_review(project)
            pj.save(project)
            return dash.describe(name, tracks, clip)

        self.start("dash", f"Suivi : {name}", work, undo=f"suivi de « {name} »")

    def start_follow(self, body: dict) -> None:
        """Flou ajouté à la main qui suit ce qui a été entouré, dans une portion du clip."""
        project = self.require()
        clip = project.clip(body["clip"])
        v = clip.video
        box = tuple(float(x) for x in body["box"])
        if box[2] - box[0] < 4 or box[3] - box[1] < 4:
            raise ApiError("zone trop petite.")
        seed = sec_to_pts(float(body["time"]), v, "floor")
        start = sec_to_pts(float(body.get("start", 0.0)), v, "floor")
        end = sec_to_pts(float(body.get("end", v.duration_s)), v, "ceil")
        if end <= start:
            raise ApiError("portion vide.")

        def work(job: Job) -> str:
            total = round((end - start) * v.tb * v.fps_fraction)
            p = progress.Progress("Suivi du flou ajouté", total, float(v.fps_fraction))
            track = dash.follow_region(project, clip, seed, box, start, end, p)
            refresh_review(project)
            pj.save(project)
            seconds = float((track.obs[-1][0] - track.obs[0][0]) * v.tb)
            job.created = track.id
            return f"Flou ajouté et suivi pendant {seconds:.1f} s.".replace(".", ",", 1)

        self.start("follow", "Suivi du flou ajouté", work, undo="flou ajouté")

    def start_contour(self, body: dict) -> None:
        """Contour précis d'un objet suivi (par son nom) ou d'un flou ajouté qui suit (par son
        identifiant) : le flou épouse l'objet. Avec `clear`, retour à la forme habituelle."""
        project = self.require()
        clip = project.clip(body["clip"])
        if "id" in body:
            tracks = [self._find_track(clip.id, int(body["id"]))[1]]
            name = tracks[0].name
        else:
            name = dash.object_name(body.get("label"))
            tracks = dash.dash_tracks(project, clip, "tracker", name)
        tracks = [t for t in tracks if t.source == "tracker"]
        if not tracks:
            raise ApiError("cet objet n'est pas suivi : le contour précis s'applique à un objet suivi.")
        if body.get("clear"):
            with self.lock:
                self._busy_check()
                contour.clear(tracks)
                self._after_zone_change()
            return

        def work(job: Job) -> str:
            result = contour.refine(project, clip, tracks)
            pj.save(project)
            if not result["kept"]:
                return f"Aucun contour fiable trouvé pour « {name} » : la forme habituelle est gardée."
            note = ""
            if result["kept"] < result["total"]:
                note = (f" Sur {result['total'] - result['kept']} image(s) de référence, le contour"
                        " n'était pas fiable : la forme habituelle y est gardée.")
            return f"Contour précis calculé pour « {name} ».{note} Vérifiez le résultat sur la vidéo."

        self.start("contour", f"Contour précis : {name}", work, undo=f"contour précis de « {name} »")

    def start_batch(self, body: dict) -> None:
        """File d'attente : analyse plusieurs vidéos (ou projets) à la suite."""
        paths = [Path(str(p)) for p in body.get("paths", []) if str(p).strip()]
        if not paths:
            raise ApiError("ajoutez au moins une vidéo à la file.")
        preset = body.get("preset")
        if preset is not None and preset not in PRESETS:
            raise ApiError("préréglage inconnu.")
        use_profile = bool(body.get("profile", False))
        device = body.get("device", "auto")

        def work(job: Job) -> str:
            job.results = []

            def on_item(index: int, total: int, name: str) -> None:
                job.item = [index + 1, total, name]

            results = batch.run_batch(paths, preset, use_profile, device, on_item, self.project)
            job.results = results
            if self.project is not None:
                self.history.clear()  # le projet ouvert a pu être réanalysé
                refresh_review(self.project)
            return batch.summary(results, len(paths))

        self.start("batch", "File d'attente", work)

    def start_dataset(self) -> None:
        project = self.require()

        def work(job: Job) -> str:
            result = training.export_dataset(project)
            return (f"{result['images']} images d'entraînement écrites ({result['labels']} plaques, "
                    f"{result['negatives']} sans plaque) dans {result['path']}")

        self.start("dataset", "Images d'entraînement", work)

    def start_estimate(self, device: str) -> None:
        project = self.require()

        def work(job: Job) -> str:
            job.label = "Mesure de la vitesse d'analyse"
            key = self._estimate_key()
            result = estimate(project, device=device)
            self._estimate = (key, result)
            if not result["frames"]:
                return "Rien à analyser."
            minutes = result["seconds"] / 60
            speed = f"{result['fps']:.1f}".replace(".", ",")
            return f"Analyse estimée à environ {max(1, round(minutes))} min ({speed} images par seconde)."

        self.start("estimate", "Mesure de la vitesse d'analyse", work)

    def start_profile(self) -> None:
        project = self.require()

        def work(job: Job) -> str:
            notes = library.apply_profile(project)
            self.history.clear()  # les réglages d'analyse ont pu changer
            refresh_review(project)
            pj.save(project)
            return " ".join(notes)

        self.start("profile", "Application du profil", work)

    def start_render(self, body: dict) -> None:
        project = self.require()
        output = Path(str(body.get("output", "")).strip()).expanduser()
        if not output.name:
            raise ApiError("indiquez un fichier de sortie.")
        if not output.is_absolute():
            output = project.path.parent / output
        if output.suffix.lower() not in (".mp4", ".mov", ".mkv"):
            output = output.with_name(output.name + ".mp4")
        preset = body.get("preset", "source")
        if preset not in EXPORT_PRESETS:
            raise ApiError("préréglage d'export inconnu.")
        options = {"quality": 80, "codec": "source", "max_side": 0, **EXPORT_PRESETS[preset]}
        options.update({k: body[k] for k in ("quality", "codec", "max_side") if k in body})
        quality = max(0, min(int(options["quality"]), 100))
        codec, encoder = options["codec"], body.get("encoder", "auto")
        max_side = max(0, int(options["max_side"]))
        debug = bool(body.get("debug", False))
        window = None
        if body.get("window"):
            start = max(0.0, float(body["window"].get("start", 0.0)))
            window = (start, start + max(0.5, float(body["window"].get("duration", 10.0))))
            if "extrait" not in output.stem:
                output = output.with_name(f"{output.stem}_extrait{output.suffix}")

        def work(job: Job) -> str:
            render_project(project, output, quality, codec, encoder, debug, window=window, max_side=max_side)
            self.last_output = output.resolve()
            return f"{'Extrait écrit' if window else 'Vidéo écrite'} : {output}"

        self.start("render", "Rendu", work)


def browse(directory: str | None, engine: Engine) -> dict:
    """Contenu d'un dossier pour le sélecteur de fichiers : sous-dossiers, vidéos, projets."""
    if directory:
        base = Path(directory).expanduser()
    elif engine.project is not None:
        base = engine.project.path.parent
    else:
        base = Path.cwd()
    base = base.resolve()
    if not base.is_dir():
        raise ApiError(f"dossier introuvable : {base}", 404)
    folders, files = [], []
    try:
        entries = sorted(base.iterdir(), key=lambda p: p.name.lower())
    except PermissionError as exc:
        raise ApiError(f"accès refusé : {base}", 403) from exc
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                if not entry.name.endswith(".proxies"):
                    folders.append(entry.name)
            elif entry.suffix.lower() in VIDEO_EXTENSIONS or entry.name.endswith(".motoblur.json"):
                files.append({"name": entry.name, "size": entry.stat().st_size})
        except OSError:
            continue
    roots = []
    if os.name == "nt":
        roots = [f"{c}:\\" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if Path(f"{c}:\\").exists()]
    return {
        "dir": str(base), "parent": None if base.parent == base else str(base.parent),
        "home": str(Path.home()), "folders": folders, "files": files, "roots": roots, "sep": os.sep,
    }


class Handler(BaseHTTPRequestHandler):
    engine: Engine
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # silence : pas de journal par requête
        pass

    # --- utilitaires ---

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        extra = dict(extra or {})
        self.send_header("Cache-Control", extra.pop("Cache-Control", "no-store"))
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _read_body(self) -> None:
        """Lit le corps de la requête dès son arrivée, que la route s'en serve ou non.

        Sur une connexion gardée ouverte, un corps laissé non lu serait pris pour le début de la
        requête suivante (« {}GET … » : réponse 501, et l'interface croit le moteur injoignable)."""
        length = int(self.headers.get("Content-Length") or 0)
        self._raw = self.rfile.read(length) if length > 0 else b""

    def _body(self) -> dict:
        raw = getattr(self, "_raw", b"")
        if not raw.strip():
            return {}
        return json.loads(raw.decode("utf-8"))

    def _local_only(self) -> None:
        """Refuse les requêtes émises par une autre page web que l'interface elle-même."""
        origin = self.headers.get("Origin")
        if origin:
            host = urllib.parse.urlparse(origin).hostname
            if host not in ("127.0.0.1", "localhost", "tauri.localhost"):
                raise ApiError("origine refusée.", 403)

    def _file(self, path: Path, content_type: str | None = None) -> None:
        """Envoie un fichier, avec prise en charge des plages d'octets (lecture vidéo)."""
        if not path.is_file():
            raise ApiError("fichier introuvable.", 404)
        size = path.stat().st_size
        content_type = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        start, end, status = 0, size - 1, 200
        header = self.headers.get("Range")
        if header and header.startswith("bytes="):
            first, _, last = header[6:].partition("-")
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
            elif last:
                start = max(0, size - int(last))
            if start > end or start >= size:
                self._send(416, b"", content_type, {"Content-Range": f"bytes */{size}"})
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            try:
                while remaining > 0:
                    chunk = f.read(min(1 << 18, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # le lecteur vidéo a changé de position : normal

    def _edit(self, label: str, action, coalesce: bool = False) -> None:
        """Exécute une modification annulable, puis renvoie l'état complet (et ce que l'action
        a renvoyé : identifiant créé, par exemple)."""
        size = self.engine.begin(label, coalesce)
        try:
            extra = action()
        except Exception:
            self.engine.settle(size, failed=True)
            raise
        self.engine.settle(size)
        self._json({**self.engine.state(), **(extra if isinstance(extra, dict) else {})})

    def _handle(self, method: str) -> None:
        engine = self.engine
        url = urllib.parse.urlparse(self.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        route = url.path
        self._read_body()
        try:
            self._local_only()
            if route == "/api/state" and method == "GET":
                self._json(engine.state())
            elif route == "/api/browse" and method == "GET":
                self._json(browse(query.get("dir"), engine))
            elif route == "/api/open" and method == "POST":
                engine.open_project(Path(self._body()["path"]))
                self._json(engine.state())
            elif route == "/api/clips" and method == "POST":
                engine.add_clip(Path(self._body()["path"]))
                self._json(engine.state())
            elif route == "/api/montage" and method == "PUT":
                self._edit("montage", lambda: engine.set_montage(self._body()))
            elif route == "/api/settings" and method == "PUT":
                body = self._body()
                if "analysis" in body or "preset" in body:
                    # Un réglage d'analyse ne s'annule pas : il refait l'analyse.
                    engine.set_settings(body)
                    engine.history.clear()
                    self._json(engine.state())
                else:
                    self._edit("réglages", lambda: engine.set_settings(body), coalesce=True)
            elif route == "/api/tracks" and method == "PATCH":
                self._edit("correction d'un flou", lambda: engine.patch_track(self._body()))
            elif route == "/api/tracks" and method == "DELETE":
                self._edit("flou retiré", lambda: engine.delete_track(query["clip"], int(query["id"])))
            elif route == "/api/manual" and method == "POST":
                self._edit("flou ajouté", lambda: {"created": engine.save_manual(self._body())})
            elif route == "/api/review" and method == "PUT":
                self._edit("validation", lambda: engine.set_review_done(self._body()))
            elif route == "/api/marks" and method == "POST":
                self._edit("signalement", lambda: {"created": engine.add_mark(self._body())})
            elif route == "/api/marks" and method == "DELETE":
                self._edit("signalement retiré", lambda: engine.delete_mark(int(query["id"])))
            elif route == "/api/dash" and method == "POST":
                body = self._body()
                if body.get("mode") in ("fixed", "clear"):
                    self._edit(f"objet « {dash.object_name(body.get('label'))} »", lambda: engine.start_dash(body))
                else:
                    engine.start_dash(body)  # le suivi s'annule comme un tout, à la fin du traitement
                    self._json(engine.state())
            elif route == "/api/follow" and method == "POST":
                engine.start_follow(self._body())
                self._json(engine.state())
            elif route == "/api/contour" and method == "POST":
                body = self._body()
                if body.get("clear"):
                    self._edit("retour à la forme simple", lambda: engine.start_contour(body))
                else:
                    engine.start_contour(body)
                    self._json(engine.state())
            elif route == "/api/batch" and method == "POST":
                engine.start_batch(self._body())
                self._json(engine.state())
            elif route == "/api/tune" and method == "GET":
                self._json(tuning.suggest(engine.require()))
            elif route == "/api/dataset" and method == "POST":
                engine.start_dataset()
                self._json(engine.state())
            elif route == "/api/similar" and method == "GET":
                self._json({"ids": engine.similar(query["clip"], int(query["id"]))})
            elif route == "/api/ignore" and method == "POST":
                body = self._body()
                self._edit("zone à ne jamais flouter", lambda: {"ignored": engine.add_ignore_zone(body)})
            elif route == "/api/ignore" and method == "DELETE":
                self._edit("zone à ne jamais flouter retirée",
                           lambda: {"restored": engine.remove_ignore_zone(query["clip"], int(query["index"]))})
            elif route == "/api/undo" and method == "POST":
                label = engine.step(forward=False)  # avant de lire l'état, qui doit en tenir compte
                self._json({**engine.state(), "undone": label})
            elif route == "/api/redo" and method == "POST":
                label = engine.step(forward=True)
                self._json({**engine.state(), "redone": label})
            elif route == "/api/sweep" and method == "PUT":
                engine.set_sweep(self._body())
                self._json(engine.state())
            elif route == "/api/estimate" and method == "POST":
                engine.start_estimate(self._body().get("device", "auto"))
                self._json(engine.state())
            elif route == "/api/thumb" and method == "GET":
                data = engine.thumbnail(query["clip"], int(query["id"]), int(query.get("w", 220)))
                self._send(200, data, "image/jpeg", {"Cache-Control": "private, max-age=3600"})
            elif route == "/api/filmstrip" and method == "GET":
                project = engine.require()
                data = media.filmstrip(project, project.clip(query["clip"]), int(query.get("count", 40)), int(query.get("h", 54)))
                self._send(200, data, "image/jpeg", {"Cache-Control": "private, max-age=3600"})
            elif route == "/api/waveform" and method == "GET":
                project = engine.require()
                self._json({"peaks": media.waveform(project, project.clip(query["clip"]), int(query.get("points", 1500)))})
            elif route == "/api/snapshot" and method == "POST":
                self._json(engine.snapshot_image(self._body()))
            elif route == "/api/recent" and method == "GET":
                self._json({"projects": library.recent()})
            elif route == "/api/storage" and method == "GET":
                self._json(library.storage(engine.project))
            elif route == "/api/storage" and method == "DELETE":
                freed = library.delete_previews(query["path"], engine.project)
                self._json({**library.storage(engine.project), "freed": freed})
            elif route == "/api/profile/save" and method == "POST":
                self._json({"profile": library.save_profile(engine.require())})
            elif route == "/api/profile/apply" and method == "POST":
                engine.start_profile()
                self._json(engine.state())
            elif route == "/api/zones" and method == "GET":
                self._json(
                    engine.zones(
                        query["clip"], int(query["from"]), int(query["to"]), query.get("dropped") == "1"
                    )
                )
            elif route == "/api/frame" and method == "GET":
                data = engine.frame_jpeg(
                    query["clip"], int(query["frame"]), query.get("blur") == "1", int(query.get("w", 0))
                )
                self._send(200, data, "image/jpeg")
            elif route == "/api/proxies" and method == "POST":
                engine.start_proxies()
                self._json(engine.state())
            elif route == "/api/analyze" and method == "POST":
                engine.start_analysis(self._body().get("device", "auto"))
                self._json(engine.state())
            elif route == "/api/render" and method == "POST":
                engine.start_render(self._body())
                self._json(engine.state())
            elif route == "/api/reveal" and method == "POST":
                self._json(engine.reveal(self._body()))
            elif route == "/api/cancel" and method == "POST":
                if engine.job and engine.job.state == "running":
                    engine.job.cancel = True
                self._json(engine.state())
            elif route.startswith("/media/") and method in ("GET", "HEAD"):
                project = engine.require()
                clip = project.clip(route[len("/media/") :])
                self._file(proxy_path(project, clip), "video/mp4")
            elif method in ("GET", "HEAD") and not route.startswith("/api/"):
                name = route.lstrip("/") or "index.html"
                target = (UI_DIR / name).resolve()
                if UI_DIR.resolve() not in target.parents or not target.is_file():
                    target = UI_DIR / "index.html"
                if not target.is_file():
                    raise ApiError("interface absente de cette installation (dossier ui).", 404)
                self._file(target)
            else:
                raise ApiError("adresse inconnue.", 404)
        except ApiError as exc:
            self._json({"error": str(exc)}, exc.status)
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json({"error": f"requête invalide : {exc}"}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")


class QuietServer(ThreadingHTTPServer):
    """Serveur qui ne signale pas les connexions fermées par le navigateur.

    Un navigateur ouvre des connexions d'avance et les abandonne sans prévenir, surtout pendant
    la lecture vidéo : c'est normal et sans conséquence, inutile d'afficher une erreur.
    """

    def handle_error(self, request, client_address) -> None:
        import sys

        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def make_server(engine: Engine, port: int = 0) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"engine": engine})
    server = QuietServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server


def _exit_when_parent_closes() -> None:
    """Arrête le moteur quand l'application de bureau qui l'a lancé se ferme.

    L'application garde ouvert le tube relié à l'entrée standard du moteur ; quand elle
    disparaît (fermeture normale ou plantage), la lecture se termine et le moteur s'arrête.
    """
    import sys

    def watch() -> None:
        try:
            while sys.stdin.buffer.read(1024):
                pass
        except (OSError, ValueError):
            pass
        os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


def serve(
    project_path: Path | None, port: int = 0, open_browser: bool = True, exit_with_parent: bool = False
) -> None:
    if exit_with_parent:
        _exit_when_parent_closes()
    engine = Engine()
    if project_path is not None:
        engine.open_project(project_path)
    server = make_server(engine, port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    # Ligne lue par l'application de bureau pour connaître le port : ne pas en changer le format.
    print(f"MOTO_BLUR_UI {url}", flush=True)
    if open_browser:
        print("Interface ouverte dans le navigateur. Ctrl+C dans ce terminal pour arrêter.", flush=True)
    if open_browser:
        import webbrowser

        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
    finally:
        server.server_close()


__all__ = ["Engine", "make_server", "serve"]
