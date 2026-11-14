"""Ligne de commande de moto-blur.

    moto-blur analyze VIDEO [VIDEO…]   analyse et écrit le fichier projet
    moto-blur render  PROJET -o SORTIE produit la vidéo finale
    moto-blur run     VIDEO… -o SORTIE enchaîne les deux
    moto-blur ui      [VIDEO|PROJET]   ouvre l'interface de montage et de vérification
    moto-blur dash    PROJET --box …   floute le compteur (zone fixe ou suivi)
    moto-blur report  PROJET           affiche les passages à vérifier
    moto-blur probe   VIDEO            affiche les caractéristiques d'une vidéo
    moto-blur info                     affiche l'accélération disponible sur cette machine
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from . import project as pj
from .timeline import format_time, parse_time, resolve_segments, timeline_duration


def _project_path(args: argparse.Namespace) -> Path:
    if args.project:
        return Path(args.project)
    first = Path(args.inputs[0])
    if first.suffix.lower() == ".json":
        return first
    return first.with_name(first.stem + ".motoblur.json")


def _open_or_create(args: argparse.Namespace) -> pj.Project:
    """Ouvre le projet s'il existe, sinon le crée à partir des vidéos données."""
    from .video import probe

    path = _project_path(args)
    videos = [Path(p) for p in args.inputs if Path(p).suffix.lower() != ".json"]
    if path.exists():
        project = pj.load(path)
        known = {project.clip_path(c).resolve() for c in project.clips}
        extra = [v for v in videos if v.resolve() not in known]
        if extra:
            raise SystemExit(
                f"Erreur : {path} existe déjà et ne contient pas {extra[0]}. "
                "Supprimez-le ou choisissez un autre fichier avec --project."
            )
        print(f"Projet existant : {path}")
    else:
        if not videos:
            raise SystemExit(f"Erreur : fichier projet introuvable : {path}")
        project = pj.Project(path=path)
        for v in videos:
            if not v.exists():
                raise SystemExit(f"Erreur : vidéo introuvable : {v}")
            video, audio = probe(v)
            clip = pj.add_clip(project, v, video, audio)
            project.timeline.append(pj.Segment(clip.id, 0.0, round(video.duration_s, 6)))
        print(f"Nouveau projet : {path}")
    _apply_edit_options(project, args)
    _apply_settings(project, args)
    pj.save(project)
    return project


def _apply_edit_options(project: pj.Project, args: argparse.Namespace) -> None:
    """Options de montage en ligne de commande : rognage et passages à garder."""
    if getattr(args, "keep", None):
        if len(project.clips) != 1:
            raise SystemExit("Erreur : --keep ne fonctionne qu'avec un seul clip (sinon, éditez le fichier projet).")
        clip = project.clips[0]
        segments = []
        for spec in args.keep:
            try:
                a, b = spec.split("-", 1)
                start, end = parse_time(a), parse_time(b)
            except ValueError:
                raise SystemExit(f"Erreur : --keep attend DEBUT-FIN (ex. 0:10-1:25.5), reçu {spec!r}") from None
            if end <= start:
                raise SystemExit(f"Erreur : --keep {spec} : la fin doit être après le début.")
            segments.append(pj.Segment(clip.id, start, min(end, round(clip.video.duration_s, 6))))
        project.timeline = segments
    trim_start = getattr(args, "trim_start", None)
    trim_end = getattr(args, "trim_end", None)
    if (trim_start or trim_end) and not project.timeline:
        raise SystemExit("Erreur : le montage est vide, rien à rogner.")
    # Valeurs absolues (et non cumulées) : relancer la même commande donne le même montage.
    if trim_start:
        seg = project.timeline[0]
        seg.start = round(trim_start, 6)
        if seg.start >= seg.end:
            raise SystemExit("Erreur : --trim-start dépasse la durée du premier passage.")
    if trim_end:
        seg = project.timeline[-1]
        seg.end = round(project.clip(seg.clip).video.duration_s - trim_end, 6)
        if seg.end <= seg.start:
            raise SystemExit("Erreur : --trim-end dépasse la durée du dernier passage.")


