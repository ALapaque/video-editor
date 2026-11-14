"""Floutage du compteur et de tout autre objet désigné à la main (téléphone, GPS…).

Chaque objet porte un nom ; plusieurs objets peuvent être suivis dans le même clip, chacun
indépendamment des autres. Deux modes sont implémentés :

1. Zone fixe : un rectangle donné par ses coordonnées, valable pour tout le clip. Convient à
   une caméra fixée à la moto avec un cadrage constant.
2. Suivi à partir d'un clic : on désigne le compteur sur une image, un modèle de suivi le suit
   image par image, vers l'avant et vers l'arrière. Le modèle est VitTrack (OpenCV, Apache-2.0,
   0,7 Mo) : il renvoie à chaque image un rectangle et un score de confiance.

Un modèle de suivi seul ne sait pas qu'il a décroché : après un changement de plan, il « suit »
ce qui ressemble vaguement à l'objet (un rétroviseur, un bout de carénage). Trois garde-fous :

- Changement de plan : une image très différente de la précédente (couleurs et formes) arrête
  le suivi. Le flou ne déborde pas sur le plan suivant.
- Ressemblance : à chaque image, la zone suivie est comparée à l'aspect de l'objet désigné
  (corrélation normalisée, insensible aux variations de luminosité). Si elle ne lui ressemble
  plus, ou si le modèle n'est plus sûr de lui, l'objet est d'abord cherché tout près de sa
  dernière position sûre (objet peu texturé, flou de bougé, glissement vers un voisin) ; à
  défaut, cette position est maintenue au plus une seconde, puis l'objet est déclaré perdu.
- Recherche : objet perdu, on le cherche dans les images suivantes en comparant l'image entière
  à son aspect, à plusieurs tailles. S'il est retrouvé avec une forte ressemblance, le suivi
  reprend ; ce morceau est signalé pour vérification. Les passages où l'objet n'a pas été
  retrouvé sont signalés aussi : le logiciel ne floute rien à l'aveugle.

Le suivi travaille sur une image réduite (640 px de large) : le compteur est un objet grand,
la pleine résolution n'apporterait rien et coûterait du temps.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import av
import cv2
import numpy as np

from . import intervals
from . import project as pj
from .models import ensure_model, get_spec
from .progress import Progress
from .timeline import kept_intervals, resolve_segments
from .video import iter_frames

TRACK_WIDTH = 640
LOST_SCORE = 0.30
# Ressemblance minimale (corrélation normalisée, -1 à 1) entre la zone suivie et l'objet.
SIMILAR_MIN = 0.30
# Ressemblance exigée pour accepter un objet retrouvé par recherche : plus stricte, pour ne
# pas reprendre le suivi sur un autre objet.
REACQUIRE_MIN = 0.62
# Durée maximale pendant laquelle la dernière position sûre est maintenue (objet masqué).
HOLD_MAX_S = 1.0
# Changement de plan : distance entre histogrammes de couleur, ou écart moyen de luminosité.
CUT_HIST = 0.45
CUT_DIFF = 38.0
# Pendant une recherche, une image sur deux est examinée (la recherche coûte cher).
SEARCH_EVERY = 2
SEARCH_SCALES = (0.6, 0.72, 0.85, 1.0, 1.18, 1.4, 1.65)
# Trou de suivi signalé à partir de cette durée.
GAP_MIN_S = 0.15
BACKWARD_CHUNK_S = 3.0
PAD_S = 1.0


def dash_span(project: pj.Project, clip: pj.Clip) -> tuple[int, int] | None:
    """Intervalle de PTS du clip sur lequel le compteur doit être suivi.

    Du début du premier passage gardé à la fin du dernier, avec une marge. Les passages coupés
    situés entre deux passages gardés sont suivis aussi : le suivi a besoin de continuité.
    """
    kept = kept_intervals(resolve_segments(project), clip.id)
    if not kept:
        return None
    pad = round(PAD_S / clip.video.tb)
    return (max(clip.video.start_pts, kept[0][0] - pad), min(clip.video.end_pts, kept[-1][1] + pad))


def object_name(label: str | None) -> str:
    """Nom normalisé d'un objet suivi (« compteur » par défaut)."""
    return (label or "").strip()[:40] or pj.DEFAULT_OBJECT


