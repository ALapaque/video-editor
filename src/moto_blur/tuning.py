"""Mesurer au lieu de deviner : ce qu'un réglage donnerait sur un projet déjà corrigé.

Un projet que vous avez relu contient de quoi juger un réglage :
- les flous que vous avez RETIRÉS (ou écartés par une zone « ne jamais flouter ») sont des
  erreurs du logiciel : un bon réglage ne les produit plus ;
- les flous que vous avez FORCÉS, ajoutés à la main ou fait suivre sont des objets à cacher :
  un bon réglage ne les perd pas ;
- les flous que vous avez LAISSÉS sont présumés bons. Ce n'est pas une certitude (un flou
  inutile a pu vous échapper), mais l'erreur va dans le sens de la prudence : le réglage
  proposé gardera ce flou.

Deux usages :
- `suggest` : sans rien réanalyser, cherche les seuils de flou qui écartent le plus d'erreurs
  sans perdre un seul flou gardé ;
- `measure` : réanalyse le projet avec d'autres réglages d'analyse (modèle, préréglage, tuiles)
  et compare le résultat au projet corrigé, vitesse comprise.
"""

from __future__ import annotations

import copy
import time
from dataclasses import asdict, replace
from pathlib import Path

from . import project as pj
from .postprocess import ZoneIndex, below_min_score, is_blurred

COVERED = 0.9  # part de la boîte d'un objet qui doit être sous un flou pour qu'il compte comme caché
KEPT_SHARE = 0.8  # part des images d'un objet où il doit être caché pour ne pas compter comme perdu


def _would_blur(track: pj.Track, blur: pj.BlurSettings) -> bool:
    """Le réglage flouterait-il cet objet, sans tenir compte des corrections faites à la main ?"""
    if not track.obs:
        return False
    plain = replace(track, disabled=False, forced=False)
    return not below_min_score(plain, blur)


def truth(project: pj.Project) -> dict:
    """Ce que les corrections disent des détections : bonnes, fausses, ou jamais regardées."""
    right, wrong, unknown, added = [], [], [], []
    blur = project.blur_settings
    for state in project.analysis.values():
        for t in state.tracks:
            if not t.obs:
                continue
            if t.source != "auto":
                added.append(t)
            elif t.disabled:
                wrong.append(t)
            elif t.forced or is_blurred(t, blur):
                right.append(t)
            else:
                unknown.append(t)  # vu, pas flouté, jamais regardé
    return {"right": right, "wrong": wrong, "unknown": unknown, "added": added}


def evaluate(project: pj.Project, blur: pj.BlurSettings, facts: dict | None = None) -> dict:
    """Effet d'un réglage de flou sur les détections existantes (rien n'est réanalysé)."""
    facts = facts or truth(project)
    lost = [t for t in facts["right"] if not _would_blur(t, blur)]
    still = [t for t in facts["wrong"] if _would_blur(t, blur)]
    new = [t for t in facts["unknown"] if _would_blur(t, blur)]
    return {
        "kept": len(facts["right"]) - len(lost), "lost": len(lost),
        "wrong_avoided": len(facts["wrong"]) - len(still), "wrong_left": len(still),
        "new": len(new),
    }


TUNED = ("face_min_score", "plate_min_score", "min_hits")


def suggest(project: pj.Project) -> dict:
    """Seuils de flou qui écartent le plus d'erreurs connues sans perdre un seul flou gardé.

    Renvoie {"usable", "reason", "now", "best", "settings"} ; `settings` ne contient que les
    réglages à changer. À égalité, le réglage le plus proche de l'actuel est retenu.
    """
    facts = truth(project)
    current = project.blur_settings
    now = evaluate(project, current, facts)
    base = {"now": now, "corrections": len(facts["wrong"]), "kept": len(facts["right"])}
    if not facts["wrong"]:
        return {**base, "usable": False, "settings": {}, "best": now,
                "reason": "Vous n'avez retiré aucun flou dans ce projet : il n'y a rien à apprendre."}
    kinds = {t.kind for t in facts["right"] + facts["wrong"]}
    faces = [round(0.20 + 0.05 * i, 2) for i in range(11)] if "face" in kinds else [current.face_min_score]
    plates = [round(0.10 + 0.05 * i, 2) for i in range(11)] if "plate" in kinds else [current.plate_min_score]
    best = None
    for hits in range(1, 7):
        for face in faces:
            for plate in plates:
                trial = replace(current, face_min_score=face, plate_min_score=plate, min_hits=hits)
                result = evaluate(project, trial, facts)
                if result["lost"]:
                    continue  # jamais au prix d'un flou gardé
                distance = (abs(face - current.face_min_score) + abs(plate - current.plate_min_score)
                            + 0.05 * abs(hits - current.min_hits))
                rank = (result["wrong_left"], result["new"], distance)
                if best is None or rank < best[0]:
                    best = (rank, trial, result)
    if best is None or best[2]["wrong_left"] >= now["wrong_left"]:
        return {**base, "usable": False, "settings": {}, "best": now,
                "reason": "Aucun réglage n'écarte plus d'erreurs sans perdre un flou que vous avez gardé."}
    changed = {k: getattr(best[1], k) for k in TUNED if getattr(best[1], k) != getattr(current, k)}
    return {**base, "usable": True, "reason": "", "best": best[2], "settings": changed}


