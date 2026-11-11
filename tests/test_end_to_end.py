"""Bout en bout sur une vidéo de synthèse, avec un faux détecteur (aucun modèle requis)."""

import shutil

import av
import numpy as np
import pytest

from conftest import (
    FPS,
    FakeDetector,
    detail,
    make_project,
    make_video,
    object_box,
    read_audio,
    read_frames,
)
from moto_blur import project as pj
from moto_blur.analyze import analyze_project, needed_intervals
from moto_blur.blur import blur_frame
from moto_blur.render import RenderError, _gain, render_project


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("src") / "source.mp4", frames=90)


@pytest.fixture
def project(tmp_path, source):
    video = tmp_path / "clip.mp4"
    shutil.copy(source, video)
    return make_project(tmp_path, video)


def frame_count(project) -> int:
    return sum(len(t.obs) for a in project.analysis.values() for t in a.tracks)


# --- Analyse --------------------------------------------------------------


def test_analysis_only_covers_kept_passages_plus_margin(project):
    project.timeline = [pj.Segment("c1", 1.0, 1.5)]
    detector = FakeDetector()
    assert analyze_project(project, detect_fn=detector)
    # 0,5 s gardée + 1 s de marge avant, 1 s après = images 0 à 74
    assert detector.calls == 75
    assert needed_intervals(project, project.clips[0]) == []


def test_extending_a_passage_only_analyzes_the_missing_part(project):
    project.timeline = [pj.Segment("c1", 0.0, 0.5)]
    first = FakeDetector()
    analyze_project(project, detect_fn=first)
    assert first.calls == 45  # images 0 à 44
    tracks_before = [t.obs for t in project.analysis["c1"].tracks]

    project.timeline = [pj.Segment("c1", 0.0, 2.0)]  # passage rallongé après coup
    second = FakeDetector()
    analyze_project(project, detect_fn=second)
    assert second.calls == 45  # seulement les images 45 à 89
    assert [t.obs for t in project.analysis["c1"].tracks][: len(tracks_before)] == tracks_before
    # toutes les images du clip sont désormais couvertes par une observation
    seen = {o[0] for t in project.analysis["c1"].tracks for o in t.obs}
    assert len(seen) == 90

    third = FakeDetector()
    analyze_project(project, detect_fn=third)
    assert third.calls == 0  # relancer ne refait rien


def test_changing_cuts_does_not_invalidate_analysis(project):
    analyze_project(project, detect_fn=FakeDetector())
    before = pj.dumps(project).split('"analysis"')[1]
    project.timeline = [pj.Segment("c1", 2.0, 2.5), pj.Segment("c1", 0.2, 0.9)]
    project.mutes = [pj.Mute("c1", 0.3, 0.5)]
    detector = FakeDetector()
    analyze_project(project, detect_fn=detector)
    assert detector.calls == 0
    assert pj.dumps(project).split('"analysis"')[1] == before


def test_changing_analysis_settings_redoes_analysis(project):
    project.timeline = [pj.Segment("c1", 0.0, 0.5)]
    analyze_project(project, detect_fn=FakeDetector())
    project.analysis_settings.conf_new = 0.5
    detector = FakeDetector()
    analyze_project(project, detect_fn=detector)
    assert detector.calls == 45
    assert len(project.analysis["c1"].tracks) == 1


def test_detect_every_n_still_covers_every_frame(project, tmp_path):
    project.analysis_settings.detect_every = 3
    detector = FakeDetector()
    analyze_project(project, detect_fn=detector)
    assert detector.calls == 30
    out = tmp_path / "out.mp4"
    render_project(project, out, encoder="software")
    for i, frame in enumerate(read_frames(out)):
        x1, y1, x2, y2 = object_box(i)
        assert detail(frame[y1:y2, x1:x2]) < 8, f"image {i} : motif encore lisible"


def test_project_file_survives_reload(project):
    analyze_project(project, detect_fn=FakeDetector())
    reloaded = pj.load(project.path)
    assert frame_count(reloaded) == frame_count(project) == 90
    assert needed_intervals(reloaded, reloaded.clips[0]) == []


# --- Rendu ----------------------------------------------------------------


def test_render_refuses_unanalyzed_passages(project, tmp_path):
    project.timeline = [pj.Segment("c1", 0.0, 0.5)]
    analyze_project(project, detect_fn=FakeDetector())
    project.timeline = [pj.Segment("c1", 0.0, 3.0)]
    with pytest.raises(RenderError, match="pas été analysés"):
        render_project(project, tmp_path / "out.mp4", encoder="software")
    assert not (tmp_path / "out.mp4").exists()