def dash_tracks(
    project: pj.Project, clip: pj.Clip, source: str | None = None, label: str | None = None
) -> list[pj.Track]:
    """Pistes d'objets désignés à la main dans un clip, filtrées par origine et par nom."""
    state = project.analysis.get(clip.id)
    if not state:
        return []
    return [
        t for t in state.tracks
        if t.kind == "dash"
        and (source is None or t.source == source)
        and (label is None or t.name == object_name(label))
    ]


def object_names(project: pj.Project, clip: pj.Clip, source: str | None = None) -> list[str]:
    return list(dict.fromkeys(t.name for t in dash_tracks(project, clip, source)))


def missing_span(
    project: pj.Project, clip: pj.Clip, label: str | None = None
) -> list[intervals.Interval]:
    """Parties à suivre que le suivi ne couvre pas encore, pour un objet ou pour tous."""
    span = dash_span(project, clip)
    if span is None:
        return []
    names = [object_name(label)] if label is not None else object_names(project, clip, "tracker")
    step = round(clip.video.frame_pts * 1.5) + 1
    missing: list[intervals.Interval] = []
    for name in names:
        tracked = [t for t in dash_tracks(project, clip, "tracker", name) if t.obs]
        if not tracked:
            continue
        covered = [(lo - step, hi + step) for lo, hi in (_attempted(t) for t in tracked)]
        missing += intervals.subtract([span], covered)
    return intervals.normalize(missing)


def _attempted(t: pj.Track) -> tuple[int, int]:
    """Partie du clip que la passe de suivi de cette piste a parcourue."""
    if t.span is not None:
        return int(t.span[0]), int(t.span[1])
    return int(t.obs[0][0]), int(t.obs[-1][0])


def set_fixed_zone(
    project: pj.Project, clip: pj.Clip, box: tuple[int, int, int, int], label: str | None = None
) -> pj.Track:
    """Mode 1 : zone fixe sur tout le clip. Remplace les zones existantes du même objet."""
    name = object_name(label)
    state = project.analysis.setdefault(clip.id, pj.ClipAnalysis())
    state.tracks = [t for t in state.tracks if not (t.kind == "dash" and t.name == name)]
    x1, y1, x2, y2 = box
    v = clip.video
    track = pj.Track(
        project.next_track_id(), "dash",
        [[v.start_pts, x1, y1, x2, y2, 1.0], [v.end_pts, x1, y1, x2, y2, 1.0]],
        source="manual", label=name,
    )
    state.tracks.append(track)
    return track


def clear_dash(project: pj.Project, clip: pj.Clip, label: str | None = None) -> int:
    """Supprime les zones d'un objet (ou de tous les objets si aucun nom n'est donné)."""
    state = project.analysis.get(clip.id)
    if not state:
        return 0
    before = len(state.tracks)
    state.tracks = [
        t for t in state.tracks
        if not (t.kind == "dash" and (label is None or t.name == object_name(label)))
    ]
    return before - len(state.tracks)


def _small(frame: av.VideoFrame, size: tuple[int, int]) -> np.ndarray:
    return frame.reformat(width=size[0], height=size[1], format="bgr24").to_ndarray()


def _new_tracker():
    params = cv2.TrackerVit_Params()
    params.net = str(ensure_model(get_spec("vittrack", "dash")))
    return cv2.TrackerVit_create(params)