def _apply_settings(project: pj.Project, args: argparse.Namespace) -> None:
    a, b = project.analysis_settings, project.blur_settings
    for name in ("face_model", "plate_model", "conf_new", "conf_keep", "tile_size", "detect_every"):
        value = getattr(args, name, None)
        if value is not None:
            setattr(a, name, value)
    if getattr(args, "no_full_pass", False):
        a.full_pass = False
    if getattr(args, "only", None):
        a.kinds = [args.only]
    if getattr(args, "conform", None):
        project.conform = args.conform
    for name in ("margin", "extend_s", "style", "face_min_score", "plate_min_score", "min_hits"):
        value = getattr(args, name, None)
        if value is not None:
            setattr(b, name, value)


def _write_report(project: pj.Project) -> None:
    from .report import build_report

    text = build_report(project)
    path = project.path.with_name(project.path.name.replace(".json", "") + ".report.txt")
    path.write_text(text, encoding="utf-8")
    print()
    print(text)
    print(f"Rapport écrit : {path}")


def cmd_analyze(args: argparse.Namespace) -> int:
    from .analyze import analyze_project

    project = _open_or_create(args)
    segments = resolve_segments(project)
    print(f"Montage : {len(segments)} passage(s), durée {format_time(timeline_duration(segments))}")
    complete = analyze_project(project, device=args.device)
    _write_report(project)
    return 0 if complete else 130


def cmd_render(args: argparse.Namespace) -> int:
    from .render import RenderError, render_project

    project = pj.load(Path(args.project_file))
    _apply_edit_options(project, args)
    _apply_settings(project, args)
    pj.save(project)
    try:
        render_project(project, Path(args.output), args.quality, args.codec, args.encoder, args.debug)
    except RenderError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .analyze import analyze_project
    from .render import RenderError, render_project

    project = _open_or_create(args)
    if not analyze_project(project, device=args.device):
        return 130
    _write_report(project)
    try:
        render_project(project, Path(args.output), args.quality, args.codec, args.encoder, args.debug)
    except RenderError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    _write_report(pj.load(Path(args.project_file)))
    return 0


def cmd_measure(args: argparse.Namespace) -> int:
    """Compare un autre réglage d'analyse au projet corrigé, ou propose des seuils de flou."""
    from . import tuning
    from .analyze import apply_preset

    project = pj.load(Path(args.project_file))
    facts = tuning.truth(project)
    print(f"Projet corrigé : {len(facts['right'])} flous gardés, {len(facts['wrong'])} retirés par vous, "
          f"{len(facts['added'])} ajoutés par vous, {len(facts['unknown'])} objets vus mais non floutés.")
    if not (args.preset or args.face_model or args.plate_model or args.tile_every or args.detect_every):
        s = tuning.suggest(project)
        if not s["usable"]:
            print(s["reason"])
            return 0
        print("Seuils de flou proposés (rien n'est réanalysé) :", ", ".join(f"{k} = {v}" for k, v in s["settings"].items()))
        print(f"  faux flous que vous aviez retirés et qui ne seraient plus produits : {s['best']['wrong_avoided']} sur {s['corrections']} "
              f"(aujourd'hui : {s['now']['wrong_avoided']})")
        print(f"  flous gardés perdus : {s['best']['lost']} ; nouveaux flous jamais regardés : {s['best']['new']}")
        return 0

    def change(trial: pj.Project) -> None:
        if args.preset:
            apply_preset(trial, args.preset)
        a = trial.analysis_settings
        for name in ("face_model", "plate_model", "tile_every", "detect_every"):
            if getattr(args, name):
                setattr(a, name, getattr(args, name))

    print("Réanalyse avec les réglages demandés (le projet n'est pas modifié)…")
    print(tuning.describe(tuning.measure(project, change, device=args.device)))
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    """Analyse plusieurs vidéos à la suite, chacune dans son propre projet."""
    from . import batch

    def on_item(index: int, total: int, name: str) -> None:
        print(f"\n=== Vidéo {index + 1} sur {total} : {name} ===")

    results = batch.run_batch([Path(p) for p in args.inputs], args.preset, args.profile, args.device, on_item)
    print("\n" + batch.summary(results, len(args.inputs)))
    return 0 if all(r["ok"] for r in results) else 1


