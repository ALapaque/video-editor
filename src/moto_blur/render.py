"""Passe 2 — rendu : coupes et flous appliqués ensemble, en un seul encodage.

Le rendu ne refait aucune analyse : il lit le fichier projet, décode les
passages gardés dans l'ordre du montage, floute les zones calculées à partir
des pistes, et encode une seule fois.

Audio :
- Montage sans aucune coupe (un seul clip gardé en entier, aucun passage muet) :
  la piste audio est recopiée telle quelle, sans réencodage.
- Sinon : l'audio est décodé, découpé à l'échantillon près, et chaque point de
  coupe ou de silence reçoit un fondu de quelques millisecondes. Sans ce fondu,
  la forme d'onde saute brutalement d'une valeur à une autre, ce qui s'entend
  comme un clic. Le tout est réencodé une seule fois en AAC haute qualité.
"""

from __future__ import annotations

import contextlib
import io
import platform
from collections.abc import Iterator
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
import cv2
import numpy as np
from av.video.frame import PictureType

from . import project as pj
from .analyze import needed_intervals
from .blur import SUPPORTED_FORMATS, blur_frame
from .postprocess import ZoneIndex
from .progress import Progress
from .timeline import ResolvedSegment, format_time, mute_intervals, pts_to_sec, resolve_segments
from .video import iter_frames

FADE_S = 0.008  # fondu audio à chaque point de coupe ou de silence
COPYABLE_AUDIO = {"aac", "mp3", "ac3", "eac3", "alac"}
DEBUG_COLORS = {"face": (60, 220, 60), "plate": (60, 200, 255), "dash": (255, 90, 200), "manual": (255, 255, 255)}  # RVB


class RenderError(Exception):
    """Erreur expliquée à l'utilisateur, sans trace technique."""


# --- Vérifications avant rendu -------------------------------------------


def check_ready(project: pj.Project, segments: list[ResolvedSegment], output: Path) -> None:
    if not segments:
        raise RenderError("le montage est vide : aucun passage à garder.")
    used = [project.clip(cid) for cid in dict.fromkeys(s.clip_id for s in segments)]
    for clip in used:
        path = project.clip_path(clip)
        if not path.exists():
            raise RenderError(f"fichier source introuvable : {path}")
        if path.resolve() == output.resolve():
            raise RenderError("la sortie ne peut pas écraser un fichier source.")
        if pj.fingerprint(path) != clip.fingerprint:
            raise RenderError(
                f"{path} a changé depuis la création du projet : l'analyse ne correspond plus. "
                "Recréez le projet."
            )
        if needed_intervals(project, clip):
            raise RenderError(
                f"le clip {clip.id} contient des passages gardés qui n'ont pas été analysés. "
                "Lancez d'abord « moto-blur analyze » sur ce projet : sans cela, ces passages "
                "sortiraient sans flou."
            )
    first = used[0].video
    different = [
        c for c in used[1:]
        if (c.video.width, c.video.height, c.video.fps_fraction, c.video.pix_fmt)
        != (first.width, first.height, first.fps_fraction, first.pix_fmt)
    ]
    if different:
        lines = [
            f"  {c.id} : {c.video.width}x{c.video.height}, {float(c.video.fps_fraction):.3f} i/s, "
            f"{c.video.pix_fmt}  ({c.path})"
            for c in used
        ]
        raise RenderError(
            "les clips assemblés n'ont pas tous le même format :\n"
            + "\n".join(lines)
            + "\nRien n'a été choisi à votre place. Comportements possibles :\n"
            "  a) aligner tous les clips sur le premier (mise à l'échelle avec bandes noires si le\n"
            "     rapport d'image diffère ; images dupliquées ou supprimées si la fréquence diffère) ;\n"
            "  b) aligner sur la plus grande résolution et la plus haute fréquence ;\n"
            "  c) rendre chaque format dans un fichier séparé.\n"
            "Aucun de ces comportements n'est encore implémenté : dites-moi lequel vous voulez."
        )