def _reverse_frames(
    container, start_pts: int, end_pts: int, size: tuple[int, int], tb
) -> Iterator[tuple[int, np.ndarray]]:
    """Images réduites de [start_pts, end_pts), de la dernière à la première.

    Un fichier vidéo ne se décode que vers l'avant. On lit donc par morceaux de quelques
    secondes, en partant de la fin, et on renverse chaque morceau en mémoire.
    """
    chunk = max(1, round(BACKWARD_CHUNK_S / tb))
    hi = end_pts
    while hi > start_pts:
        lo = max(start_pts, hi - chunk)
        frames = [(f.pts, _small(f, size)) for f in iter_frames(container, lo, hi)]
        yield from reversed(frames)
        hi = lo


class _Look:
    """Aspect d'une image réduite, pour repérer les changements de plan."""

    def __init__(self, image: np.ndarray):
        small = cv2.resize(image, (96, 54), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        self.hist = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
        cv2.normalize(self.hist, self.hist)
        self.gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0).astype(np.float32)

    def is_cut_from(self, previous: _Look) -> bool:
        dist = cv2.compareHist(previous.hist, self.hist, cv2.HISTCMP_BHATTACHARYYA)
        diff = float(np.abs(self.gray - previous.gray).mean())
        return dist > CUT_HIST or diff > CUT_DIFF


def is_cut(previous: np.ndarray, current: np.ndarray) -> bool:
    """Vrai si `current` appartient manifestement à un autre plan que `previous`."""
    return _Look(current).is_cut_from(_Look(previous))


class _Appearance:
    """Aspect de l'objet suivi : l'image désignée et une image récente sûre."""

    SIDE = 48  # taille de comparaison, en pixels (plus grand côté)

    def __init__(self, image: np.ndarray, box: tuple[int, int, int, int]):
        x, y, w, h = box
        self.patch = cv2.cvtColor(image[y : y + h, x : x + w], cv2.COLOR_BGR2GRAY)  # pour la recherche
        k = self.SIDE / max(w, h)
        self.size = (max(8, round(w * k)), max(8, round(h * k)))
        self.seed = self._norm(self.patch)
        self.recent = self.seed

    @classmethod
    def from_patch(cls, patch: np.ndarray) -> _Appearance:
        """Aspect construit à partir d'une image de l'objet seule (profil enregistré)."""
        self = cls.__new__(cls)
        self.patch = patch if patch.ndim == 2 else cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        h, w = self.patch.shape[:2]
        k = cls.SIDE / max(w, h)
        self.size = (max(8, round(w * k)), max(8, round(h * k)))
        self.seed = self._norm(self.patch)
        self.recent = self.seed
        return self

    def _norm(self, patch: np.ndarray) -> np.ndarray:
        return cv2.resize(patch, self.size, interpolation=cv2.INTER_AREA)

    def similarity(self, image: np.ndarray, box: tuple[int, int, int, int]) -> float:
        x, y, w, h = box
        ih, iw = image.shape[:2]
        x0, y0, x1, y1 = max(0, x), max(0, y), min(iw, x + w), min(ih, y + h)
        if x1 - x0 < 4 or y1 - y0 < 4:
            return -1.0
        patch = self._norm(cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY))
        return max(_ncc(patch, self.seed), _ncc(patch, self.recent))

    def remember(self, image: np.ndarray, box: tuple[int, int, int, int]) -> None:
        """Mémorise l'aspect récent (lumière qui change, objet qui tourne lentement)."""
        x, y, w, h = box
        ih, iw = image.shape[:2]
        x0, y0, x1, y1 = max(0, x), max(0, y), min(iw, x + w), min(ih, y + h)
        if x1 - x0 >= 4 and y1 - y0 >= 4:
            self.recent = self._norm(cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY))

    def search(
        self, image: np.ndarray, near: tuple[int, int, int, int] | None = None
    ) -> tuple[float, tuple[int, int, int, int]] | None:
        """Cherche l'objet, à plusieurs tailles. Renvoie (ressemblance, boîte).

        Sans `near`, dans toute l'image ; avec `near` (dernière position sûre), seulement autour.
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        ph, pw = self.patch.shape[:2]
        if near is not None:
            x, y, w, h = near
            return self._refine(image, gray, max(w / pw, h / ph), x, y)
        half = cv2.resize(gray, (gray.shape[1] // 2, gray.shape[0] // 2), interpolation=cv2.INTER_AREA)
        # 1) Recherche grossière, sur l'image réduite de moitié, à quelques tailles.
        best: tuple[float, float, int, int] | None = None  # (ressemblance, taille, x, y)
        for scale in SEARCH_SCALES:
            tw, th = round(pw * scale / 2), round(ph * scale / 2)
            if tw < 6 or th < 6 or tw >= half.shape[1] or th >= half.shape[0]:
                continue
            template = cv2.resize(self.patch, (tw, th), interpolation=cv2.INTER_AREA)
            result = cv2.matchTemplate(half, template, cv2.TM_CCOEFF_NORMED)
            _, value, _, (mx, my) = cv2.minMaxLoc(result)
            if best is None or value > best[0]:
                best = (float(value), scale, mx * 2, my * 2)
        if best is None:
            return None
        _, scale0, x0, y0 = best
        return self._refine(image, gray, scale0, x0, y0)

    def _refine(
        self, image: np.ndarray, gray: np.ndarray, scale0: float, x0: int, y0: int
    ) -> tuple[float, tuple[int, int, int, int]] | None:
        """Affinage autour d'une position, à pleine taille et à tailles proches."""
        ph, pw = self.patch.shape[:2]
        margin = max(12, round(0.25 * max(pw, ph) * scale0))
        found: tuple[float, tuple[int, int, int, int]] | None = None
        for k in (0.88, 0.92, 0.96, 1.0, 1.04, 1.08, 1.12):
            tw, th = round(pw * scale0 * k), round(ph * scale0 * k)
            rx0, ry0 = max(0, x0 - margin), max(0, y0 - margin)
            rx1 = min(gray.shape[1], x0 + round(pw * scale0) + margin)
            ry1 = min(gray.shape[0], y0 + round(ph * scale0) + margin)
            if tw < 8 or th < 8 or rx1 - rx0 <= tw or ry1 - ry0 <= th:
                continue
            template = cv2.resize(self.patch, (tw, th), interpolation=cv2.INTER_AREA)
            result = cv2.matchTemplate(gray[ry0:ry1, rx0:rx1], template, cv2.TM_CCOEFF_NORMED)
            _, value, _, (mx, my) = cv2.minMaxLoc(result)
            if found is None or value > found[0]:
                found = (float(value), (rx0 + mx, ry0 + my, tw, th))
        if found is None:
            return None
        # 3) Confirmation : la ressemblance est mesurée comme pendant le suivi.
        return self.similarity(image, found[1]), found[1]


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    if a.std() < 1e-3 or b.std() < 1e-3:
        return 0.0
    return float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0, 0])


