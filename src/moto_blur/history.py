"""Annuler / rétablir les modifications faites dans l'interface.

Avant chaque modification (montage, réglages de flou, correction d'une zone, objet désigné…),
l'état modifiable du projet est mémorisé. « Annuler » le restaure ; « Rétablir » revient en
avant. Les détections issues de l'analyse ne sont jamais modifiées par ces actions (seuls leurs
réglages le sont : ignorée, forcée, prolongée) : elles ne sont donc pas recopiées, ce qui garde
l'historique léger même sur une longue vidéo.

Une nouvelle analyse vide l'historique : elle remplace les détections, et un état antérieur
ne pourrait plus être restauré fidèlement.
"""

from __future__ import annotations

import copy
from dataclasses import asdict

from . import project as pj

MAX_STEPS = 60


def snapshot(project: pj.Project) -> dict:
    """État modifiable du projet (tout sauf les détections automatiques et les réglages d'analyse)."""
    edits: dict[str, dict[int, tuple]] = {}
    extra: dict[str, list[pj.Track]] = {}
    for clip_id, state in project.analysis.items():
        edits[clip_id] = {
            t.id: (t.disabled, t.forced, t.extend_before_s, t.extend_after_s)
            for t in state.tracks if t.source == "auto"
        }
        extra[clip_id] = [copy.deepcopy(t) for t in state.tracks if t.source != "auto"]
    return {
        "timeline": [asdict(s) for s in project.timeline],
        "mutes": [asdict(m) for m in project.mutes],
        "blur": asdict(project.blur_settings),
        "conform": project.conform,
        "review_done": list(project.review_done),
        "marks": copy.deepcopy(project.marks),
        "sweep": dict(project.sweep),
        "ignore": copy.deepcopy(project.ignore),
        "edits": edits,
        "extra": extra,
    }


def restore(project: pj.Project, snap: dict) -> None:
    project.timeline = [pj.Segment(**s) for s in snap["timeline"]]
    project.mutes = [pj.Mute(**m) for m in snap["mutes"]]
    project.blur_settings = pj.BlurSettings(**snap["blur"])
    project.conform = snap["conform"]
    project.review_done = list(snap["review_done"])
    project.marks = copy.deepcopy(snap["marks"])
    project.sweep = dict(snap["sweep"])
    project.ignore = copy.deepcopy(snap.get("ignore", {}))
    for clip_id, state in project.analysis.items():
        edits = snap["edits"].get(clip_id, {})
        auto = [t for t in state.tracks if t.source == "auto"]
        for t in auto:
            if t.id in edits:
                t.disabled, t.forced, t.extend_before_s, t.extend_after_s = edits[t.id]
        state.tracks = auto + [copy.deepcopy(t) for t in snap["extra"].get(clip_id, [])]


class History:
    """Pile d'annulation d'un projet ouvert."""

    def __init__(self) -> None:
        self.undo: list[tuple[str, dict]] = []
        self.redo: list[tuple[str, dict]] = []

    def record(self, project: pj.Project, label: str) -> None:
        """À appeler AVANT une modification : mémorise l'état courant sous le nom de l'action."""
        self.undo.append((label, snapshot(project)))
        del self.undo[:-MAX_STEPS]
        self.redo.clear()

    def forget_last(self) -> None:
        """Retire la dernière mémorisation (l'action annoncée n'a finalement rien changé)."""
        if self.undo:
            self.undo.pop()

    def clear(self) -> None:
        self.undo.clear()
        self.redo.clear()

    def step_back(self, project: pj.Project) -> str | None:
        if not self.undo:
            return None
        label, snap = self.undo.pop()
        self.redo.append((label, snapshot(project)))
        restore(project, snap)
        return label

    def step_forward(self, project: pj.Project) -> str | None:
        if not self.redo:
            return None
        label, snap = self.redo.pop()
        self.undo.append((label, snapshot(project)))
        restore(project, snap)
        return label

    def labels(self) -> dict:
        return {
            "undo": self.undo[-1][0] if self.undo else None,
            "redo": self.redo[-1][0] if self.redo else None,
        }