def can_copy_everything(project: pj.Project, segments: list[ResolvedSegment]) -> bool:
    """Vrai si le montage ne contient aucune coupe : un clip entier, sans passage muet."""
    if len(segments) != 1:
        return False
    seg = segments[0]
    clip = project.clip(seg.clip_id)
    whole = seg.in_pts <= clip.video.start_pts and seg.out_pts >= clip.video.end_pts
    return whole and not mute_intervals(project, clip)


# --- Choix de l'encodeur --------------------------------------------------


@dataclass
class EncoderChoice:
    name: str
    pix_fmt: str
    options: dict[str, str]
    bit_rate: int | None
    hardware: bool


def _target_bitrate(video: pj.VideoInfo, codec: str, quality: int) -> int:
    """Débit visé pour les encodeurs matériels pilotés au débit."""
    base = video.bit_rate
    if base <= 0:
        base = int(video.width * video.height * float(video.fps_fraction) * 0.12)
        source_codec = "h264"
    else:
        source_codec = video.codec
    if source_codec == "h264" and codec == "hevc":
        base = int(base * 0.7)
    elif source_codec == "hevc" and codec == "h264":
        base = int(base * 1.5)
    # 80 = 1,25 fois le débit source (un réencodage au même débit perd en qualité).
    return int(base * 1.25 * 2 ** ((quality - 80) / 20))


def encoder_candidates(
    codec: str, video: pj.VideoInfo, quality: int, encoder: str, ten_bit: bool
) -> list[EncoderChoice]:
    """Encodeurs à essayer, dans l'ordre : matériel de la machine, puis logiciel."""
    crf = round(34 - 0.2 * quality)  # 80 -> 18, 100 -> 14, 50 -> 24
    qp = str(crf)
    hw_fmt = "p010le" if ten_bit else "nv12"
    sw_fmt = video.pix_fmt if video.pix_fmt in SUPPORTED_FORMATS else "yuv420p"
    if codec == "h264" and ten_bit:
        sw_fmt = "yuv420p"  # le H.264 10 bits est très mal lu : sortie 8 bits
    bitrate = _target_bitrate(video, codec, quality)
    hardware = {
        f"{codec}_videotoolbox": EncoderChoice(
            f"{codec}_videotoolbox", "p010le" if ten_bit else "yuv420p", {"allow_sw": "0"}, bitrate, True
        ),
        f"{codec}_amf": EncoderChoice(
            f"{codec}_amf", hw_fmt, {"rc": "cqp", "qp_i": qp, "qp_p": qp, "quality": "quality"}, None, True
        ),
        f"{codec}_nvenc": EncoderChoice(
            f"{codec}_nvenc", hw_fmt, {"rc": "constqp", "qp": qp, "preset": "p5"}, None, True
        ),
        f"{codec}_qsv": EncoderChoice(f"{codec}_qsv", hw_fmt, {"global_quality": qp}, None, True),
    }
    if codec == "h264":
        software = EncoderChoice("libx264", sw_fmt, {"crf": str(crf), "preset": "medium"}, None, False)
    else:
        software = EncoderChoice(
            "libx265", sw_fmt,
            {"crf": str(crf + 2), "preset": "medium", "x265-params": "log-level=error"}, None, False,
        )
    if encoder == "software":
        return [software]
    if encoder not in ("auto", "hardware"):
        if encoder in hardware:
            return [hardware[encoder]]
        if encoder == software.name:
            return [software]
        raise RenderError(f"encodeur inconnu ou incompatible avec le codec {codec} : {encoder}")
    system = platform.system()
    order = {
        "Darwin": [f"{codec}_videotoolbox"],
        "Windows": [f"{codec}_amf", f"{codec}_nvenc", f"{codec}_qsv"],
    }.get(system, [f"{codec}_nvenc"])
    if codec == "h264" and ten_bit:
        order = []  # pas de H.264 10 bits en matériel
    chosen = [hardware[n] for n in order]
    if encoder == "auto":
        chosen.append(software)
    return chosen