@dataclass
class _Piece:
    """Morceau de suivi continu, dans l'ordre de traitement (vers l'avant ou l'arrière)."""

    obs: list[list[float]] = field(default_factory=list)
    reacquired_at: int | None = None  # PTS où l'objet a été retrouvé par recherche
    begins_at_cut: bool = False  # le morceau commence juste après un changement de plan
    ends_at_cut: bool = False  # le morceau s'arrête sur un changement de plan
    similarity: float = 1.0  # ressemblance au moment où l'objet a été retrouvé


def _follow(
    frames,
    seed_image: np.ndarray,
    init: tuple[int, int, int, int],
    scale: float,
    clip: pj.Clip,
    progress: Progress | None,
    search: bool,
) -> tuple[list[_Piece], int | None]:
    """Suit l'objet sur une suite d'images réduites, dans l'ordre donné.

    Renvoie les morceaux de suivi et le PTS de la dernière image examinée. Avec `search`
    faux (correction d'un plan), le suivi s'arrête au premier changement de plan ou à la
    première perte, sans chercher l'objet ailleurs.
    """
    w, h = clip.video.width, clip.video.height
    hold_max = max(1, round(HOLD_MAX_S * float(clip.video.fps_fraction)))
    appearance = _Appearance(seed_image, init)
    tracker = _new_tracker()
    tracker.init(seed_image, init)
    previous = _Look(seed_image)
    pieces: list[_Piece] = [_Piece()]
    piece: _Piece | None = pieces[0]
    last_good: tuple[int, int, int, int] | None = init
    doubtful = 0  # images d'affilée où le suivi n'est pas sûr
    since_good = 0
    lost_frames = 0
    last_pts = None

    def to_source(b: tuple[int, int, int, int]) -> list[float]:
        x, y, bw, bh = b
        box = [x / scale, y / scale, (x + bw) / scale, (y + bh) / scale]
        return [min(max(box[0], 0), w), min(max(box[1], 0), h), min(max(box[2], 0), w), min(max(box[3], 0), h)]

    for pts, image in frames:
        if progress:
            progress.step()
        look = _Look(image)
        cut = look.is_cut_from(previous)
        previous = look
        if piece is not None and cut:
            piece.ends_at_cut = True
            piece = None
            if not search:
                break  # l'image de coupe appartient déjà au plan suivant
        last_pts = pts
        if piece is None:
            # Objet perdu : le chercher (tout de suite après une coupe, puis une image sur deux).
            lost_frames += 1
            if not search or (not cut and lost_frames % SEARCH_EVERY):
                continue
            found = appearance.search(image)
            if found is None or found[0] < REACQUIRE_MIN:
                continue
            similarity, box = found
            tracker = _new_tracker()
            tracker.init(image, box)
            piece = _Piece(reacquired_at=int(pts), begins_at_cut=cut, similarity=similarity)
            pieces.append(piece)
            last_good, doubtful, since_good, lost_frames = box, 0, 0, 0
            piece.obs.append([int(pts), *to_source(box), round(similarity, 3)])
            continue
        _, (x, y, bw, bh) = tracker.update(image)
        box = (int(x), int(y), int(bw), int(bh))
        score = float(tracker.getTrackingScore())
        similarity = appearance.similarity(image, box) if bw >= 2 and bh >= 2 else -1.0
        if score >= LOST_SCORE and similarity >= SIMILAR_MIN:
            last_good, doubtful = box, 0
            since_good += 1
            if similarity >= 0.5 and since_good % hold_max == 0:
                appearance.remember(image, box)
            piece.obs.append([int(pts), *to_source(box), round(min(score, 1.0), 3)])
            continue
        # Le modèle de suivi hésite ou a glissé : l'objet est-il toujours là, tout près ?
        # (objet peu texturé, flou de bougé, ou glissement vers un objet voisin)
        local = appearance.search(image, near=last_good) if last_good is not None else None
        if local is not None and local[0] >= REACQUIRE_MIN:
            similarity, box = local
            tracker = _new_tracker()
            tracker.init(image, box)
            last_good, doubtful = box, 0
            since_good += 1
            piece.obs.append([int(pts), *to_source(box), round(similarity, 3)])
            continue
        doubtful += 1
        if doubtful > hold_max:
            # Perdu : on arrête de flouter à l'aveugle. Les images déjà maintenues sont gardées :
            # dans le même plan, la dernière position sûre reste la meilleure estimation.
            piece = None
            lost_frames = 0
            if not search:
                break
            continue
        # Incertain : dernière position sûre maintenue, marquée par un score faible.
        piece.obs.append([int(pts), *to_source(last_good), round(min(score, max(similarity, 0.0), 0.29), 3)])
    return [p for p in pieces if p.obs], last_pts


