"""Noms de l'application : dossier, fichiers projet, variables d'environnement.

L'application s'appelait « moto-blur » : les anciens noms restent reconnus (projets
`.motoblur.json`, dossier `~/.moto-blur`, variables `MOTO_BLUR_*`), pour que rien ne se
perde chez ceux qui l'utilisaient déjà.
"""

from __future__ import annotations

import os
from pathlib import Path

APP = "Flouto"
PROJECT_SUFFIX = ".flouto.json"
OLD_PROJECT_SUFFIX = ".motoblur.json"
PROJECT_SUFFIXES = (PROJECT_SUFFIX, OLD_PROJECT_SUFFIX)


def env(name: str) -> str | None:
    """Variable `FLOUTO_<name>`, ou à défaut l'ancienne `MOTO_BLUR_<name>`."""
    return os.environ.get(f"FLOUTO_{name}") or os.environ.get(f"MOTO_BLUR_{name}")


def home() -> Path:
    """Dossier de l'application (`~/.flouto`). L'ancien `~/.moto-blur` est renommé la
    première fois ; s'il ne peut pas l'être, il continue d'être utilisé tel quel."""
    custom = env("HOME")
    if custom:
        return Path(custom)
    folder = Path.home() / ".flouto"
    old = Path.home() / ".moto-blur"
    if not folder.exists() and old.is_dir():
        try:
            old.rename(folder)
        except OSError:
            return old
    return folder


def is_project(path: Path | str) -> bool:
    return str(path).endswith(PROJECT_SUFFIXES)


def project_name(path: Path | str) -> str:
    """Nom lisible d'un projet : `trajet.flouto.json` → `trajet`."""
    name = Path(path).name
    for suffix in PROJECT_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name[:-5] if name.endswith(".json") else name


def project_for(video: Path) -> Path:
    """Fichier projet d'une vidéo : le nouveau nom, sauf si seul un ancien projet existe."""
    new = video.with_name(video.stem + PROJECT_SUFFIX)
    old = video.with_name(video.stem + OLD_PROJECT_SUFFIX)
    if not new.exists() and old.exists():
        return old
    return new