def cmd_train(args: argparse.Namespace) -> int:
    """Entraîne un modèle de plaques sur des projets vérifiés et l'installe."""
    from .training import train

    projects = [pj.load(Path(p)) for p in args.projects]
    result = train(projects, args.name, args.epochs, args.base, args.device, force=args.force)
    print(f"\nModèle « {result['model']} » installé : {result['path']}")
    print("Il apparaît dans la liste « Modèle de plaques » des réglages experts.")
    print("Avant de l'adopter, mesurez-le sur une vidéo vérifiée qui n'a PAS servi à l'entraînement :")
    print(f"  uv run moto-blur measure AUTRE-PROJET.motoblur.json --plate-model {result['model']}")
    print("Ne le gardez que s'il ne perd aucun flou et produit moins de faux flous.")
    return 0


def cmd_dataset(args: argparse.Namespace) -> int:
    from .training import export_dataset

    result = export_dataset(pj.load(Path(args.project_file)), Path(args.output) if args.output else None)
    print(f"\n{result['images']} images écrites dans {result['path']} "
          f"({result['labels']} plaques, {result['negatives']} images sans plaque).")
    print("Ces images montrent des plaques non floutées : ne les publiez pas. Suite : docs/ENTRAINEMENT.md")
    return 0


def _parse_box(text: str, video: pj.VideoInfo) -> tuple[int, int, int, int]:
    try:
        x, y, w, h = (float(v) for v in text.split(","))
    except ValueError:
        raise SystemExit(f"Erreur : --box attend X,Y,LARGEUR,HAUTEUR en pixels, reçu {text!r}") from None
    if w <= 0 or h <= 0:
        raise SystemExit("Erreur : --box : la largeur et la hauteur doivent être positives.")
    return (
        int(max(0, x)), int(max(0, y)), int(min(video.width, x + w)), int(min(video.height, y + h))
    )