def track_from_click(
    project: pj.Project,
    clip: pj.Clip,
    seed_pts: int,
    box: tuple[float, float, float, float],
    direction: str = "both",
    progress: Progress | None = None,
    label: str | None = None,
    scope: str = "auto",
) -> list[pj.Track]:
    """Mode 2 : suit l'objet désigné par `box` (x1, y1, x2, y2) sur l'image `seed_pts`.

    `direction` : "both", "forward" (de ce point vers la fin) ou "backward".
    `scope` : "all" suit l'objet sur tout le clip, en le recherchant après chaque changement de
    plan ; "shot" ne suit que le plan de l'image désignée (correction d'un endroit précis) ;
    "auto" choisit "shot" si cet objet est déjà suivi dans le clip, "all" sinon.
    Les observations du MÊME objet sur la partie parcourue sont remplacées ; les autres objets
    ne sont pas touchés. Renvoie les morceaux de suivi créés.
    """
    name = object_name(label)
    span = dash_span(project, clip)
    if span is None:
        raise ValueError(f"le clip {clip.id} n'a aucun passage gardé dans le montage.")
    if scope == "auto":
        scope = "shot" if dash_tracks(project, clip, "tracker", name) else "all"
    search = scope == "all"
    seed_pts = min(max(int(seed_pts), span[0]), span[1] - 1)
    v = clip.video
    scale = TRACK_WIDTH / v.width
    size = (TRACK_WIDTH, max(2, round(v.height * scale / 2) * 2))
    init = (
        round(box[0] * scale), round(box[1] * scale),
        max(2, round((box[2] - box[0]) * scale)), max(2, round((box[3] - box[1]) * scale)),
    )
    forward: list[_Piece] = []
    backward: list[_Piece] = []
    with av.open(str(project.clip_path(clip))) as container:
        seed_frame = next(iter_frames(container, seed_pts, v.end_pts), None)
        if seed_frame is None:
            raise ValueError("image de départ introuvable.")
        seed_pts = seed_frame.pts
        seed_image = _small(seed_frame, size)
        lo = hi = seed_pts
        if direction in ("both", "forward"):
            step = round(v.frame_pts / 2) + 1
            frames = ((f.pts, _small(f, size)) for f in iter_frames(container, seed_pts + step, span[1]))
            forward, end = _follow(frames, seed_image, init, scale, clip, progress, search)
            hi = span[1] - 1 if search else (end if end is not None else seed_pts)
        if direction in ("both", "backward"):
            frames = _reverse_frames(container, span[0], seed_pts, size, v.tb)
            backward, end = _follow(frames, seed_image, init, scale, clip, progress, search)
            lo = span[0] if search else (end if end is not None else seed_pts)

    # Le morceau qui contient l'image désignée réunit ses deux moitiés (arrière et avant).
    seed_obs = [seed_pts, *[float(b) for b in box], 1.0]
    first_fwd = forward.pop(0) if forward and forward[0].reacquired_at is None else _Piece()
    first_bwd = backward.pop(0) if backward and backward[0].reacquired_at is None else _Piece()
    seed_piece = _Piece(
        obs=list(reversed(first_bwd.obs)) + [seed_obs] + first_fwd.obs,
        begins_at_cut=first_bwd.ends_at_cut,
        ends_at_cut=first_fwd.ends_at_cut,
    )
    # Morceaux vers l'arrière : dans l'ordre du temps, leurs extrémités sont inversées.
    pieces = [seed_piece] + forward + [
        _Piece(list(reversed(p.obs)), p.reacquired_at, p.ends_at_cut, p.begins_at_cut, p.similarity)
        for p in backward
    ]

    state = project.analysis.setdefault(clip.id, pj.ClipAnalysis())
    kept_tracks = []
    for t in state.tracks:
        same = t.kind == "dash" and t.name == name
        if same and t.source == "tracker":
            t.obs = [o for o in t.obs if o[0] < lo or o[0] > hi]
            if not t.obs:
                continue
        elif same and t.source == "manual" and len(t.obs) == 2 and t.obs[0][1:5] == t.obs[1][1:5]:
            continue  # une zone fixe est remplacée par le suivi
        kept_tracks.append(t)
    state.tracks = kept_tracks
    created = []
    for p in sorted(pieces, key=lambda p: p.obs[0][0]):
        track = pj.Track(
            project.next_track_id(), "dash", p.obs, source="tracker", label=name,
            reacquired_at=p.reacquired_at, span=[int(lo), int(hi)],
            # Pas de flou prolongé de l'autre côté d'un changement de plan.
            extend_before_s=0.0 if p.begins_at_cut else None,
            extend_after_s=0.0 if p.ends_at_cut else None,
        )
        state.tracks.append(track)
        created.append(track)
    return created