# --- Comparaison avec une nouvelle analyse --------------------------------------------------


def _coverage(track: pj.Track, index: ZoneIndex) -> float:
    """Part des images de l'objet où un flou du candidat le couvre presque entièrement."""
    hits = 0
    for o in track.obs:
        x1, y1, x2, y2 = o[1:5]
        area = max(1.0, (x2 - x1) * (y2 - y1))
        for _kind, (zx1, zy1, zx2, zy2) in index.zones(o[0]):
            w = min(x2, zx2) - max(x1, zx1)
            h = min(y2, zy2) - max(y1, zy1)
            if w > 0 and h > 0 and w * h >= COVERED * area:
                hits += 1
                break
    return hits / len(track.obs)


def compare(reference: pj.Project, candidate: pj.Project) -> dict:
    """Ce que le candidat (même vidéo, autres réglages) donne par rapport au projet corrigé."""
    out = {"kept": 0, "lost": 0, "added_caught": 0, "added_missed": 0, "wrong_back": 0,
           "wrong_gone": 0, "blurs": 0, "lost_ids": []}
    for clip in reference.clips:
        state = candidate.analysis.get(clip.id)
        tracks = state.tracks if state else []
        index = ZoneIndex(tracks, candidate.blur_settings, clip.video)
        out["blurs"] += sum(1 for t in tracks if t.source == "auto" and is_blurred(t, candidate.blur_settings))
        ref = reference.analysis.get(clip.id)
        facts = truth(pj.Project(clips=[clip], blur_settings=reference.blur_settings,
                                 analysis={clip.id: ref} if ref else {}))
        for t in facts["right"]:
            if _coverage(t, index) >= KEPT_SHARE:
                out["kept"] += 1
            else:
                out["lost"] += 1
                out["lost_ids"].append(t.id)
        for t in facts["added"]:
            out["added_caught" if _coverage(t, index) >= KEPT_SHARE else "added_missed"] += 1
        for t in facts["wrong"]:
            out["wrong_back" if _coverage(t, index) >= 0.5 else "wrong_gone"] += 1
    return out


def measure(project: pj.Project, change, device: str = "auto") -> dict:
    """Réanalyse une copie du projet après `change(copie)` et la compare au projet corrigé.

    Le projet d'origine n'est jamais modifié ; la copie est écrite à côté de lui le temps de la
    mesure, puis supprimée. Renvoie le résultat de `compare`, plus {"seconds", "frames", "fps"}.
    """
    from .analyze import analyze_project, frames_to_analyze

    if project.path is None:
        raise ValueError("le projet doit être enregistré.")
    trial = pj.Project(
        clips=copy.deepcopy(project.clips), timeline=copy.deepcopy(project.timeline),
        analysis_settings=replace(project.analysis_settings, kinds=list(project.analysis_settings.kinds)),
        blur_settings=replace(project.blur_settings), ignore=copy.deepcopy(project.ignore),
        preset=project.preset,
    )
    trial.path = Path(project.path).with_name(Path(project.path).name.replace(".motoblur.json", "") + ".mesure.motoblur.json")
    change(trial)
    frames = frames_to_analyze(trial)
    started = time.perf_counter()
    try:
        complete = analyze_project(trial, device=device)
    finally:
        trial.path.unlink(missing_ok=True)
    seconds = time.perf_counter() - started
    if not complete:
        raise RuntimeError("mesure interrompue.")
    result = compare(project, trial)
    result.update(seconds=round(seconds, 1), frames=frames, fps=round(frames / max(seconds, 1e-9), 2),
                  settings={"analysis": asdict(trial.analysis_settings), "preset": trial.preset})
    return result


def describe(result: dict) -> str:
    lines = [
        f"  flous gardés par vous, toujours cachés : {result['kept']}",
        f"  flous gardés par vous, PERDUS          : {result['lost']}"
        + (f"  (objets n° {', '.join(str(i) for i in result['lost_ids'][:12])})" if result["lost"] else ""),
        f"  objets ajoutés par vous, trouvés seul  : {result['added_caught']} sur {result['added_caught'] + result['added_missed']}",
        f"  faux flous retirés par vous, revenus   : {result['wrong_back']} sur {result['wrong_back'] + result['wrong_gone']}",
        f"  flous au total                         : {result['blurs']}",
    ]
    if "seconds" in result:
        lines.append(f"  durée de l'analyse                     : {result['seconds']:.0f} s pour {result['frames']} images ({result['fps']:.1f} images par seconde)")
    return "\n".join(lines)