def _configure(stream: av.VideoStream, choice: EncoderChoice, video: pj.VideoInfo, tb: Fraction, src=None):
    cc = stream.codec_context
    cc.width, cc.height = video.width, video.height
    cc.pix_fmt = choice.pix_fmt
    cc.time_base = tb
    cc.framerate = video.fps_fraction
    cc.options = dict(choice.options)
    if choice.bit_rate:
        cc.bit_rate = choice.bit_rate
    if choice.name.startswith(("hevc", "libx265")):
        cc.codec_tag = "hvc1"  # sans cela, QuickTime et iOS refusent de lire le H.265
    if src is not None:
        # Métadonnées couleur recopiées : sinon le lecteur devine et les couleurs dérivent.
        for attr in ("color_range", "colorspace", "color_primaries", "color_trc"):
            with contextlib.suppress(AttributeError, ValueError, TypeError):
                setattr(cc, attr, getattr(src, attr))


def _works(choice: EncoderChoice, video: pj.VideoInfo, tb: Fraction) -> str | None:
    """Essaie réellement l'encodeur sur une image. Renvoie None si ça marche, sinon l'erreur."""
    try:
        with av.open(io.BytesIO(), "w", format="mp4") as out:
            stream = out.add_stream(choice.name, rate=video.fps_fraction)
            _configure(stream, choice, video, tb)
            frame = av.VideoFrame(video.width, video.height, choice.pix_fmt)
            for plane in frame.planes:
                plane.update(bytes(plane.buffer_size))
            frame.pts, frame.time_base = 0, tb
            # On encode sans écrire : seul compte le fait que l'encodeur s'ouvre et accepte
            # l'image (libx265 date mal ses paquets quand la vidéo ne fait qu'une image).
            packets = list(stream.encode(frame)) + list(stream.encode(None))
            if not packets:
                return "aucune donnée produite"
        return None
    except Exception as exc:  # pilote absent, format refusé, etc.
        return f"{type(exc).__name__}: {exc}"


def pick_encoder(
    codec: str, video: pj.VideoInfo, quality: int, encoder: str, tb: Fraction
) -> EncoderChoice:
    ten_bit = "10" in video.pix_fmt or "12" in video.pix_fmt
    failures = []
    for choice in encoder_candidates(codec, video, quality, encoder, ten_bit):
        try:
            av.codec.Codec(choice.name, "w")
        except Exception:
            failures.append(f"{choice.name} : absent de cette installation")
            continue
        error = _works(choice, video, tb)
        if error is None:
            return choice
        failures.append(f"{choice.name} : {error}")
    raise RenderError("aucun encodeur utilisable :\n  " + "\n  ".join(failures))


# --- Audio ----------------------------------------------------------------