def follow_region(
    project: pj.Project,
    clip: pj.Clip,
    seed_pts: int,
    box: tuple[float, float, float, float],
    start_pts: int,
    end_pts: int,
    progress: Progress | None = None,
) -> pj.Track:
    """Flou ajouté à la main qui SUIT ce qui a été entouré (une personne, une plaque).

    Le suivi part de l'image désignée, vers l'avant et vers l'arrière, dans [start_pts, end_pts].
    Il s'arrête au premier changement de plan ou dès que l'objet est perdu : un flou manuel ne
    cherche jamais l'objet ailleurs. La piste créée est une zone manuelle suivie (catégorie
    "manual", origine "tracker") : elle se retire et se prolonge comme les autres.
    """
    v = clip.video
    start_pts, end_pts = max(int(start_pts), v.start_pts), min(int(end_pts), v.end_pts)
    seed_pts = min(max(int(seed_pts), start_pts), max(start_pts, end_pts - 1))
    scale = TRACK_WIDTH / v.width
    size = (TRACK_WIDTH, max(2, round(v.height * scale / 2) * 2))
    init = (
        round(box[0] * scale), round(box[1] * scale),
        max(2, round((box[2] - box[0]) * scale)), max(2, round((box[3] - box[1]) * scale)),
    )
    with av.open(str(project.clip_path(clip))) as container:
        seed_frame = next(iter_frames(container, seed_pts, v.end_pts), None)
        if seed_frame is None:
            raise ValueError("image de départ introuvable.")
        seed_pts = seed_frame.pts
        seed_image = _small(seed_frame, size)
        step = round(v.frame_pts / 2) + 1
        frames = ((f.pts, _small(f, size)) for f in iter_frames(container, seed_pts + step, end_pts))
        forward, _ = _follow(frames, seed_image, init, scale, clip, progress, search=False)
        frames = _reverse_frames(container, start_pts, seed_pts, size, v.tb)
        backward, _ = _follow(frames, seed_image, init, scale, clip, progress, search=False)
    obs = [[seed_pts, *[float(b) for b in box], 1.0]]
    if backward:
        obs = list(reversed(backward[0].obs)) + obs
    if forward:
        obs = obs + forward[0].obs
    state = project.analysis.setdefault(clip.id, pj.ClipAnalysis())
    track = pj.Track(
        project.next_track_id(), "manual", obs, source="tracker",
        extend_before_s=0.0 if backward and backward[0].ends_at_cut else None,
        extend_after_s=0.0 if forward and forward[0].ends_at_cut else None,
    )
    state.tracks.append(track)
    return track


