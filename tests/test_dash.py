"""Compteur : zone fixe et suivi à partir d'un clic."""

import shutil
import urllib.error

import pytest

from conftest import FakeDetector, detail, make_project, make_video, object_box, read_frames
from moto_blur import dash
from moto_blur import project as pj
from moto_blur.analyze import analyze_project
from moto_blur.render import RenderError, render_project


@pytest.fixture
def project(tmp_path):
    video = make_video(tmp_path / "clip.mp4", frames=90)
    return make_project(tmp_path, video)


def empty_detector():
    return FakeDetector(skip=set(range(1000)))


def test_fixed_zone_blurs_the_same_rectangle_on_every_frame(project, tmp_path):
    analyze_project(project, detect_fn=empty_detector())
    dash.set_fixed_zone(project, project.clips[0], (200, 120, 300, 170))
    out = tmp_path / "out.mp4"
    render_project(project, out, encoder="software")
    frames = read_frames(out)
    assert len(frames) == 90
    for frame in (frames[0], frames[45], frames[89]):
        assert detail(frame[125:165, 205:295]) < 8  # zone fixe : illisible
        assert detail(frame[20:50, 20:120]) > 25  # ailleurs : fond texturé intact
    # remplacer la zone fixe n'en laisse qu'une
    dash.set_fixed_zone(project, project.clips[0], (10, 10, 50, 50))
    assert len(dash.dash_tracks(project, project.clips[0])) == 1


@pytest.fixture
def tracker_available():
    try:
        dash._new_tracker()
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(f"modèle de suivi indisponible (pas de réseau) : {exc}")


def test_click_tracking_follows_the_object_both_ways(project, tmp_path, tracker_available):
    analyze_project(project, detect_fn=empty_detector())
    clip = project.clips[0]
    seed = clip.video.start_pts + round(45 * clip.video.frame_pts)
    track = dash.track_from_click(project, clip, seed, object_box(45))
    assert len(track.obs) == 90 and track.source == "tracker"
    for i, o in enumerate(track.obs):
        x1, y1, x2, y2 = object_box(i)
        assert abs(o[1] - x1) < 12 and abs(o[3] - x2) < 12 and abs(o[2] - y1) < 12, f"image {i}"
    out = tmp_path / "out.mp4"
    render_project(project, out, encoder="software")
    for i, frame in enumerate(read_frames(out)):
        x1, y1, x2, y2 = object_box(i)
        assert detail(frame[y1 + 4 : y2 - 4, x1 + 4 : x2 - 4]) < 8, f"image {i} : compteur lisible"


def test_tracking_is_extended_when_a_kept_passage_grows(project, tmp_path, tracker_available):
    project.timeline = [pj.Segment("c1", 0.5, 1.0)]
    analyze_project(project, detect_fn=empty_detector())
    clip = project.clips[0]
    dash.track_from_click(project, clip, round(20 * clip.video.frame_pts), object_box(20))
    covered = {o[0] for t in dash.dash_tracks(project, clip) for o in t.obs}
    assert len(covered) == 60  # images 0 à 59 : passage gardé + 1 s de marge
    assert dash.missing_span(project, clip) == []

    project.timeline = [pj.Segment("c1", 0.5, 3.0)]
    assert dash.missing_span(project, clip) != []
    with pytest.raises(RenderError, match="analys|compteur"):
        render_project(project, tmp_path / "out.mp4", encoder="software")
    analyze_project(project, detect_fn=empty_detector())
    assert dash.missing_span(project, clip) == []
    covered = {o[0] for t in dash.dash_tracks(project, clip) for o in t.obs}
    assert len(covered) == 90


