"""Fichier projet : clips source, instructions de montage, résultat d'analyse.

Tout tient dans un seul fichier JSON. Les fichiers vidéo d'origine ne sont
jamais modifiés : le montage n'est qu'une liste d'instructions.

Unités :
- Les instructions de montage (`timeline`, `mutes`) sont en secondes, mesurées
  depuis la première image du clip source. Elles sont lisibles et modifiables
  à la main ; elles sont converties en PTS exacts au moment du calcul.
- L'analyse (`analysis`) est en PTS : l'horodatage entier de chaque image dans
  la base de temps du flux vidéo source. C'est exact, indépendant du montage,
  et ça fonctionne aussi pour les vidéos à fréquence d'images variable.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field, fields
from fractions import Fraction
from pathlib import Path

PROJECT_VERSION = 1
KINDS = ("face", "plate")  # catégories détectées automatiquement
DEFAULT_OBJECT = "compteur"  # nom d'un objet suivi quand aucun n'est donné
KIND_LABELS = {"face": "visage", "plate": "plaque", "dash": "compteur", "manual": "zone manuelle"}


@dataclass
class VideoInfo:
    codec: str
    width: int
    height: int
    pix_fmt: str
    fps: str  # fraction "60000/1001"
    time_base: str  # fraction "1/60000"
    start_pts: int
    end_pts: int  # PTS de fin exclusif (dernière image + une durée d'image)
    frames: int
    bit_rate: int
    rotation: int = 0

    @property
    def fps_fraction(self) -> Fraction:
        return Fraction(self.fps)

    @property
    def tb(self) -> Fraction:
        return Fraction(self.time_base)

    @property
    def frame_pts(self) -> Fraction:
        """Durée nominale d'une image, en unités de PTS (peut ne pas être entière)."""
        return 1 / (self.fps_fraction * self.tb)

    @property
    def duration_s(self) -> float:
        return float((self.end_pts - self.start_pts) * self.tb)


@dataclass
class AudioInfo:
    codec: str
    rate: int
    layout: str
    channels: int
    bit_rate: int


@dataclass
class Clip:
    id: str
    path: str  # relatif au fichier projet si possible
    fingerprint: str
    video: VideoInfo
    audio: AudioInfo | None = None


@dataclass
class Segment:
    """Passage gardé : [start, end) en secondes depuis la première image du clip."""

    clip: str
    start: float
    end: float


@dataclass
class Mute:
    """Passage muet, en temps source du clip (secondes)."""

    clip: str
    start: float
    end: float


@dataclass
class AnalysisSettings:
    face_model: str = "yolo11s-face"
    plate_model: str = "yolo-v9-s-608"
    kinds: list[str] = field(default_factory=lambda: list(KINDS))
    # Seuil pour CRÉER une piste, et seuil (très bas) pour PROLONGER une piste existante.
    conf_new: float = 0.20
    conf_keep: float = 0.05
    # Côté d'une tuile en pixels source. 0 = automatique, -1 = pas de tuiles.
    tile_size: int = 0
    tile_overlap: float = 0.2
    full_pass: bool = True
    # 1 = détection sur chaque image (recommandé). N = une image sur N, le reste est interpolé.
    detect_every: int = 1
    # Durée maximale pendant laquelle une piste perdue peut être retrouvée (trou comblé).
    max_gap_s: float = 1.0

    def signature(self) -> str:
        blob = json.dumps(asdict(self), sort_keys=True).encode()
        return hashlib.sha1(blob).hexdigest()[:12]