def extend_tracking(project: pj.Project, clip: pj.Clip, progress: Progress | None = None) -> bool:
    """Prolonge le suivi de chaque objet sur les passages gardés ajoutés depuis. Vrai si prolongé."""
    done = False
    for name in object_names(project, clip, "tracker"):
        for lo, hi in missing_span(project, clip, name):
            tracked = [t for t in dash_tracks(project, clip, "tracker", name) if t.obs]
            first = min((t.obs[0] for t in tracked), key=lambda o: o[0])
            last = max((t.obs[-1] for t in tracked), key=lambda o: o[0])
            if lo >= max(_attempted(t)[1] for t in tracked) - clip.video.frame_pts * 2:
                track_from_click(project, clip, int(last[0]), tuple(last[1:5]), "forward", progress, name, "all")
            elif hi <= min(_attempted(t)[0] for t in tracked) + clip.video.frame_pts * 2:
                track_from_click(project, clip, int(first[0]), tuple(first[1:5]), "backward", progress, name, "all")
            else:
                continue
            done = True
    return done


def build_dash_review(clip: pj.Clip, tracks: list[pj.Track]) -> list[dict]:
    """Passages à faire vérifier pour les objets suivis.

    - suivi incertain : dernière position maintenue faute de mieux ;
    - objet retrouvé automatiquement : confirmer qu'il s'agit bien de lui ;
    - objet non suivi : parcouru mais ni suivi ni retrouvé (rien n'y est flouté).
    """
    out = []

    def item(start: int, end: int, ids: list[int], reason: str) -> dict:
        # « risk » : ici, quelque chose peut rester visible (objet perdu, ou flou posé sur un
        # autre objet). C'est une alerte à regarder, pas un simple doute.
        return {"clip": clip.id, "start_pts": int(start), "end_pts": int(end), "kinds": ["dash"],
                "reasons": [reason], "tracks": ids, "risk": True}

    followed = [t for t in tracks if t.kind == "dash" and t.source == "tracker" and t.obs and not t.disabled]
    two_s = 2 / clip.video.tb
    for t in followed:
        at = t.reacquired_at
        if at is not None:
            # Deux secondes autour du moment où l'objet a été retrouvé, dans ce morceau.
            found = next((o for o in t.obs if int(o[0]) == at), t.obs[0])
            start = max(int(t.obs[0][0]), int(at - two_s)) if at == int(t.obs[-1][0]) else at
            end = at if at == int(t.obs[-1][0]) else min(int(t.obs[-1][0]), int(at + two_s))
            out.append(item(start, end, [t.id],
                            f"objet « {t.name} » retrouvé automatiquement (ressemblance {found[5]:.2f}) : "
                            "vérifiez que le flou est bien sur lui"))
        run: list[list[float]] = []
        for o in [*t.obs, None]:
            if o is not None and o[5] < LOST_SCORE and int(o[0]) != at:
                run.append(o)
                continue
            if run:
                worst = min(r[5] for r in run)
                out.append(item(run[0][0], run[-1][0], [t.id],
                                f"suivi de l'objet « {t.name} » incertain (score {worst:.2f}) : "
                                "dernière position maintenue"))
                run = []
    step = round(clip.video.frame_pts * 1.5) + 1
    gap_min = GAP_MIN_S / clip.video.tb
    for name in dict.fromkeys(t.name for t in followed):
        mine = [t for t in followed if t.name == name]
        attempted = intervals.normalize([_attempted(t) for t in mine])
        covered = [(int(t.obs[0][0]) - step, int(t.obs[-1][0]) + step) for t in mine]
        for lo, hi in intervals.subtract(attempted, covered):
            if hi - lo >= gap_min:
                out.append(item(lo, hi, [],
                                f"objet « {name} » non suivi : ni retrouvé ni flouté ici "
                                "(changement de plan, objet caché ou sorti de l'image)"))
    return sorted(out, key=lambda r: r["start_pts"])


def describe(name: str, tracks: list[pj.Track], clip: pj.Clip) -> str:
    """Résumé en clair d'une passe de suivi."""
    frames = sum(len(t.obs) for t in tracks)
    doubtful = sum(1 for t in tracks for o in t.obs if o[5] < LOST_SCORE and int(o[0]) != t.reacquired_at)
    found = sum(1 for t in tracks if t.reacquired_at is not None)
    parts = [f"« {name} » suivi sur {frames} images"]
    if found:
        parts.append(f"retrouvé automatiquement {found} fois après un changement de plan ou une perte")
    if tracks and tracks[0].span:
        lo, hi = tracks[0].span
        covered = intervals.total(intervals.normalize(
            [(int(t.obs[0][0]), int(t.obs[-1][0]) + round(clip.video.frame_pts)) for t in tracks]
        ))
        lost_s = max(0.0, float((hi - lo - covered) * clip.video.tb))
        if lost_s >= GAP_MIN_S:
            parts.append(f"non suivi pendant {lost_s:.1f} s")
    if doubtful:
        parts.append(f"incertain sur {doubtful} images")
    text = ", ".join(parts) + "."
    if found or doubtful:
        text += " Ces passages sont à vérifier."
    return text