def test_lost_tracking_is_reported_and_holds_last_position(project):
    clip = project.clips[0]
    f = round(clip.video.frame_pts)
    obs = [[i * f, 100, 100, 200, 150, 0.8 if i < 10 or i >= 20 else 0.1] for i in range(30)]
    state = project.analysis.setdefault(clip.id, pj.ClipAnalysis())
    state.tracks.append(pj.Track(1, "dash", obs, source="tracker"))
    (item,) = dash.build_dash_review(clip, state.tracks)
    assert (item["start_pts"], item["end_pts"]) == (10 * f, 19 * f)
    assert "« compteur » incertain" in item["reasons"][0]


def test_dash_survives_a_change_of_detection_settings(project):
    analyze_project(project, detect_fn=empty_detector())
    dash.set_fixed_zone(project, project.clips[0], (200, 120, 300, 170))
    project.analysis_settings.conf_new = 0.4
    analyze_project(project, detect_fn=empty_detector())
    assert len(dash.dash_tracks(project, project.clips[0])) == 1
    shutil.rmtree(project.path.parent, ignore_errors=True)


def test_several_named_objects_are_independent(project):
    """Compteur et téléphone : chacun sa zone, modifier l'un ne touche pas l'autre."""
    clip = project.clips[0]
    dash.set_fixed_zone(project, clip, (10, 10, 60, 40))  # sans nom : « compteur »
    dash.set_fixed_zone(project, clip, (200, 100, 260, 160), "téléphone")
    assert dash.object_names(project, clip) == ["compteur", "téléphone"]
    dash.set_fixed_zone(project, clip, (220, 100, 280, 160), "téléphone")  # remplace le téléphone seul
    assert len(dash.dash_tracks(project, clip)) == 2
    assert dash.dash_tracks(project, clip, label="compteur")[0].obs[0][1:5] == [10, 10, 60, 40]
    assert dash.dash_tracks(project, clip, label="téléphone")[0].obs[0][1:5] == [220, 100, 280, 160]
    pj.save(project)
    names = [t.name for t in pj.load(project.path).analysis[clip.id].tracks]
    assert names == ["compteur", "téléphone"]
    assert dash.clear_dash(project, clip, "téléphone") == 1
    assert dash.object_names(project, clip) == ["compteur"]


def test_tracking_one_object_keeps_the_others(project, tracker_available):
    analyze_project(project, detect_fn=empty_detector())
    clip = project.clips[0]
    f = clip.video.frame_pts
    dash.set_fixed_zone(project, clip, (10, 120, 60, 170), "GPS")
    dash.track_from_click(project, clip, round(45 * f), object_box(45), label="téléphone")
    dash.track_from_click(project, clip, round(10 * f), (250, 20, 300, 50), label="compteur")
    assert sorted(dash.object_names(project, clip)) == ["GPS", "compteur", "téléphone"]
    phone = dash.dash_tracks(project, clip, "tracker", "téléphone")
    assert len(phone) == 1 and len(phone[0].obs) == 90
    # relancer le suivi du téléphone à partir d'une autre image ne touche ni le compteur ni le GPS
    before = [t.obs for t in dash.dash_tracks(project, clip, label="compteur")]
    dash.track_from_click(project, clip, round(60 * f), object_box(60), "forward", label="téléphone")
    assert [t.obs for t in dash.dash_tracks(project, clip, label="compteur")] == before
    assert len(dash.dash_tracks(project, clip, "manual", "GPS")) == 1
    covered = {o[0] for t in dash.dash_tracks(project, clip, "tracker", "téléphone") for o in t.obs}
    assert len(covered) == 90
    assert dash.missing_span(project, clip) == []


def test_lost_tracking_reason_names_the_object(project):
    clip = project.clips[0]
    f = round(clip.video.frame_pts)
    obs = [[i * f, 100, 100, 200, 150, 0.1 if 5 <= i < 9 else 0.8] for i in range(20)]
    state = project.analysis.setdefault(clip.id, pj.ClipAnalysis())
    state.tracks.append(pj.Track(1, "dash", obs, source="tracker", label="téléphone"))
    (item,) = dash.build_dash_review(clip, state.tracks)
    assert "« téléphone »" in item["reasons"][0]