@dataclass
class BlurSettings:
    # Marge ajoutée autour de chaque boîte, en fraction de sa taille, de chaque côté.
    margin: float = 0.20
    min_margin_px: int = 6
    # Prolongation du flou avant l'apparition et après la disparition (secondes).
    extend_s: float = 0.30
    # Pendant la prolongation, la zone grossit de ce facteur par seconde (incertitude).
    extend_growth: float = 1.5
    # Union avec les zones des N images voisines (absorbe vibrations et mouvements rapides).
    temporal_pad_frames: int = 1
    # "pixelate" (mosaïque très grossière lissée) ou "solid" (aplat de couleur moyenne).
    style: str = "pixelate"
    # Nombre maximal de cellules de mosaïque sur le plus grand côté de la zone.
    cells: int = 5
    # Forme du flou par catégorie : "ellipse", "rounded" (rectangle arrondi) ou "rect".
    face_shape: str = "ellipse"
    plate_shape: str = "rounded"
    dash_shape: str = "rounded"
    # Marge du compteur suivi : plus faible, c'est un grand objet dont la boîte est fiable.
    dash_margin: float = 0.08
    # Largeur du bord adouci des ellipses, en fraction de leur rayon.
    feather: float = 0.12
    # Score minimal qu'une piste détectée doit atteindre au moins une fois pour être floutée.
    # Les fausses détections restent presque toujours à faible score ; un vrai objet finit par
    # être vu nettement. Plus haut : moins de faux flous, mais plus de risque d'en manquer un vrai.
    # Fusionner les flous qui se recouvrent (un même objet pris pour un visage ET une plaque,
    # ou une détection à l'intérieur d'un objet suivi) : un seul flou au lieu de deux ou trois.
    merge_overlaps: bool = True
    # Nombre d'images où une détection doit atteindre le score minimal pour être floutée. Un vrai
    # visage ou une vraie plaque reste plusieurs images à l'écran ; une fausse détection
    # (feuillage, reflet, carénage) clignote. Exception : une détection très nette (score
    # minimal + 0,25) suffit seule.
    min_hits: int = 3
    face_min_score: float = 0.40
    plate_min_score: float = 0.25

    def min_score_for(self, kind: str) -> float:
        return {"face": self.face_min_score, "plate": self.plate_min_score}.get(kind, 0.0)

    def shape_for(self, kind: str) -> str:
        return {"face": self.face_shape, "plate": self.plate_shape, "dash": self.dash_shape}.get(
            kind, "rect"
        )


@dataclass
class Track:
    id: int
    kind: str  # "face", "plate", "dash" (compteur) ou "manual"
    # Observations brutes : [pts, x1, y1, x2, y2, score]. Pour une zone manuelle, ce sont les
    # images clés posées à la main ; la zone est interpolée entre elles.
    obs: list[list[float]]
    # "auto" : détection ; "tracker" : suivi à partir d'un clic ; "manual" : tracée à la main.
    source: str = "auto"
    # Corrections faites à la vérification : piste ignorée (faux positif), flou prolongé.
    disabled: bool = False
    # Floutée même si son score est sous le minimum (décision prise à la vérification).
    forced: bool = False
    extend_before_s: float | None = None
    extend_after_s: float | None = None
    # Nom de l'objet suivi ou de la zone fixe (catégorie "dash") : « compteur », « téléphone »…
    # Les pistes de même nom dans un clip forment un seul objet. Vide = « compteur ».
    label: str = ""
    # Suivi d'objet : PTS de l'image où l'objet a été retrouvé par recherche automatique (après un
    # changement de plan ou une perte), à faire confirmer à la vérification. None sinon.
    reacquired_at: int | None = None
    # Suivi d'objet : intervalle de PTS [début, fin] que la passe de suivi a parcouru, objet
    # trouvé ou non. Sert à distinguer « pas encore suivi » de « suivi mais objet perdu ».
    span: list[int] | None = None

    @property
    def name(self) -> str:
        """Nom à afficher : celui de l'objet suivi, sinon celui de la catégorie."""
        if self.kind == "dash":
            return self.label or DEFAULT_OBJECT
        return KIND_LABELS.get(self.kind, self.kind)