def test_render_never_overwrites_source(project):
    analyze_project(project, detect_fn=FakeDetector())
    source_path = project.clip_path(project.clips[0])
    before = source_path.read_bytes()
    with pytest.raises(RenderError, match="écraser"):
        render_project(project, source_path, encoder="software")
    assert source_path.read_bytes() == before


def test_full_render_blurs_object_on_every_frame_and_keeps_the_rest(project, tmp_path, source):
    analyze_project(project, detect_fn=FakeDetector())
    out = tmp_path / "out.mp4"
    render_project(project, out, quality=100, encoder="software")
    original, result = read_frames(source), read_frames(out)
    assert len(result) == len(original) == 90
    for i, (a, b) in enumerate(zip(original, result, strict=False)):
        x1, y1, x2, y2 = object_box(i)
        assert detail(a[y1:y2, x1:x2]) > 25  # motif net dans la source
        assert detail(b[y1:y2, x1:x2]) < 8, f"image {i} : objet non flouté"
        # loin de l'objet, l'image est restée (presque) identique
        far = np.abs(a[120:, :].astype(int) - b[120:, :].astype(int))
        assert far.mean() < 4


def test_no_cut_copies_audio_without_reencoding(project, tmp_path, source):
    analyze_project(project, detect_fn=FakeDetector())
    out = tmp_path / "out.mp4"
    render_project(project, out, encoder="software")

    def packets(path):
        with av.open(str(path)) as c:
            return [bytes(p) for p in c.demux(c.streams.audio[0]) if p.dts is not None]

    assert packets(out) == packets(source)


def test_cuts_are_frame_accurate_and_applied_in_one_render(project, tmp_path, source):
    analyze_project(project, detect_fn=FakeDetector())
    # garder les images 15 à 29 puis 60 à 74, dans cet ordre inversé
    project.timeline = [pj.Segment("c1", 2.0, 2.5), pj.Segment("c1", 0.5, 1.0)]
    out = tmp_path / "out.mp4"
    render_project(project, out, quality=100, encoder="software")
    original, result = read_frames(source), read_frames(out)
    expected = list(range(60, 75)) + list(range(15, 30))
    assert len(result) == len(expected)
    for frame, i in zip(result, expected, strict=False):
        # l'image de sortie est bien l'image source attendue (fond identique)...
        far = np.abs(original[i][120:].astype(int) - frame[120:].astype(int)).mean()
        assert far < 4
        # ...et son objet est flouté à la bonne position
        x1, y1, x2, y2 = object_box(i)
        assert detail(frame[y1:y2, x1:x2]) < 8
    with av.open(str(out)) as c:
        assert float(c.streams.video[0].duration * c.streams.video[0].time_base) == pytest.approx(1.0, abs=0.001)
        assert float(c.streams.audio[0].duration * c.streams.audio[0].time_base) == pytest.approx(1.0, abs=0.03)


def test_no_audible_click_at_cut_points(project, tmp_path):
    analyze_project(project, detect_fn=FakeDetector())
    project.timeline = [pj.Segment("c1", 0.0, 0.7), pj.Segment("c1", 1.3, 2.1)]
    out = tmp_path / "out.mp4"
    render_project(project, out, encoder="software")
    audio = read_audio(out)[0]
    # Un sinus de 220 Hz d'amplitude 0,5 varie au plus de 0,0144 entre deux échantillons.
    # Une coupe sèche produirait un saut bien plus grand ; le fondu l'empêche.
    jumps = np.abs(np.diff(audio))
    assert jumps.max() < 0.03
    cut = round(0.7 * 48000)
    assert np.abs(audio[cut - 20 : cut + 20]).max() < 0.1  # passage par le silence à la coupe
    assert np.abs(audio[cut - 4800 : cut - 2400]).max() > 0.4  # le son est bien là avant


def test_muted_passage_is_silent_with_fades(project, tmp_path):
    analyze_project(project, detect_fn=FakeDetector())
    project.mutes = [pj.Mute("c1", 1.0, 2.0)]
    out = tmp_path / "out.mp4"
    render_project(project, out, encoder="software")
    audio = read_audio(out)[0]
    assert np.abs(audio[round(1.1 * 48000) : round(1.9 * 48000)]).max() < 0.01
    assert np.abs(audio[round(0.3 * 48000) : round(0.8 * 48000)]).max() > 0.4
    assert np.abs(audio[round(2.2 * 48000) : round(2.8 * 48000)]).max() > 0.4
    assert np.abs(np.diff(audio)).max() < 0.03
    assert len(read_frames(out)) == 90  # la vidéo n'est pas coupée


def test_gain_envelope():
    positions = np.arange(1000, dtype=np.float64)
    g = _gain(positions, 1000, 100, [(400.0, 600.0)])
    assert g[0] < 0.01 and g[-1] < 0.01 and g[200] == 1 and g[800] == 1
    assert g[400:600].max() == 0
    assert 0 < g[350] < 1 and 0 < g[650] < 1
    assert np.abs(np.diff(g)).max() <= 0.0101  # aucune marche


