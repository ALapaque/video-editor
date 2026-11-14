"""File d'attente : analyser plusieurs vidéos à la suite, sans rester devant l'écran.

Chaque vidéo a son propre projet, créé à côté d'elle s'il n'existe pas. Les vidéos sont
traitées l'une après l'autre ; une vidéo illisible ou une erreur n'arrête pas les suivantes.
Le lendemain, les projets analysés se retrouvent dans les projets récents.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import av

from . import library
from . import project as pj
from .analyze import analyze_project, apply_ignore_zones, apply_preset, refresh_review
from .video import probe

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".mts", ".webm"}


def open_or_create(path: Path) -> pj.Project:
    """Projet d'une vidéo (créé à côté d'elle s'il n'existe pas), ou projet existant."""
    path = Path(path).expanduser()
    if path.suffix.lower() == ".json":
        if not path.exists():
            raise ValueError(f"projet introuvable : {path}")
        return pj.load(path)
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError(f"ni une vidéo ni un projet : {path.name}")
    if not path.exists():
        raise ValueError(f"fichier introuvable : {path}")
    project_path = path.with_name(path.stem + ".motoblur.json")
    if project_path.exists():
        return pj.load(project_path)
    project = pj.Project(path=project_path)
    try:
        video, audio = probe(path)
    except (av.FFmpegError, ValueError) as exc:
        raise ValueError(f"vidéo illisible : {exc}") from exc
    clip = pj.add_clip(project, path, video, audio)
    project.timeline.append(pj.Segment(clip.id, 0.0, round(video.duration_s, 6)))
    pj.save(project)
    return project


def run_batch(
    paths: list[Path], preset: str | None = None, use_profile: bool = False, device: str = "auto",
    on_item: Callable[[int, int, str], None] | None = None,
    current: pj.Project | None = None, detect_fn=None,
) -> list[dict]:
    """Analyse chaque vidéo ou projet de la liste. Renvoie un résultat par élément :
    {"path", "name", "ok", "message", "seconds"}.

    `current` : le projet ouvert dans l'interface ; s'il figure dans la liste, c'est lui qui est
    mis à jour (et non une copie relue sur le disque). Une interruption (annulation) arrête la
    file ; ce qui est fait est gardé, y compris l'analyse partielle de la vidéo en cours.
    """
    results: list[dict] = []
    for i, raw in enumerate(paths):
        path = Path(raw).expanduser()
        name = path.name.replace(".motoblur.json", "")
        if on_item:
            on_item(i, len(paths), name)
        started = time.perf_counter()
        entry = {"path": str(path), "name": name, "ok": False, "message": "", "seconds": 0.0}
        results.append(entry)
        try:
            project = open_or_create(path)
            if current is not None and current.path is not None and project.path is not None \
                    and Path(current.path).resolve() == Path(project.path).resolve():
                project = current
            entry["path"] = str(project.path)
            if preset and preset != project.preset:
                apply_preset(project, preset)
            notes: list[str] = []
            if use_profile and library.profile_summary() and not any(
                t.source != "auto" for state in project.analysis.values() for t in state.tracks
            ):
                notes = library.apply_profile(project)
                pj.save(project)
            complete = analyze_project(project, device=device, detect_fn=detect_fn)
            if apply_ignore_zones(project):
                refresh_review(project)
            pj.save(project)
            library.remember(project.path)
            entry["seconds"] = round(time.perf_counter() - started, 1)
            if not complete:
                entry["message"] = "Analyse interrompue : ce qui est fait est conservé."
                break
            lost = [n for n in notes if "non retrouvé" in n]
            entry["ok"] = True
            entry["message"] = "Analysée." + (" " + " ".join(lost) if lost else "")
        except KeyboardInterrupt:
            entry["message"] = "Interrompue."
            break
        except Exception as exc:  # noqa: BLE001 - une vidéo en erreur n'arrête pas les suivantes
            entry["message"] = f"Erreur : {exc}"
    return results


def summary(results: list[dict], total: int) -> str:
    done = sum(1 for r in results if r["ok"])
    text = f"{done} vidéo{'s' if done > 1 else ''} analysée{'s' if done > 1 else ''} sur {total}."
    failed = [f"{r['name']} : {r['message']}" for r in results if not r["ok"]]
    if failed:
        text += " " + " ; ".join(failed)
    if done:
        text += " Vous les retrouverez dans les projets récents."
    return text
