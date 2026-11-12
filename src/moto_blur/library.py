"""Ce que le logiciel retient d'une vidéo à l'autre : projets récents, place occupée par les
aperçus, et profil « ma moto » (réglages et objets à flouter réutilisés sur chaque nouvelle vidéo).

Tout est rangé dans le dossier de l'application (`~/.moto-blur`), jamais dans les vidéos.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import time
from dataclasses import asdict
from pathlib import Path

import av
import cv2

from . import dash
from . import project as pj
from .models import models_dir
from .progress import Progress
from .proxy import proxy_dir
from .video import iter_frames

MAX_RECENT = 12
PROFILE_SAMPLES = 30  # images examinées pour retrouver un objet du profil dans une nouvelle vidéo


def app_dir() -> Path:
    env = os.environ.get("MOTO_BLUR_HOME")
    folder = Path(env) if env else Path.home() / ".moto-blur"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# --- Projets récents --------------------------------------------------------


def _recent_file() -> Path:
    return app_dir() / "recents.json"


def _read_recent() -> list[str]:
    try:
        data = json.loads(_recent_file().read_text(encoding="utf-8"))
        return [str(p) for p in data if isinstance(p, str)]
    except (OSError, ValueError):
        return []


def remember(project_path: Path) -> None:
    path = str(Path(project_path).resolve())
    paths = [path] + [p for p in _read_recent() if p != path]
    _recent_file().write_text(json.dumps(paths[:MAX_RECENT], ensure_ascii=False, indent=1), encoding="utf-8")


def recent() -> list[dict]:
    """Projets ouverts récemment qui existent encore, du plus récent au plus ancien."""
    out = []
    for p in _read_recent():
        path = Path(p)
        if path.is_file():
            out.append({"path": p, "name": path.name.replace(".motoblur.json", ""), "folder": str(path.parent),
                        "modified": int(path.stat().st_mtime)})
    return out


# --- Place occupée ----------------------------------------------------------


def _size(folder: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            with contextlib.suppress(OSError):
                total += (Path(root) / name).stat().st_size
    return total


def storage(current: pj.Project | None) -> dict:
    """Place prise par les fichiers d'aperçu des projets connus, et par les modèles."""
    folders = {Path(r["folder"]) for r in recent()}
    if current is not None and current.path is not None:
        folders.add(current.path.resolve().parent)
    mine = proxy_dir(current).resolve() if current is not None and current.path is not None else None
    previews = []
    for folder in sorted(folders):
        try:
            entries = sorted(p for p in folder.iterdir() if p.is_dir() and p.name.endswith(".proxies"))
        except OSError:
            continue
        for entry in entries:
            project_file = entry.with_name(entry.name[: -len(".proxies")] + ".json")
            previews.append({
                "path": str(entry), "name": entry.name[: -len(".proxies")].replace(".motoblur", ""),
                "size": _size(entry), "current": mine is not None and entry.resolve() == mine,
                "orphan": not project_file.exists(),
            })
    return {"previews": previews, "models": _size(models_dir()) if models_dir().exists() else 0}


def delete_previews(path: str, current: pj.Project | None) -> int:
    """Supprime un dossier d'aperçus listé par `storage` (jamais celui du projet ouvert).

    Renvoie la place libérée. Les aperçus se refabriquent à la prochaine ouverture du projet.
    """
    known = {p["path"]: p for p in storage(current)["previews"]}
    entry = known.get(str(Path(path)))
    if entry is None:
        raise ValueError("dossier d'aperçus inconnu.")
    if entry["current"]:
        raise ValueError("ce sont les aperçus du projet ouvert : fermez-le d'abord.")
    shutil.rmtree(entry["path"])
    return int(entry["size"])


# --- Profil « ma moto » -----------------------------------------------------


def _profile_file() -> Path:
    return app_dir() / "profil.json"