@dataclass
class ClipAnalysis:
    signature: str = ""
    analyzed: list[list[int]] = field(default_factory=list)  # intervalles de PTS
    tracks: list[Track] = field(default_factory=list)


@dataclass
class Project:
    clips: list[Clip] = field(default_factory=list)
    timeline: list[Segment] = field(default_factory=list)
    mutes: list[Mute] = field(default_factory=list)
    analysis_settings: AnalysisSettings = field(default_factory=AnalysisSettings)
    blur_settings: BlurSettings = field(default_factory=BlurSettings)
    analysis: dict[str, ClipAnalysis] = field(default_factory=dict)
    review: list[dict] = field(default_factory=list)
    # Vérification guidée : clés (voir `review_key`) des passages douteux que l'utilisateur a
    # validés, et signalements posés pendant la relecture complète. Un signalement est
    # {"id": entier, "clip": identifiant, "time": secondes depuis la première image du clip}.
    review_done: list[str] = field(default_factory=list)
    marks: list[dict] = field(default_factory=list)
    # Assemblage de clips de formats différents : "" (pas encore choisi, le rendu refuse),
    # "first" (tout aligner sur le premier clip) ou "largest" (plus grande résolution et
    # plus haute fréquence d'images).
    conform: str = ""
    path: Path | None = None

    def clip(self, clip_id: str) -> Clip:
        for c in self.clips:
            if c.id == clip_id:
                return c
        raise KeyError(f"clip inconnu dans le projet : {clip_id}")

    def clip_path(self, clip: Clip) -> Path:
        p = Path(clip.path)
        if not p.is_absolute() and self.path is not None:
            p = self.path.parent / p
        return p

    def next_track_id(self) -> int:
        ids = [t.id for a in self.analysis.values() for t in a.tracks]
        return max(ids, default=0) + 1


def review_key(item: dict) -> str:
    """Clé d'un passage à vérifier. Elle change dès que le passage change : il est alors redemandé."""
    return f"{item['clip']}:{item['start_pts']}:{item['end_pts']}"


def prune_review_state(project: Project) -> None:
    """Oublie les validations de passages disparus et les signalements de clips retirés."""
    keys = {review_key(r) for r in project.review}
    project.review_done = [k for k in dict.fromkeys(project.review_done) if k in keys]
    clips = {c.id for c in project.clips}
    project.marks = [m for m in project.marks if m.get("clip") in clips]


def fingerprint(path: Path) -> str:
    """Empreinte rapide : taille + début et fin du fichier (pas de lecture complète)."""
    size = path.stat().st_size
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
        if size > (2 << 20):
            f.seek(-(1 << 20), os.SEEK_END)
            h.update(f.read(1 << 20))
    return h.hexdigest()[:16]


def _relative_to_project(video: Path, project_path: Path) -> str:
    try:
        return os.path.relpath(video.resolve(), project_path.resolve().parent).replace("\\", "/")
    except ValueError:  # autre lecteur sous Windows
        return str(video.resolve())


def add_clip(project: Project, video_path: Path, video: VideoInfo, audio: AudioInfo | None) -> Clip:
    n = len(project.clips) + 1
    while any(c.id == f"c{n}" for c in project.clips):
        n += 1
    rel = _relative_to_project(video_path, project.path) if project.path else str(video_path)
    clip = Clip(id=f"c{n}", path=rel, fingerprint=fingerprint(video_path), video=video, audio=audio)
    project.clips.append(clip)
    return clip


# --- Lecture / écriture ---------------------------------------------------