def _gain(positions: np.ndarray, total: int, fade: int, mutes: list[tuple[float, float]]) -> np.ndarray:
    """Gain par échantillon : fondus d'entrée et de sortie du passage, silences avec fondus."""
    fade = max(1, min(fade, total // 2)) if total >= 2 else 1
    gain = np.minimum((positions + 0.5) / fade, (total - positions - 0.5) / fade).clip(0, 1)
    for a, b in mutes:
        gain *= np.maximum((a - positions) / fade, (positions - b) / fade).clip(0, 1)
    return gain.astype(np.float32)


def audio_segment(
    path: Path | None,
    start_s: Fraction,
    total: int,
    rate: int,
    layout: str,
    channels: int,
    mutes_s: list[tuple[Fraction, Fraction]],
) -> Iterator[np.ndarray]:
    """Produit exactement `total` échantillons (canaux, n) pour un passage gardé.

    `start_s` : début du passage dans le temps du fichier source, en secondes.
    Si le fichier n'a pas de son (path None) ou si le son manque, du silence.
    """
    fade = round(FADE_S * rate)
    mutes = [(float((a - start_s) * rate), float((b - start_s) * rate)) for a, b in mutes_s]
    emitted = 0

    def finish(chunk: np.ndarray) -> np.ndarray:
        positions = np.arange(emitted, emitted + chunk.shape[1], dtype=np.float64)
        return chunk * _gain(positions, total, fade, mutes)

    if path is not None:
        with av.open(str(path)) as container:
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="fltp", layout=layout, rate=rate)
            # On se place un peu avant le passage : la première trame décodée après un saut est
            # inutilisable (le décodeur AAC a besoin de la trame précédente). Au tout début du
            # fichier on ne saute pas, sinon cette première trame serait perdue.
            seek_s = start_s - Fraction(1, 5)
            if seek_s > 0:
                container.seek(int(seek_s / stream.time_base), stream=stream, backward=True)
            position = None  # indice (relatif au passage) du prochain échantillon décodé
            for frame in container.decode(stream):
                if frame.pts is None:
                    continue
                if position is None:
                    position = round((frame.pts * stream.time_base - start_s) * rate)
                for out in resampler.resample(frame):
                    chunk = out.to_ndarray().astype(np.float32, copy=False)
                    begin = position
                    position += chunk.shape[1]
                    if begin < emitted:  # avant le passage (ou déjà émis) : on jette le début
                        chunk = chunk[:, emitted - begin :]
                        begin = emitted
                    if chunk.shape[1] == 0:
                        continue
                    if begin > emitted:  # trou dans le son source : silence
                        gap = min(begin - emitted, total - emitted)
                        yield np.zeros((channels, gap), dtype=np.float32)
                        emitted += gap
                    chunk = chunk[:, : total - emitted]
                    if chunk.shape[1]:
                        yield finish(chunk)
                        emitted += chunk.shape[1]
                    if emitted >= total:
                        return
    while emitted < total:
        n = min(total - emitted, rate)
        yield np.zeros((channels, n), dtype=np.float32)
        emitted += n


class AudioTimeline:
    """Assemble et encode le son du montage, au fil de l'avancement de la vidéo."""

    def __init__(self, project: pj.Project, segments: list[ResolvedSegment], out: av.container.OutputContainer):
        ref = next(
            (project.clip(s.clip_id).audio for s in segments if project.clip(s.clip_id).audio), None
        )
        assert ref is not None
        self.rate = ref.rate
        self.layout = ref.layout if ref.channels <= 2 else "stereo"
        self.channels = min(ref.channels, 2)
        self.stream = out.add_stream("aac", rate=self.rate)
        self.stream.layout = self.layout
        self.stream.bit_rate = min(320_000, max(ref.bit_rate, 128_000 * self.channels))
        self.out = out
        self.written = 0
        self.chunks = self._chunks(project, segments)

    def _chunks(self, project: pj.Project, segments: list[ResolvedSegment]) -> Iterator[np.ndarray]:
        done = 0
        for seg in segments:
            clip = project.clip(seg.clip_id)
            # Bornes cumulées : pas de dérive entre le son et l'image au fil des passages.
            total = round(seg.end * self.rate) - done
            done += total
            mutes = [
                (Fraction(a) * seg.tb, Fraction(b) * seg.tb) for a, b in mute_intervals(project, clip)
            ]
            path = project.clip_path(clip) if clip.audio else None
            yield from audio_segment(
                path, seg.in_pts * seg.tb, total, self.rate, self.layout, self.channels, mutes
            )

    def pump(self, until_s: float | None) -> None:
        """Encode le son jusqu'à l'instant `until_s` du montage (None : jusqu'au bout)."""
        while until_s is None or self.written < until_s * self.rate:
            chunk = next(self.chunks, None)
            if chunk is None:
                break
            frame = av.AudioFrame.from_ndarray(
                np.ascontiguousarray(chunk), format="fltp", layout=self.layout
            )
            frame.rate = self.rate
            frame.pts = self.written
            frame.time_base = Fraction(1, self.rate)
            self.written += chunk.shape[1]
            for packet in self.stream.encode(frame):
                self.out.mux(packet)

    def close(self) -> None:
        self.pump(None)
        for packet in self.stream.encode(None):
            self.out.mux(packet)


# --- Image ----------------------------------------------------------------


def _draw_debug(frame: av.VideoFrame, index: ZoneIndex, video: pj.VideoInfo) -> av.VideoFrame:
    """Mode debug : cadres de détection et scores dessinés à la place du flou."""
    rgb = np.ascontiguousarray(frame.to_ndarray(format="rgb24"))
    scale = max(1.0, video.height / 1080)
    thick = max(1, round(2 * scale))
    font = cv2.FONT_HERSHEY_SIMPLEX
    for item in index.active(frame.pts):
        shaped = item.shaped(frame.pts)
        if shaped is None:
            continue
        zone = shaped.box
        color = DEBUG_COLORS.get(item.track.kind, (255, 255, 255))
        # Zone qui serait floutée : contour jaune fin, à la forme réelle du flou.
        thin = max(1, thick // 2)
        if shaped.shape == "ellipse":
            center = ((zone[0] + zone[2]) // 2, (zone[1] + zone[3]) // 2)
            axes = ((zone[2] - zone[0]) // 2, (zone[3] - zone[1]) // 2)
            cv2.ellipse(rgb, center, axes, 0, 0, 360, (255, 230, 0), thin, cv2.LINE_AA)
        else:
            cv2.rectangle(rgb, zone[:2], zone[2:], (255, 230, 0), thin)
        obs = item.observation(frame.pts)
        label = pj.KIND_LABELS[item.track.kind]
        if obs is not None:
            x1, y1, x2, y2 = (round(v) for v in obs[:4])
            cv2.rectangle(rgb, (x1, y1), (x2, y2), color, thick)
            text = f"{label} {obs[4]:.2f} #{item.track.id}"
        else:
            raw = item.raw_box(frame.pts)
            if raw is not None:
                x1, y1, x2, y2 = (round(v) for v in raw)
                cv2.rectangle(rgb, (x1, y1), (x2, y2), (255, 120, 0), max(1, thick // 2))
            text = f"{label} (comble/prolonge) #{item.track.id}"
        origin = (zone[0], max(round(16 * scale), zone[1] - round(6 * scale)))
        cv2.putText(rgb, text, origin, font, 0.55 * scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
        cv2.putText(rgb, text, origin, font, 0.55 * scale, color, thick, cv2.LINE_AA)
    stamp = f"source {format_time(pts_to_sec(frame.pts, video))}  pts {frame.pts}"
    pos = (round(12 * scale), round(28 * scale))
    cv2.putText(rgb, stamp, pos, font, 0.7 * scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(rgb, stamp, pos, font, 0.7 * scale, (255, 255, 255), thick, cv2.LINE_AA)
    return av.VideoFrame.from_ndarray(rgb, format="rgb24")


def process_frame(
    frame: av.VideoFrame,
    index: ZoneIndex,
    blur: pj.BlurSettings,
    video: pj.VideoInfo,
    pix_fmt: str,
    debug: bool,
) -> av.VideoFrame:
    pts = frame.pts
    if debug:
        frame = _draw_debug(frame, index, video)
    else:
        zones = index.shaped(pts)
        if zones:
            if frame.format.name not in SUPPORTED_FORMATS:
                frame = frame.reformat(format="yuv420p")
            # Indispensable : le décodeur réutilise cette image comme référence pour les
            # suivantes. Sans copie, le flou contaminerait les images décodées ensuite.
            frame.make_writable()
            blur_frame(frame, zones, blur.cells, blur.style, blur.feather)
    if frame.format.name != pix_fmt:
        frame = frame.reformat(format=pix_fmt)
    frame.pict_type = PictureType.NONE  # l'encodeur choisit lui-même ses images clés
    return frame


# --- Rendu ----------------------------------------------------------------


def render_project(
    project: pj.Project,
    output: Path,
    quality: int = 80,
    codec: str = "source",
    encoder: str = "auto",
    debug: bool = False,
) -> None:
    output = Path(output)
    segments = resolve_segments(project)
    check_ready(project, segments, output)
    first_clip = project.clip(segments[0].clip_id)
    video = first_clip.video
    if codec == "source":
        codec = video.codec if video.codec in ("h264", "hevc") else "hevc"
    out_tb = video.tb
    choice = pick_encoder(codec, video, quality, encoder, out_tb)
    kind = "matériel" if choice.hardware else "logiciel"
    print(f"Encodeur vidéo : {choice.name} ({kind}), format {choice.pix_fmt}, qualité {quality}")

    has_audio = any(project.clip(s.clip_id).audio for s in segments)
    copy_audio = (
        has_audio
        and can_copy_everything(project, segments)
        and (first_clip.audio.codec in COPYABLE_AUDIO or output.suffix.lower() == ".mkv")
    )
    total_frames = round(sum(s.duration for s in segments) * video.fps_fraction)
    progress = Progress("Rendu", total_frames, float(video.fps_fraction))
    indexes: dict[str, ZoneIndex] = {}

    def zone_index(clip: pj.Clip) -> ZoneIndex:
        if clip.id not in indexes:
            state = project.analysis.get(clip.id) or pj.ClipAnalysis()
            indexes[clip.id] = ZoneIndex(state.tracks, project.blur_settings, clip.video)
        return indexes[clip.id]

    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.stem + ".partiel" + output.suffix)
    try:
        with av.open(str(partial), "w") as out:
            with av.open(str(project.clip_path(first_clip))) as src:
                vstream = out.add_stream(choice.name, rate=video.fps_fraction)
                _configure(vstream, choice, video, out_tb, src.streams.video[0].codec_context)

            def encode(frame: av.VideoFrame, out_pts: int) -> None:
                frame.pts, frame.time_base = out_pts, out_tb
                for packet in vstream.encode(frame):
                    out.mux(packet)
                progress.step()

            if copy_audio:
                print("Audio : aucune coupe, piste recopiée sans réencodage.")
                _render_with_audio_copy(project, first_clip, out, encode, zone_index, choice, debug)
            else:
                audio = AudioTimeline(project, segments, out) if has_audio else None
                if audio:
                    print(f"Audio : réencodé en AAC {audio.stream.bit_rate // 1000} kb/s, fondus aux coupes.")
                for seg in segments:
                    clip = project.clip(seg.clip_id)
                    index = zone_index(clip)
                    base = round(seg.offset / out_tb)
                    ratio = seg.tb / out_tb
                    count = 0
                    with av.open(str(project.clip_path(clip))) as src:
                        for frame in iter_frames(src, seg.in_pts, seg.out_pts):
                            rel = round((frame.pts - seg.in_pts) * ratio)
                            ready = process_frame(
                                frame, index, project.blur_settings, clip.video, choice.pix_fmt, debug
                            )
                            encode(ready, base + rel)
                            count += 1
                            if audio and count % 8 == 0:
                                audio.pump(float((base + rel) * out_tb) + 0.5)
                if audio:
                    audio.close()
            for packet in vstream.encode(None):
                out.mux(packet)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(output)
    print(progress.close())
    print(f"Vidéo écrite : {output}")


def _render_with_audio_copy(project, clip, out, encode, zone_index, choice, debug) -> None:
    """Un seul clip entier : l'image est réencodée, les paquets audio sont recopiés tels quels."""
    index = zone_index(clip)
    with av.open(str(project.clip_path(clip))) as src:
        v = src.streams.video[0]
        a = src.streams.audio[0]
        v.thread_type = "AUTO"
        audio_out = out.add_stream_from_template(a)
        for packet in src.demux(v, a):
            if packet.stream is a:
                if packet.dts is None:
                    continue
                packet.stream = audio_out
                out.mux(packet)
                continue
            for frame in packet.decode():
                if frame.pts is None:
                    continue
                pts = frame.pts
                ready = process_frame(frame, index, project.blur_settings, clip.video, choice.pix_fmt, debug)
                encode(ready, pts)