def profile_summary() -> dict | None:
    """Ce que contient le profil enregistré (None s'il n'y en a pas)."""
    try:
        data = json.loads(_profile_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {
        "saved_at": data.get("saved_at", 0), "preset": data.get("preset", "normal"),
        "kinds": data.get("kinds", []), "objects": [o["name"] for o in data.get("objects", [])],
    }


def save_profile(project: pj.Project) -> dict:
    """Enregistre les réglages du projet et l'aspect de ses objets suivis comme profil."""
    objects = []
    folder = app_dir() / "profil"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    for clip in project.clips:
        v = clip.video
        for name in dash.object_names(project, clip):
            if name in seen:
                continue
            seen.add(name)
            fixed = dash.dash_tracks(project, clip, "manual", name)
            if fixed:
                x1, y1, x2, y2 = fixed[0].obs[0][1:5]
                objects.append({"name": name, "mode": "fixed",
                                "box": [x1 / v.width, y1 / v.height, x2 / v.width, y2 / v.height]})
                continue
            tracked = [t for t in dash.dash_tracks(project, clip, "tracker", name) if t.obs]
            if not tracked:
                continue
            # Image de référence : au milieu du morceau désigné à la main, là où le suivi est sûr.
            piece = next((t for t in tracked if t.reacquired_at is None), tracked[0])
            sure = [o for o in piece.obs if o[5] >= 0.5] or piece.obs
            obs = sure[len(sure) // 2]
            with av.open(str(project.clip_path(clip))) as container:
                frame = next(iter_frames(container, int(obs[0]), v.end_pts + 1), None)
            if frame is None:
                continue
            scale = dash.TRACK_WIDTH / v.width
            image = frame.reformat(width=dash.TRACK_WIDTH, height=max(2, round(v.height * scale / 2) * 2),
                                   format="bgr24").to_ndarray()
            x1, y1, x2, y2 = (round(c * scale) for c in obs[1:5])
            patch = image[max(0, y1) : y2, max(0, x1) : x2]
            if patch.shape[0] < 6 or patch.shape[1] < 6:
                continue
            file = f"objet-{len(objects)}.png"
            cv2.imwrite(str(folder / file), patch)
            objects.append({"name": name, "mode": "track", "template": file})
    data = {
        "saved_at": int(time.time()), "preset": project.preset, "kinds": list(project.analysis_settings.kinds),
        "blur": asdict(project.blur_settings), "objects": objects,
    }
    _profile_file().write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return profile_summary() or {}


def find_object(project: pj.Project, clip: pj.Clip, template, progress: Progress | None = None):
    """Cherche un objet du profil dans un clip. Renvoie (pts, boîte source, ressemblance) ou None."""
    span = dash.dash_span(project, clip)
    if span is None:
        return None
    v = clip.video
    scale = dash.TRACK_WIDTH / v.width
    size = (dash.TRACK_WIDTH, max(2, round(v.height * scale / 2) * 2))
    appearance = dash._Appearance.from_patch(template)
    best = None
    step = max(1, (span[1] - span[0]) // PROFILE_SAMPLES)
    with av.open(str(project.clip_path(clip))) as container:
        for pts in range(span[0], span[1], step):
            frame = next(iter_frames(container, pts, v.end_pts + 1), None)
            if progress:
                progress.step()
            if frame is None:
                continue
            found = appearance.search(frame.reformat(width=size[0], height=size[1], format="bgr24").to_ndarray())
            if found and (best is None or found[0] > best[2]):
                x, y, w, h = found[1]
                best = (frame.pts, (x / scale, y / scale, (x + w) / scale, (y + h) / scale), found[0])
            if best and best[2] >= 0.9:
                break  # trouvé nettement : inutile de chercher plus loin
    return best if best and best[2] >= dash.REACQUIRE_MIN else None


def apply_profile(project: pj.Project, progress: Progress | None = None) -> list[str]:
    """Applique le profil au projet : réglages, puis recherche et suivi de chaque objet connu.

    Renvoie des phrases décrivant ce qui a été fait, objet par objet.
    """
    from .analyze import apply_preset

    try:
        data = json.loads(_profile_file().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("aucun profil enregistré.") from exc
    notes = []
    apply_preset(project, data.get("preset", "normal"))
    names = {f for f in pj.BlurSettings.__dataclass_fields__}
    project.blur_settings = pj.BlurSettings(**{k: v for k, v in data.get("blur", {}).items() if k in names})
    kinds = [k for k in pj.KINDS if k in data.get("kinds", list(pj.KINDS))]
    if kinds != project.analysis_settings.kinds:
        project.analysis_settings.kinds = kinds
    notes.append("Réglages du profil appliqués.")
    for clip in project.clips:
        v = clip.video
        existing = set(dash.object_names(project, clip))
        for obj in data.get("objects", []):
            name = obj["name"]
            if name in existing:
                continue
            if obj["mode"] == "fixed":
                x1, y1, x2, y2 = obj["box"]
                dash.set_fixed_zone(project, clip, (round(x1 * v.width), round(y1 * v.height),
                                                    round(x2 * v.width), round(y2 * v.height)), name)
                notes.append(f"« {name} » : zone fixe reprise du profil.")
                continue
            template = cv2.imread(str(app_dir() / "profil" / obj["template"]), cv2.IMREAD_GRAYSCALE)
            if template is None:
                notes.append(f"« {name} » : image de référence introuvable dans le profil.")
                continue
            found = find_object(project, clip, template, progress)
            if found is None:
                notes.append(f"« {name} » : non retrouvé dans {Path(clip.path).name}. Désignez-le à la main.")
                continue
            pts, box, similarity = found
            tracks = dash.track_from_click(project, clip, pts, box, "both", progress, name, "all")
            notes.append(f"« {name} » : retrouvé (ressemblance {similarity:.2f}). " + dash.describe(name, tracks, clip))
    return notes