def cmd_dash(args: argparse.Namespace) -> int:
    from . import dash
    from .analyze import refresh_review
    from .progress import Progress
    from .timeline import sec_to_pts

    project = pj.load(Path(args.project_file))
    clip = project.clip(args.clip) if args.clip else project.clips[0]
    if args.clear:
        n = dash.clear_dash(project, clip, args.name)
        print(f"{n} zone(s) supprimée(s) sur {clip.id}.")
    elif not args.box:
        raise SystemExit("Erreur : indiquez --box X,Y,LARGEUR,HAUTEUR (ou --clear).")
    elif args.fixed:
        box = _parse_box(args.box, clip.video)
        dash.set_fixed_zone(project, clip, box, args.name)
        print(f"Zone fixe « {dash.object_name(args.name)} » enregistrée sur {clip.id} : {box}")
    else:
        box = _parse_box(args.box, clip.video)
        seed = sec_to_pts(args.at or 0.0, clip.video)
        span = dash.dash_span(project, clip)
        if span is None:
            raise SystemExit(f"Erreur : le clip {clip.id} n'a aucun passage gardé.")
        total = round((span[1] - span[0]) * clip.video.tb * clip.video.fps_fraction)
        name = dash.object_name(args.name)
        progress = Progress(f"Suivi de « {name} » {clip.id}", total, float(clip.video.fps_fraction))
        tracks = dash.track_from_click(project, clip, seed, box, args.direction, progress, name, args.scope)
        print(progress.close())
        print(dash.describe(name, tracks, clip))
    refresh_review(project)
    pj.save(project)
    _write_report(project)
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from .server import serve

    target = None
    if args.inputs:
        if len(args.inputs) > 1 or args.project:
            project = _open_or_create(args)
            target = project.path
        else:
            target = Path(args.inputs[0])
    serve(target, port=args.port, open_browser=not args.no_browser, exit_with_parent=args.exit_with_parent)
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    from .video import probe

    for name in args.videos:
        video, audio = probe(Path(name))
        bits = "10 bits" if "10" in video.pix_fmt else "12 bits" if "12" in video.pix_fmt else "8 bits"
        print(name)
        print(f"  vidéo  : {video.codec}, {video.width}x{video.height}, {float(video.fps_fraction):.3f} i/s "
              f"({video.fps}), {video.pix_fmt} ({bits}), {video.bit_rate / 1e6:.1f} Mb/s")
        print(f"  durée  : {format_time(video.duration_s)} ({video.frames} images), base de temps {video.time_base}")
        if video.rotation:
            print(f"  rotation : {video.rotation}°")
        if audio:
            print(f"  audio  : {audio.codec}, {audio.rate} Hz, {audio.layout}, {audio.bit_rate / 1000:.0f} kb/s")
        else:
            print("  audio  : aucune piste")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    import platform

    import av
    import onnxruntime as ort

    from .models import MODELS, models_dir

    print(f"moto-blur {__version__} — Python {platform.python_version()} — {platform.system()} {platform.machine()}")
    print(f"ONNX Runtime {ort.__version__} : {', '.join(p.replace('ExecutionProvider', '') for p in ort.get_available_providers())}")
    names = ["h264_videotoolbox", "hevc_videotoolbox", "h264_amf", "hevc_amf", "h264_nvenc", "hevc_nvenc", "libx264", "libx265"]
    present = []
    for n in names:
        try:
            av.codec.Codec(n, "w")
            present.append(n)
        except Exception:
            pass
    print(f"PyAV {av.__version__}, encodeurs présents : {', '.join(present)}")
    print("  (un encodeur matériel présent n'est utilisable que si la machine a le matériel ;")
    print("   le rendu les essaie réellement et se replie sur le logiciel sinon)")
    print(f"Dossier des modèles : {models_dir()}")
    for m in MODELS.values():
        here = "téléchargé" if (models_dir() / f"{m.name}.onnx").exists() else "non téléchargé"
        print(f"  {m.name:<15} {pj.KIND_LABELS[m.kind]:<7} {m.size_mb:>5.1f} Mo  {here:<15} {m.license}")
    return 0


