"""Rapport texte des passages douteux, avec horodatages source et montage."""

from __future__ import annotations

from . import project as pj
from .timeline import format_time, pts_to_sec, resolve_segments, source_to_timeline


def build_report(project: pj.Project) -> str:
    segments = resolve_segments(project)
    lines = ["PASSAGES À VÉRIFIER", ""]
    lines.append(
        "Le logiciel signale ici les flous dont il n'est pas sûr. Ces passages sont floutés quand"
    )
    lines.append(
        "même ; vérifiez-les dans la vidéo debug ou la vidéo finale. Attention : un objet qui n'a"
    )
    lines.append("jamais été détecté n'apparaît pas dans ce rapport.")
    lines.append("")
    shown = 0
    hidden = 0
    for clip in project.clips:
        items = [r for r in project.review if r["clip"] == clip.id]
        state = project.analysis.get(clip.id)
        counts = {k: 0 for k in pj.KINDS}
        for t in state.tracks if state else []:
            counts[t.kind] = counts.get(t.kind, 0) + 1
        summary = ", ".join(f"{n} {pj.KIND_LABELS[k]}{'s' if n > 1 else ''}" for k, n in counts.items())
        lines.append(f"Clip {clip.id} — {clip.path}")
        lines.append(f"  objets suivis : {summary}")
        visible = []
        for r in items:
            where = source_to_timeline(segments, clip.id, r["start_pts"]) or source_to_timeline(
                segments, clip.id, r["end_pts"]
            )
            if not where:
                hidden += 1  # passage coupé au montage : sans objet
                continue
            visible.append((r, where[0]))
        if not visible:
            lines.append("  aucun passage douteux dans les passages gardés.")
        for r, at in visible:
            shown += 1
            kinds = " + ".join(pj.KIND_LABELS[k] for k in r["kinds"])
            lines.append(
                f"  {format_time(pts_to_sec(r['start_pts'], clip.video))} → "
                f"{format_time(pts_to_sec(r['end_pts'], clip.video))} (source)   "
                f"montage {format_time(at)}   {kinds}"
            )
            lines.append(f"      {' ; '.join(r['reasons'])}")
        lines.append("")
    lines.append(f"Total : {shown} passage{'s' if shown != 1 else ''} à vérifier.")
    if hidden:
        lines.append(f"({hidden} passage{'s' if hidden != 1 else ''} douteux ignoré{'s' if hidden != 1 else ''} car coupé{'s' if hidden != 1 else ''} au montage.)")
    return "\n".join(lines) + "\n"