def _track_to_json(t: Track) -> str:
    obs = [[int(o[0]), int(o[1]), int(o[2]), int(o[3]), int(o[4]), round(float(o[5]), 3)] for o in t.obs]
    doc: dict = {"id": t.id, "kind": t.kind}
    if t.source != "auto":
        doc["source"] = t.source
    if t.disabled:
        doc["disabled"] = True
    if t.forced:
        doc["forced"] = True
    if t.extend_before_s is not None:
        doc["extend_before_s"] = t.extend_before_s
    if t.extend_after_s is not None:
        doc["extend_after_s"] = t.extend_after_s
    if t.label:
        doc["label"] = t.label
    if t.reacquired_at is not None:
        doc["reacquired_at"] = int(t.reacquired_at)
    if t.span is not None:
        doc["span"] = [int(t.span[0]), int(t.span[1])]
    doc["obs"] = obs
    return json.dumps(doc, separators=(",", ":"))


def dumps(project: Project) -> str:
    """Sérialise le projet : montage lisible (indenté), analyse compacte (une piste par ligne)."""
    head = {
        "version": PROJECT_VERSION,
        "clips": [asdict(c) for c in project.clips],
        "timeline": [asdict(s) for s in project.timeline],
        "mutes": [asdict(m) for m in project.mutes],
        "settings": {
            "analysis": asdict(project.analysis_settings),
            "blur": asdict(project.blur_settings),
        },
        "conform": project.conform,
        "review_done": project.review_done,
        "marks": project.marks,
        "review": project.review,
    }
    text = json.dumps(head, indent=2, ensure_ascii=False)
    parts = []
    for clip_id, a in project.analysis.items():
        tracks = ",\n".join("        " + _track_to_json(t) for t in a.tracks)
        parts.append(
            f"    {json.dumps(clip_id)}: {{\n"
            f'      "signature": {json.dumps(a.signature)},\n'
            f'      "analyzed": {json.dumps(a.analyzed)},\n'
            f'      "tracks": [\n{tracks}\n      ]\n'
            "    }"
        )
    analysis = '  "analysis": {\n' + ",\n".join(parts) + "\n  }" if parts else '  "analysis": {}'
    return text[: text.rindex("}")].rstrip() + ",\n" + analysis + "\n}\n"


def save(project: Project, path: Path | None = None) -> None:
    path = Path(path or project.path)
    project.path = path
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(dumps(project), encoding="utf-8")
    os.replace(tmp, path)


def _known(cls, values: dict):
    """Construit un bloc de réglages en ignorant les clés inconnues (fichier plus récent)."""
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in values.items() if k in names})


def load(path: Path) -> Project:
    path = Path(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("version") != PROJECT_VERSION:
        raise ValueError(f"version de fichier projet non prise en charge : {doc.get('version')}")
    clips = []
    for c in doc["clips"]:
        audio = AudioInfo(**c["audio"]) if c.get("audio") else None
        clips.append(Clip(c["id"], c["path"], c["fingerprint"], VideoInfo(**c["video"]), audio))
    settings = doc.get("settings", {})
    analysis = {}
    for clip_id, a in doc.get("analysis", {}).items():
        tracks = [
            Track(
                t["id"], t["kind"], t["obs"], t.get("source", "auto"), t.get("disabled", False),
                t.get("forced", False), t.get("extend_before_s"), t.get("extend_after_s"),
                t.get("label", ""), t.get("reacquired_at"), t.get("span"),
            )
            for t in a.get("tracks", [])
        ]
        analysis[clip_id] = ClipAnalysis(a.get("signature", ""), a.get("analyzed", []), tracks)
    return Project(
        clips=clips,
        timeline=[Segment(**s) for s in doc.get("timeline", [])],
        mutes=[Mute(**m) for m in doc.get("mutes", [])],
        analysis_settings=_known(AnalysisSettings, settings.get("analysis", {})),
        blur_settings=_known(BlurSettings, settings.get("blur", {})),
        analysis=analysis,
        review=doc.get("review", []),
        review_done=[str(k) for k in doc.get("review_done", [])],
        marks=[
            {"id": int(m["id"]), "clip": str(m["clip"]), "time": float(m["time"])}
            for m in doc.get("marks", [])
        ],
        conform=doc.get("conform", ""),
        path=path,
    )