def _add_edit_options(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("montage")
    g.add_argument("--trim-start", type=parse_time, metavar="DUREE", help="durée à retirer au début (ex. 12.5 ou 0:12.5)")
    g.add_argument("--trim-end", type=parse_time, metavar="DUREE", help="durée à retirer à la fin")
    g.add_argument("--keep", action="append", metavar="DEBUT-FIN",
                   help="passage à garder, répétable (un seul clip) ; ex. --keep 0:10-1:30 --keep 2:00-3:15")


def _add_analysis_options(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("analyse")
    g.add_argument("--device", choices=["auto", "gpu", "cpu"], default="auto", help="accélération (défaut : auto)")
    g.add_argument("--face-model", help="modèle de visages (voir « moto-blur info »)")
    g.add_argument("--plate-model", help="modèle de plaques")
    g.add_argument("--only", choices=list(pj.KINDS), help="ne détecter qu'une catégorie")
    g.add_argument("--conf-new", type=float, help="score minimal pour créer une piste (défaut 0.20)")
    g.add_argument("--conf-keep", type=float, help="score minimal pour prolonger une piste (défaut 0.05)")
    g.add_argument("--tile-size", type=int, help="côté des tuiles en pixels source ; 0 = auto, -1 = sans tuiles")
    g.add_argument("--no-full-pass", action="store_true", help="ne pas ajouter la passe sur l'image entière")
    g.add_argument("--detect-every", type=int, help="détecter une image sur N (défaut 1 ; plus rapide mais plus risqué)")


def _add_render_options(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("rendu")
    g.add_argument("-o", "--output", required=True, help="vidéo de sortie (.mp4, .mov ou .mkv)")
    g.add_argument("--debug", action="store_true", help="dessiner les cadres de détection et les scores au lieu de flouter")
    g.add_argument("--quality", type=int, default=80, help="qualité de 0 à 100 (défaut 80)")
    g.add_argument("--codec", choices=["source", "h264", "hevc"], default="source", help="codec de sortie (défaut : celui de la source)")
    g.add_argument("--encoder", default="auto", help="auto, hardware, software, ou un nom précis (ex. hevc_amf)")
    g.add_argument("--conform", choices=["first", "largest"], help="clips de formats différents : aligner sur le premier clip, ou sur la plus grande résolution et fréquence")
    g.add_argument("--margin", type=float, help="marge autour des zones, en fraction de leur taille (défaut 0.30)")
    g.add_argument("--extend-s", type=float, help="prolongation du flou avant et après l'objet, en secondes (défaut 0.30)")
    g.add_argument("--min-hits", type=int, help="nombre d'images où une détection doit atteindre le score minimal pour être floutée (défaut 3)")
    g.add_argument("--face-min-score", type=float, help="score minimal qu'un visage doit atteindre pour être flouté (défaut 0.40)")
    g.add_argument("--plate-min-score", type=float, help="score minimal qu'une plaque doit atteindre pour être floutée (défaut 0.25)")
    g.add_argument("--style", choices=["pixelate", "solid"], help="type de flou (défaut pixelate)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moto-blur", description="Floutage automatique et montage simple pour vidéos moto.")
    parser.add_argument("--version", action="version", version=f"moto-blur {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="analyser (détection + suivi) et écrire le fichier projet")
    p.add_argument("inputs", nargs="+", metavar="VIDEO|PROJET", help="vidéos dans l'ordre du montage, ou un fichier projet existant")
    p.add_argument("-p", "--project", help="fichier projet (défaut : <première vidéo>.motoblur.json)")
    _add_edit_options(p)
    _add_analysis_options(p)
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("render", help="produire la vidéo finale à partir d'un fichier projet")
    p.add_argument("project_file", metavar="PROJET")
    _add_render_options(p)
    _add_edit_options(p)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("run", help="analyser puis rendre")
    p.add_argument("inputs", nargs="+", metavar="VIDEO|PROJET")
    p.add_argument("-p", "--project")
    _add_edit_options(p)
    _add_analysis_options(p)
    _add_render_options(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("report", help="afficher les passages à vérifier")
    p.add_argument("project_file", metavar="PROJET")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("batch", help="analyser plusieurs vidéos à la suite (file d'attente), chacune dans son propre projet")
    p.add_argument("inputs", nargs="+", metavar="VIDEO|PROJET")
    p.add_argument("--preset", choices=["normal", "difficile", "rapide"], help="préréglage d'analyse pour toutes les vidéos")
    p.add_argument("--profile", action="store_true", help="reprendre le profil « ma moto » (réglages et objets suivis) pour les vidéos neuves")
    p.add_argument("--device", choices=["auto", "gpu", "cpu"], default="auto")
    p.set_defaults(func=cmd_batch)

    p = sub.add_parser("measure", help="mesurer un réglage sur un projet corrigé : seuils de flou proposés, ou comparaison après réanalyse")
    p.add_argument("project_file", metavar="PROJET")
    p.add_argument("--preset", choices=["normal", "difficile", "rapide"], help="préréglage d'analyse à essayer")
    p.add_argument("--face-model", help="modèle de visages à essayer")
    p.add_argument("--plate-model", help="modèle de plaques à essayer (un modèle personnel, par exemple)")
    p.add_argument("--tile-every", type=int, help="tuiles fines une image sur N (la passe d'ensemble reste sur toutes)")
    p.add_argument("--detect-every", type=int, help="détecter une image sur N")
    p.add_argument("--device", choices=["auto", "gpu", "cpu"], default="auto")
    p.set_defaults(func=cmd_measure)

    p = sub.add_parser("train", help="entraîner un modèle de plaques sur vos projets vérifiés et l'installer (outil séparé, téléchargé au premier usage)")
    p.add_argument("projects", nargs="+", metavar="PROJET", help="projets vérifiés (plusieurs sorties différentes de préférence)")
    p.add_argument("--name", default="mes-plaques", help="nom du modèle (défaut : mes-plaques)")
    p.add_argument("--epochs", type=int, default=80, help="nombre de passes sur les images (défaut : 80)")
    p.add_argument("--base", default="yolo11s", choices=["yolo11n", "yolo11s", "yolo11m"], help="modèle de départ (n : rapide, s : équilibré, m : précis)")
    p.add_argument("--device", default="auto", help="auto (puce Apple si présente, sinon processeur), mps, cpu")
    p.add_argument("--force", action="store_true", help="entraîner même avec très peu de plaques (pour essayer la chaîne)")
    p.set_defaults(func=cmd_train)

    p = sub.add_parser("dataset", help="exporter des images d'entraînement (plaques) à partir d'un projet vérifié")
    p.add_argument("project_file", metavar="PROJET")
    p.add_argument("-o", "--output", metavar="DOSSIER", help="dossier de sortie (défaut : <projet>_entrainement)")
    p.set_defaults(func=cmd_dataset)

    p = sub.add_parser("ui", help="ouvrir l'interface de montage et de vérification")
    p.add_argument("inputs", nargs="*", metavar="VIDEO|PROJET", help="vidéos ou fichier projet à ouvrir (facultatif)")
    p.add_argument("-p", "--project", help="fichier projet à créer pour plusieurs vidéos")
    p.add_argument("--port", type=int, default=0, help="port local (défaut : un port libre)")
    p.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur")
    p.add_argument("--exit-with-parent", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_ui)

    p = sub.add_parser("dash", help="flouter le compteur ou un autre objet désigné : zone fixe ou suivi à partir d'une image")
    p.add_argument("project_file", metavar="PROJET")
    p.add_argument("--box", metavar="X,Y,L,H", help="rectangle du compteur en pixels : coin haut-gauche, largeur, hauteur")
    p.add_argument("--at", type=parse_time, metavar="TEMPS", help="instant (temps source) de l'image où le rectangle est donné (défaut : début)")
    p.add_argument("--clip", help="identifiant du clip (défaut : le premier)")
    p.add_argument("--name", metavar="NOM", help="nom de l'objet : compteur (défaut), téléphone, GPS… Chaque nom est suivi séparément")
    p.add_argument("--scope", choices=["auto", "all", "shot"], default="auto",
                   help="all : tout le clip, en retrouvant l'objet après chaque changement de plan ; "
                   "shot : seulement le plan de l'image donnée (correction) ; auto (défaut) : shot si l'objet est déjà suivi")
    p.add_argument("--fixed", action="store_true", help="zone fixe sur tout le clip, sans suivi")
    p.add_argument("--direction", choices=["both", "forward", "backward"], default="both", help="sens du suivi à partir de l'image donnée")
    p.add_argument("--clear", action="store_true", help="supprimer les zones de l'objet nommé (ou de tous les objets sans --name)")
    p.set_defaults(func=cmd_dash)

    p = sub.add_parser("probe", help="afficher les caractéristiques d'une vidéo")
    p.add_argument("videos", nargs="+", metavar="VIDEO")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("info", help="afficher l'accélération et les modèles disponibles")
    p.set_defaults(func=cmd_info)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # consoles Windows : accents et flèches
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrompu.", file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