def test_two_clips_are_joined_in_order(tmp_path, source):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    shutil.copy(source, a)
    make_video(b, frames=30, audio=False)
    project = make_project(tmp_path, b, a)
    project.timeline = [pj.Segment("c1", 0.0, 1.0), pj.Segment("c2", 1.0, 2.0)]
    analyze_project(project, detect_fn=FakeDetector())
    out = tmp_path / "out.mp4"
    render_project(project, out, encoder="software")
    result = read_frames(out)
    assert len(result) == 60
    for frame, i in zip(result, list(range(30)) + list(range(30, 60)), strict=False):
        x1, y1, x2, y2 = object_box(i)
        assert detail(frame[y1:y2, x1:x2]) < 8
    audio = read_audio(out)[0]
    assert np.abs(audio[: 44000]).max() < 0.01  # premier clip sans son : silence
    assert np.abs(audio[52000:90000]).max() > 0.4


def test_mismatched_clips_are_reported_not_silently_converted(tmp_path, source):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    shutil.copy(source, a)
    make_video(b, frames=30, size=(640, 360))
    project = make_project(tmp_path, a, b)
    analyze_project(project, detect_fn=FakeDetector())
    with pytest.raises(RenderError, match="même format"):
        render_project(project, tmp_path / "out.mp4", encoder="software")


def test_hevc_output_and_debug_mode(project, tmp_path, source):
    analyze_project(project, detect_fn=FakeDetector())
    out = tmp_path / "debug.mp4"
    render_project(project, out, codec="hevc", encoder="software", debug=True)
    with av.open(str(out)) as c:
        assert c.streams.video[0].codec_context.name == "hevc"
    result = read_frames(out)
    assert len(result) == 90
    x1, y1, x2, y2 = object_box(40)
    assert detail(result[40][y1 + 8 : y2 - 8, x1 + 8 : x2 - 8]) > 25  # debug : objet visible, non flouté


def test_report_lists_doubtful_passages(project):
    from moto_blur.report import build_report

    analyze_project(project, detect_fn=FakeDetector(score=0.25, skip=set(range(30, 45))))
    text = build_report(project)
    assert "confiance faible" in text and "perdu puis retrouvé" in text
    assert "00:00:00.000" in text and "plaque" in text
    project.timeline = []
    assert "Total : 0 passage" in build_report(project)


# --- Flou -----------------------------------------------------------------


@pytest.mark.parametrize("fmt,dtype", [("yuv420p", np.uint8), ("yuv420p10le", np.uint16)])
def test_blur_destroys_detail_and_leaves_the_rest_untouched(fmt, dtype):
    rng = np.random.default_rng(0)
    frame = av.VideoFrame(320, 180, fmt)
    maxval = 255 if dtype == np.uint8 else 1023
    for plane in frame.planes:
        data = rng.integers(0, maxval + 1, (plane.height, plane.line_size // dtype().itemsize), dtype=dtype)
        plane.update(data.tobytes())
    before = [np.frombuffer(p, dtype=dtype).reshape(p.height, -1)[:, : p.width].copy() for p in frame.planes]
    blur_frame(frame, [(100, 40, 200, 100)], cells=5)
    after = [np.frombuffer(p, dtype=dtype).reshape(p.height, -1)[:, : p.width] for p in frame.planes]
    y_before, y_after = before[0], after[0]
    inside = y_after[40:100, 100:200].astype(float)
    # le bruit d'origine (écart-type ~ 30 % de la plage) est réduit à un aplat presque uniforme
    assert inside.std() < 0.03 * maxval
    assert np.abs(np.diff(inside, axis=1)).max() < 0.02 * maxval
    outside = np.ones_like(y_before, dtype=bool)
    outside[40:100, 100:200] = False
    assert (y_before[outside] == y_after[outside]).all()
    for b, a in zip(before[1:], after[1:], strict=False):
        assert (b[:20] == a[:20]).all() and a[20:50, 50:100].std() < 0.03 * maxval


def test_solid_style_is_a_flat_fill():
    frame = av.VideoFrame.from_ndarray(
        np.random.default_rng(0).integers(0, 255, (180, 320, 3), dtype=np.uint8), format="rgb24"
    ).reformat(format="yuv420p")
    blur_frame(frame, [(10, 10, 110, 60)], style="solid")
    y = np.frombuffer(frame.planes[0], dtype=np.uint8).reshape(frame.planes[0].height, -1)
    assert len(np.unique(y[10:60, 10:110])) == 1


def test_fps_constant_is_consistent():
    assert FPS == 30
