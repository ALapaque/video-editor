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
import threading
import traceback
import urllib.parse
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import av
import cv2

from . import dash, progress
from . import project as pj
from .analyze import analyze_project, needed_intervals, refresh_review
from .blur import SUPPORTED_FORMATS, blur_frame
from .postprocess import ZoneIndex, below_min_score
from .proxy import build_proxy, proxy_path
from .render import RenderError, check_ready, formats_differ, render_project, used_clips
from .timeline import pts_to_sec, resolve_segments, sec_to_pts
from .video import iter_frames, probe

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".mts", ".webm"}
UI_DIR = Path(__file__).parent / "ui"


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

    def to_json(self) -> dict:
        return {
            "kind": self.kind, "label": self.label, "done": self.done, "total": self.total,
            "fps": round(self.fps, 1), "state": self.state, "message": self.message,
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

    # --- Projet -----------------------------------------------------------

    def require(self) -> pj.Project:
        if self.project is None:
            raise ApiError("aucun projet ouvert.", 409)
        return self.project

    def touch(self) -> None:
        self.version += 1
        pj.save(self.require())

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
                return {"project": None, "job": job, "version": self.version}
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
                    "mismatch": formats_differ(used_clips(project, resolve_segments(project))),
                    "tracks": tracks,
                    "problem": problem,
                },
                "job": job,
                "version": self.version,
            }

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
        }
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
            _, track = self._find_track(body["clip"], int(body["id"]))
            if "disabled" in body:
                track.disabled = bool(body["disabled"])
            if "forced" in body:
                track.forced = bool(body["forced"])
            for key in ("extend_before_s", "extend_after_s"):
                if key in body:
                    value = body[key]
                    setattr(track, key, None if value is None else max(0.0, min(float(value), 600.0)))
            self._after_zone_change()

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
            for block, target in (("analysis", project.analysis_settings), ("blur", project.blur_settings)):
                for key, value in body.get(block, {}).items():
                    if not hasattr(target, key):
                        raise ApiError(f"réglage inconnu : {key}")
                    current = getattr(target, key)
                    if isinstance(current, bool):
                        value = bool(value)
                    elif isinstance(current, int):
                        value = int(value)
                    elif isinstance(current, float):
                        value = float(value)
                    setattr(target, key, value)
            refresh_review(project)  # le score minimal change les passages à vérifier
            self.touch()

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
                for item in index.active(pts):
                    z = item.shaped(pts)
                    if z is None:
                        continue
                    obs = item.observation(pts)
                    items.append([*z.box, z.shape, z.radius, z.kind, z.track_id, None if obs is None else round(obs[4], 2)])
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

    def frame_jpeg(self, clip_id: str, frame_index: int, blur: bool, max_width: int) -> bytes:
        """Image source exacte, en pleine résolution, avec ou sans le flou du rendu final."""
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
        image = frame.to_ndarray(format="bgr24")
        if 0 < max_width < image.shape[1]:
            height = round(image.shape[0] * max_width / image.shape[1])
            image = cv2.resize(image, (max_width, height), interpolation=cv2.INTER_AREA)
        ok, data = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise ApiError("encodage de l'image impossible.", 500)
        return data.tobytes()

    # --- Traitements en arrière-plan --------------------------------------

    def _busy_check(self) -> None:
        if self.job and self.job.state == "running":
            raise ApiError("un traitement est déjà en cours.", 409)

    def start(self, kind: str, label: str, work) -> None:
        with self.lock:
            self._busy_check()
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
            complete = analyze_project(project, device=device)
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

        def work(job: Job) -> str:
            span = dash.dash_span(project, clip)
            if span is None:
                raise ApiError("ce clip n'a aucun passage gardé.")
            total = round((span[1] - span[0]) * clip.video.tb * clip.video.fps_fraction)
            p = progress.Progress(f"Suivi : {name}", total, float(clip.video.fps_fraction))
            track = dash.track_from_click(project, clip, seed, box, direction, p, name)
            refresh_review(project)
            pj.save(project)
            lost = sum(1 for o in track.obs if o[5] < dash.LOST_SCORE)
            return f"« {name} » suivi sur {len(track.obs)} images, incertain sur {lost}."

        self.start("dash", f"Suivi : {name}", work)

    def start_render(self, body: dict) -> None:
        project = self.require()
        output = Path(str(body.get("output", "")).strip()).expanduser()
        if not output.name:
            raise ApiError("indiquez un fichier de sortie.")
        if not output.is_absolute():
            output = project.path.parent / output
        if output.suffix.lower() not in (".mp4", ".mov", ".mkv"):
            output = output.with_name(output.name + ".mp4")
        quality = max(0, min(int(body.get("quality", 80)), 100))
        codec, encoder = body.get("codec", "source"), body.get("encoder", "auto")
        debug = bool(body.get("debug", False))

        def work(job: Job) -> str:
            render_project(project, output, quality, codec, encoder, debug)
            return f"Vidéo écrite : {output}"

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
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

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

    def _handle(self, method: str) -> None:
        engine = self.engine
        url = urllib.parse.urlparse(self.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        route = url.path
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
                engine.set_montage(self._body())
                self._json(engine.state())
            elif route == "/api/settings" and method == "PUT":
                engine.set_settings(self._body())
                self._json(engine.state())
            elif route == "/api/tracks" and method == "PATCH":
                engine.patch_track(self._body())
                self._json(engine.state())
            elif route == "/api/tracks" and method == "DELETE":
                engine.delete_track(query["clip"], int(query["id"]))
                self._json(engine.state())
            elif route == "/api/manual" and method == "POST":
                track_id = engine.save_manual(self._body())
                self._json({**engine.state(), "created": track_id})
            elif route == "/api/review" and method == "PUT":
                engine.set_review_done(self._body())
                self._json(engine.state())
            elif route == "/api/marks" and method == "POST":
                mark_id = engine.add_mark(self._body())
                self._json({**engine.state(), "created": mark_id})
            elif route == "/api/marks" and method == "DELETE":
                engine.delete_mark(int(query["id"]))
                self._json(engine.state())
            elif route == "/api/dash" and method == "POST":
                engine.start_dash(self._body())
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
