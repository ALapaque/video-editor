"""Stabilisation, vérification par objet, annulation, préréglages, extrait, profil, médias."""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import av
import numpy as np
import pytest

from conftest import (
    FakeDetector,
    make_project,
    make_shots_video,
    make_video,
    object_box,
    read_frames,
)
from moto_blur import dash, library, media
from moto_blur import project as pj
from moto_blur.analyze import analyze_project, apply_preset, frames_to_analyze
from moto_blur.history import History
from moto_blur.postprocess import TrackZones, build_review, smooth_boxes
from moto_blur.render import clip_to_window, render_project
from moto_blur.server import Engine, make_server
from moto_blur.timeline import resolve_segments

F = 512


def none():
    return FakeDetector(skip=set(range(1000)))


# --- Flous stabilisés -----------------------------------------------------------


def test_smoothed_boxes_always_contain_the_detection_and_jitter_less():
    rng = np.random.default_rng(0)
    raw = []
    for i in range(60):
        cx, cy = 500 + 4 * i + rng.normal(0, 4), 300 + rng.normal(0, 4)
        w, h = 80 + rng.normal(0, 6), 40 + rng.normal(0, 4)
        raw.append((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))
    smooth = smooth_boxes(raw)
    for a, b in zip(raw, smooth, strict=True):
        assert b[0] <= a[0] + 1e-6 and b[1] <= a[1] + 1e-6 and b[2] >= a[2] - 1e-6 and b[3] >= a[3] - 1e-6
    # les bords haut et bas (l'objet ne bouge pas verticalement) tremblent deux fois moins
    for edge in (1, 3):
        jitter = lambda boxes, k=edge: np.abs(np.diff([b[k] for b in boxes])).mean()  # noqa: E731
        assert jitter(smooth) < 0.5 * jitter(raw)
    area = lambda boxes: np.mean([(b[2] - b[0]) * (b[3] - b[1]) for b in boxes])  # noqa: E731
    assert area(smooth) < 1.5 * area(raw)  # sans grossir démesurément


def test_smoothing_can_be_turned_off_and_never_touches_manual_zones(video_info):
    obs = [[i * F, 100 + (7 if i % 2 else 0), 100, 180, 140, 0.9] for i in range(10)]
    exact = pj.BlurSettings(margin=0, min_margin_px=0, extend_s=0, temporal_pad_frames=0, smooth=False)
    assert TrackZones(pj.Track(1, "plate", obs), exact, video_info).zone(5 * F) == (107, 100, 180, 140)
    smooth = pj.BlurSettings(margin=0, min_margin_px=0, extend_s=0, temporal_pad_frames=0)
    z = TrackZones(pj.Track(1, "plate", obs), smooth, video_info).zone(5 * F)
    assert z[0] <= 107 and z[2] >= 180 and z != (107, 100, 180, 140)
    manual = pj.Track(2, "manual", [[0, 10, 10, 50, 50, 1.0], [F, 12, 10, 52, 50, 1.0], [2 * F, 10, 10, 50, 50, 1.0]], source="manual")
    assert TrackZones(manual, pj.BlurSettings(), video_info).zone(F) == (12, 10, 52, 50)


# --- Vérification par objet -----------------------------------------------------


def test_busy_passage_is_split_into_one_item_per_object(video_info):
    weak = lambda i, x: pj.Track(i, "face", [[k * F, x, 100, x + 40, 140, 0.32] for k in range(20)])  # noqa: E731
    few = build_review("c1", [weak(1, 100), weak(2, 300)], video_info)
    assert len(few) == 1 and few[0]["tracks"] == [1, 2]
    assert few[0]["reasons_by_track"].keys() == {"1", "2"}
    assert all("confiance faible" in r[0] for r in few[0]["reasons_by_track"].values())
    many = build_review("c1", [weak(i, 100 * i) for i in range(1, 7)], video_info)
    assert [r["tracks"] for r in many] == [[1], [2], [3], [4], [5], [6]]
    # deux objets sur le même intervalle : deux clés différentes
    assert len({pj.review_key(r) for r in many}) == 6
    # une foule reste un seul passage : un par un serait interminable (la planche sert à cela)
    crowd = build_review("c1", [weak(i, 60 * i) for i in range(1, 13)], video_info)
    assert len(crowd) == 1 and len(crowd[0]["tracks"]) == 12


# --- Flou manuel qui suit -------------------------------------------------------


@pytest.fixture
def tracker_available():
    try:
        dash._new_tracker()
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(f"modèle de suivi indisponible : {exc}")


def test_added_blur_follows_its_target_and_stops_at_a_cut(tmp_path, tracker_available):
    path, truth = make_shots_video(tmp_path / "plans.mp4")
    project = make_project(tmp_path, path)
    analyze_project(project, detect_fn=none())
    clip = project.clips[0]
    f = clip.video.frame_pts
    track = dash.follow_region(project, clip, round(10 * f), truth[10], 0, round(90 * f))
    assert track.kind == "manual" and track.source == "tracker"
    frames = [round(o[0] / f) for o in track.obs]
    assert frames[0] == 0 and frames[-1] == 29  # tout le plan, pas au-delà de la coupe
    for o in track.obs:
        t = truth[round(o[0] / f)]
        assert abs(o[1] - t[0]) < 12 and abs(o[3] - t[2]) < 12
    assert track.extend_after_s == 0.0
    assert dash.missing_span(project, clip) == []  # ce n'est pas un « objet suivi » du clip


# --- Annuler / rétablir ---------------------------------------------------------


def test_history_restores_edits_but_keeps_detections(video_info):
    p = pj.Project(clips=[pj.Clip("c1", "a.mp4", "x", video_info)], timeline=[pj.Segment("c1", 0, 5)])
    auto = pj.Track(1, "face", [[0, 1, 2, 30, 40, 0.9]])
    p.analysis["c1"] = pj.ClipAnalysis("s", [], [auto])
    h = History()
    h.record(p, "coupe")
    p.timeline = [pj.Segment("c1", 0, 2), pj.Segment("c1", 3, 5)]
    h.record(p, "flou retiré")
    auto.disabled = True
    h.record(p, "flou ajouté")
    p.analysis["c1"].tracks.append(pj.Track(2, "manual", [[0, 1, 1, 9, 9, 1.0], [F, 1, 1, 9, 9, 1.0]], source="manual"))
    assert h.labels() == {"undo": "flou ajouté", "redo": None}
    assert h.step_back(p) == "flou ajouté" and len(p.analysis["c1"].tracks) == 1
    assert h.step_back(p) == "flou retiré" and not auto.disabled
    assert h.step_back(p) == "coupe" and p.timeline == [pj.Segment("c1", 0, 5)]
    assert h.step_back(p) is None
    assert h.step_forward(p) == "coupe" and len(p.timeline) == 2
    assert h.step_forward(p) == "flou retiré" and p.analysis["c1"].tracks[0].disabled
    assert p.analysis["c1"].tracks[0] is auto  # la détection elle-même n'est jamais recopiée
    h.record(p, "autre")  # une nouvelle action efface ce qui pouvait être rétabli
    assert h.labels()["redo"] is None


# --- Préréglages ----------------------------------------------------------------


def test_presets_change_analysis_settings_and_keep_kinds(tmp_path):
    project = make_project(tmp_path, make_video(tmp_path / "v.mp4", frames=30))
    project.analysis_settings.kinds = ["plate"]
    normal = project.analysis_settings.signature()
    apply_preset(project, "rapide")
    a = project.analysis_settings
    assert (a.face_model, a.plate_model, a.detect_every, a.kinds) == ("yolo11n-face", "yolo-v9-t-640", 2, ["plate"])
    assert a.signature() != normal and project.preset == "rapide"
    apply_preset(project, "difficile")
    assert a.conf_new < 0.2 and a.detect_every == 1 and a.tile_size >= 640
    assert project.blur_settings.face_min_score < 0.4
    apply_preset(project, "normal")
    assert a.signature() == normal and project.blur_settings.min_hits == 3
    with pytest.raises(ValueError):
        apply_preset(project, "inconnu")
    pj.save(project)
    assert pj.load(project.path).preset == "normal"


# --- Extrait de contrôle et taille de sortie --------------------------------------


def test_window_selects_part_of_the_montage(video_info):
    p = pj.Project(clips=[pj.Clip("c1", "a.mp4", "x", video_info)],
                   timeline=[pj.Segment("c1", 0, 2), pj.Segment("c1", 5, 8)])
    parts = clip_to_window(resolve_segments(p), 1.0, 3.5)
    assert [(s.in_pts, s.out_pts, float(s.offset)) for s in parts] == [(15360, 30720, 0.0), (76800, 99840, 1.0)]
    assert clip_to_window(resolve_segments(p), 9, 12) == []


def test_extract_and_reduced_size_render(tmp_path):
    project = make_project(tmp_path, make_video(tmp_path / "v.mp4", frames=90))
    analyze_project(project, detect_fn=FakeDetector())
    out = tmp_path / "extrait.mp4"
    render_project(project, out, encoder="software", window=(1.0, 2.0), max_side=160)
    frames = read_frames(out)
    assert len(frames) == 30 and frames[0].shape[:2] == (90, 160)
    with av.open(str(out)) as c:
        assert float(c.streams.audio[0].duration * c.streams.audio[0].time_base) == pytest.approx(1.0, abs=0.03)
    # l'extrait montre bien les images 30 à 59, objet flouté (position réduite de moitié)
    x1, y1, x2, y2 = (v // 2 for v in object_box(40))
    assert np.abs(np.diff(frames[10][y1 + 2 : y2 - 2, x1 + 2 : x2 - 2].astype(int), axis=1)).mean() < 10


# --- Médias ---------------------------------------------------------------------


def test_thumbnail_filmstrip_and_waveform(tmp_path):
    project = make_project(tmp_path, make_video(tmp_path / "v.mp4", frames=60))
    analyze_project(project, detect_fn=FakeDetector())
    clip = project.clips[0]
    (track,) = project.analysis["c1"].tracks
    thumb = media.track_thumbnail(project, clip, track, 200)
    assert thumb[:2] == b"\xff\xd8" and media.track_thumbnail(project, clip, track, 200) == thumb
    strip = media.filmstrip(project, clip, count=8, height=36)
    image = av.open(__import__("io").BytesIO(strip)).decode(video=0).__next__()
    assert (image.width, image.height) == (8 * 64, 36)
    peaks = media.waveform(project, clip, points=100)
    assert len(peaks) == 100 and max(peaks) == 1.0 and min(peaks) > 0.5  # sinus continu
    silent = make_project(tmp_path / "s", make_video((tmp_path / "s").mkdir() or tmp_path / "s" / "v.mp4", frames=30, audio=False))
    assert media.waveform(silent, silent.clips[0]) == []


# --- Récents, ménage, profil ------------------------------------------------------


def test_recent_projects_and_preview_cleanup(tmp_path):
    a = make_project(tmp_path, make_video(tmp_path / "a.mp4", frames=30))
    pj.save(a)
    library.remember(a.path)
    assert [r["path"] for r in library.recent()] == [str(a.path.resolve())]
    other = tmp_path / "vieux.motoblur.proxies"
    other.mkdir()
    (other / "c1.mp4").write_bytes(b"x" * 5000)
    mine = tmp_path / "test.motoblur.proxies"
    mine.mkdir()
    (mine / "c1.mp4").write_bytes(b"x" * 100)
    listing = {p["name"]: p for p in library.storage(a)["previews"]}
    assert listing["vieux"]["size"] == 5000 and listing["vieux"]["orphan"] and not listing["vieux"]["current"]
    assert listing["test"]["current"]
    with pytest.raises(ValueError):
        library.delete_previews(str(mine), a)  # jamais ceux du projet ouvert
    with pytest.raises(ValueError):
        library.delete_previews(str(tmp_path), a)  # ni un dossier quelconque
    assert library.delete_previews(str(other), a) == 5000 and not other.exists()
    a.path.unlink()
    assert library.recent() == []


def test_profile_finds_the_object_in_a_new_video(tmp_path, tracker_available):
    path, truth = make_shots_video(tmp_path / "une.mp4")
    first = make_project(tmp_path, path)
    analyze_project(first, detect_fn=none())
    clip = first.clips[0]
    dash.track_from_click(first, clip, round(10 * clip.video.frame_pts), truth[10])
    dash.set_fixed_zone(first, clip, (500, 20, 600, 60), "GPS")
    first.blur_settings.margin = 0.4
    assert library.profile_summary() is None
    summary = library.save_profile(first)
    assert sorted(summary["objects"]) == ["GPS", "compteur"]
    # une autre vidéo (mêmes plans) : le compteur est retrouvé sans aucune désignation
    (tmp_path / "b").mkdir()
    path2, truth2 = make_shots_video(tmp_path / "b" / "deux.mp4")
    second = make_project(tmp_path / "b", path2)
    notes = library.apply_profile(second)
    clip2 = second.clips[0]
    assert sorted(dash.object_names(second, clip2)) == ["GPS", "compteur"]
    assert second.blur_settings.margin == 0.4
    assert any("retrouvé" in n for n in notes)
    f = clip2.video.frame_pts
    obs = {round(o[0] / f): o for t in dash.dash_tracks(second, clip2, "tracker") for o in t.obs}
    assert all(abs(obs[i][1] - truth2[i][0]) < 15 for i in range(5, 28))
    # appliquer deux fois ne duplique rien
    library.apply_profile(second)
    assert len(dash.dash_tracks(second, clip2, "manual", "GPS")) == 1


# --- Serveur --------------------------------------------------------------------


@pytest.fixture
def api(tmp_path):
    project = make_project(tmp_path, make_video(tmp_path / "clip.mp4", frames=90))
    analyze_project(project, detect_fn=FakeDetector())
    engine = Engine(project)
    server = make_server(engine)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(method, path, body=None, raw=False):
        data = None if body is None else json.dumps(body).encode()
        with urllib.request.urlopen(urllib.request.Request(base + path, data=data, method=method)) as response:
            content = response.read()
            return content if raw else json.loads(content)

    def wait():
        for _ in range(200):
            state = call("GET", "/api/state")
            if not state["job"] or state["job"]["state"] != "running":
                return state
            time.sleep(0.05)
        raise AssertionError("traitement trop long")

    call.engine, call.project, call.wait, call.base = engine, project, wait, base
    yield call
    server.shutdown()
    server.server_close()


def test_undo_and_redo_over_the_api(api):
    assert api("GET", "/api/state")["project"]["history"] == {"undo": None, "redo": None}
    api("PUT", "/api/montage", {"timeline": [{"clip": "c1", "start": 0, "end": 1}]})
    track_id = api("GET", "/api/state")["project"]["tracks"]["c1"][0]["id"]
    api("PATCH", "/api/tracks", {"clip": "c1", "id": track_id, "disabled": True})
    state = api("PATCH", "/api/tracks", {"clip": "c1", "id": track_id, "disabled": True})  # ne change rien
    assert state["project"]["history"]["undo"] == "correction d'un flou"
    state = api("POST", "/api/undo")
    assert state["undone"] == "correction d'un flou" and not state["project"]["tracks"]["c1"][0]["disabled"]
    state = api("POST", "/api/undo")
    assert state["undone"] == "montage" and state["project"]["timeline"][0]["end"] == 3.0
    with pytest.raises(urllib.error.HTTPError) as error:
        api("POST", "/api/undo")
    assert error.value.code == 409
    state = api("POST", "/api/redo")
    assert state["redone"] == "montage" and state["project"]["timeline"][0]["end"] == 1.0
    assert pj.load(api.project.path).timeline == [pj.Segment("c1", 0.0, 1.0)]
    # une modification refusée ne laisse rien dans l'historique
    with pytest.raises(urllib.error.HTTPError):
        api("POST", "/api/manual", {"clip": "c1", "keyframes": [[0, 1, 1, 2, 2], [1, 1, 1, 2, 2]]})
    assert api("GET", "/api/state")["project"]["history"]["undo"] == "montage"


def test_batch_disable_preset_sweep_and_thumbnail(api):
    track_id = api("GET", "/api/state")["project"]["tracks"]["c1"][0]["id"]
    state = api("PATCH", "/api/tracks", {"clip": "c1", "ids": [track_id], "disabled": True})
    assert state["project"]["tracks"]["c1"][0]["disabled"]
    state = api("PUT", "/api/sweep", {"position": 12.5, "done": True})
    assert state["project"]["sweep"] == {"position": 12.5, "done": True}
    assert pj.load(api.project.path).sweep["done"] is True
    assert api("GET", f"/api/thumb?clip=c1&id={track_id}", raw=True)[:2] == b"\xff\xd8"
    assert api("GET", "/api/filmstrip?clip=c1&count=6&h=30", raw=True)[:2] == b"\xff\xd8"
    assert len(api("GET", "/api/waveform?clip=c1&points=60")["peaks"]) == 60
    state = api("PUT", "/api/settings", {"preset": "rapide"})
    assert state["project"]["preset"] == "rapide" and state["project"]["clips"][0]["needs_analysis"]
    assert state["project"]["history"] == {"undo": None, "redo": None}  # ne s'annule pas
    assert frames_to_analyze(api.project) == 90
    with pytest.raises(urllib.error.HTTPError):
        api("PUT", "/api/settings", {"preset": "turbo"})


def test_snapshot_extract_and_export_presets(api, tmp_path):
    shot = api("POST", "/api/snapshot", {"clip": "c1", "frame": 30, "blur": True})
    assert shot["path"].endswith(".png") and (shot["width"], shot["height"]) == (320, 180)
    api("POST", "/api/render", {"output": "essai.mp4", "encoder": "software", "window": {"start": 0.5, "duration": 1.0}})
    state = api.wait()
    assert state["job"]["state"] == "done" and "essai_extrait.mp4" in state["job"]["message"]
    assert len(read_frames(tmp_path / "essai_extrait.mp4")) == 30
    api("POST", "/api/render", {"output": "petit.mp4", "encoder": "software", "preset": "leger", "max_side": 160})
    assert api.wait()["job"]["state"] == "done"
    assert read_frames(tmp_path / "petit.mp4")[0].shape[:2] == (90, 160)
    with pytest.raises(urllib.error.HTTPError):
        api("POST", "/api/render", {"output": "x.mp4", "preset": "inconnu"})


def test_recent_storage_and_profile_routes(api):
    api.engine.open_project(api.project.path)
    assert api("GET", "/api/recent")["projects"][0]["name"] == "test"
    assert "previews" in api("GET", "/api/storage")
    assert api("GET", "/api/state")["profile"] is None
    api("POST", "/api/dash", {"clip": "c1", "mode": "fixed", "time": 0, "box": [10, 120, 110, 170], "label": "GPS"})
    assert api("POST", "/api/profile/save")["profile"]["objects"] == ["GPS"]
    assert api("GET", "/api/state")["profile"]["objects"] == ["GPS"]


def test_followed_blur_over_the_api_can_be_undone(tmp_path, tracker_available):
    path, truth = make_shots_video(tmp_path / "plans.mp4")
    project = make_project(tmp_path, path)
    analyze_project(project, detect_fn=none())
    engine = Engine(project)
    engine.start_follow({"clip": "c1", "time": 10 / 30, "box": list(truth[10]), "start": 0, "end": 3})
    for _ in range(400):
        if engine.job.state != "running":
            break
        time.sleep(0.05)
    assert engine.job.state == "done", engine.job.message
    (track,) = project.analysis["c1"].tracks
    assert engine.job.to_json()["created"] == track.id and track.kind == "manual"
    assert engine.history.labels()["undo"] == "flou ajouté"
    engine.step(forward=False)
    assert project.analysis["c1"].tracks == []
    engine.step(forward=True)
    assert len(project.analysis["c1"].tracks) == 1
    with pytest.raises(Exception, match="trop petite"):
        engine.start_follow({"clip": "c1", "time": 0, "box": [10, 10, 12, 12]})


def test_plate_model_converted_for_batches_gives_the_same_boxes():
    """La version « par lot » du modèle de plaques détecte comme l'original, tuile par tuile."""
    from moto_blur.detect import Detector
    from moto_blur.models import batchable, ensure_model, get_spec

    spec = get_spec("yolo-v9-t-640", "plate")
    try:
        path = ensure_model(spec)
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(f"modèle indisponible : {exc}")
    converted, target = batchable(spec, path)
    assert converted.output == "ultralytics" and converted.dynamic and target != path
    import cv2

    image = np.random.default_rng(1).integers(60, 120, (360, 1280, 3)).astype(np.uint8)
    for x in (200, 800):  # deux « voitures » avec une plaque dessinée
        cv2.rectangle(image, (x - 60, 100), (x + 260, 300), (30, 30, 120), -1)
        cv2.rectangle(image, (x, 200), (x + 200, 248), (255, 255, 255), -1)
        cv2.rectangle(image, (x, 200), (x + 200, 248), (0, 0, 0), 2)
        cv2.putText(image, "AB-123-CD", (x + 8, 236), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 3)
    one = Detector(spec, path, (1280, 360), 0.05, device="cpu")
    many = Detector(converted, target, (1280, 360), 0.05, device="cpu")
    assert one.batch == 1 and many.batch == len(many.crops) > 1  # un seul appel pour toutes les tuiles
    a, b = one.detect(image), many.detect(image)
    assert len(a) == len(b) >= 1
    order = lambda d: d[np.argsort(d[:, 0])]  # noqa: E731
    # mêmes objets, mêmes scores ; les contours diffèrent de quelques pixels (fusion des doublons)
    assert np.allclose(order(a)[:, :4], order(b)[:, :4], atol=10.0)
    assert np.allclose(order(a)[:, 4], order(b)[:, 4], atol=0.02)


# --- Contour précis ---------------------------------------------------------------


def _contour_project(tmp_path):
    path, truth = make_shots_video(tmp_path / "plans.mp4")
    project = make_project(tmp_path, path)
    analyze_project(project, detect_fn=none())
    clip = project.clips[0]
    tracks = dash.track_from_click(project, clip, round(10 * clip.video.frame_pts), truth[10])
    return project, clip, tracks, truth


def test_contour_zone_covers_both_neighbouring_outlines(video_info):
    """Entre deux images de référence, le flou couvre la réunion des deux contours, grossie."""
    from moto_blur.blur import contour_mask, zone_outline

    square = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
    lozenge = [0.5, -0.1, 1.1, 0.5, 0.5, 1.1, -0.1, 0.5]
    obs = [[k * F, 400 + 2 * k, 300, 600 + 2 * k, 400, 1.0] for k in range(11)]
    track = pj.Track(1, "dash", obs, source="tracker", outline=[[0, *square], [10 * F, *lozenge]])
    zones = TrackZones(track, pj.BlurSettings(), video_info)
    exact, between = zones.shaped(0), zones.shaped(5 * F)
    assert exact.shape == "contour" and len(exact.polygons) == 1
    assert between.shape == "contour" and len(between.polygons) == 2
    x1, y1, x2, y2 = between.box
    local = [[(px - x1, py - y1) for px, py in poly] for poly in between.polygons]
    mask = contour_mask(y2 - y1, x2 - x1, local, between.grow, 0.12)
    # tout le carré et tout le losange, reportés sur la position de l'objet à cet instant
    for poly in between.polygons:
        inside = np.zeros(mask.shape, dtype=np.uint8)
        import cv2
        cv2.fillPoly(inside, [np.rint(np.array(poly) - (x1, y1)).astype(np.int32)], 1)
        assert mask[inside == 1].min() == 1.0
    assert mask.min() == 0.0  # mais pas tout le rectangle : les coins restent nets
    outline = zone_outline(between)
    assert len(outline) >= 8 and all(x1 <= x <= x2 and y1 <= y <= y2 for x, y in outline)
    # références trop éloignées, ou hors de la partie suivie : forme habituelle
    far = pj.Track(2, "dash", [[k * F, 400, 300, 600, 400, 1.0] for k in range(61)], source="tracker",
                   outline=[[0, *square], [60 * F, *square]])
    assert TrackZones(far, pj.BlurSettings(), video_info).shaped(30 * F).shape != "contour"
    assert zones.shaped(12 * F).shape != "contour"  # prolongation après la dernière image suivie
    # le projet garde le contour
    assert "outline" in pj._track_to_json(track)


def test_precise_contour_never_uncovers_the_object(tmp_path, tracker_available):
    from moto_blur import contour
    from moto_blur.blur import contour_mask
    from moto_blur.postprocess import ZoneIndex

    try:
        segmenter = contour.Segmenter(device="cpu")
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(f"modèle de segmentation indisponible : {exc}")
    project, clip, tracks, truth = _contour_project(tmp_path)
    result = contour.refine(project, clip, tracks, segmenter=segmenter)
    assert result["kept"] == result["total"] >= 8
    pj.save(project)
    project = pj.load(project.path)  # le contour survit à l'enregistrement
    index = ZoneIndex(project.analysis["c1"].tracks, project.blur_settings, clip.video)
    f = clip.video.frame_pts
    tight = 0
    for i, (tx1, ty1, tx2, ty2) in truth.items():
        covered = np.zeros((360, 640), dtype=np.float32)
        for zone in index.shaped(round(i * f)):
            x1, y1, x2, y2 = (max(0, zone.box[0]), max(0, zone.box[1]), min(640, zone.box[2]), min(360, zone.box[3]))
            if zone.shape == "contour":
                local = [[(px - x1, py - y1) for px, py in poly] for poly in zone.polygons]
                part = contour_mask(y2 - y1, x2 - x1, local, zone.grow, 0.0)
                tight += 1
            else:
                part = np.ones((y2 - y1, x2 - x1), dtype=np.float32)
            covered[y1:y2, x1:x2] = np.maximum(covered[y1:y2, x1:x2], part)
        assert covered[ty1:ty2, tx1:tx2].min() == 1.0, f"image {i} : compteur découvert"
    assert tight >= 50  # la plupart des images ont bien le contour précis
    # et le rendu final l'utilise sans erreur
    out = tmp_path / "contour.mp4"
    render_project(project, out, encoder="software")
    frame = read_frames(out)[10]
    x1, y1, x2, y2 = truth[10]
    assert np.abs(np.diff(frame[y1 + 4 : y2 - 4, x1 + 4 : x2 - 4].astype(int), axis=1)).mean() < 6


def test_contour_routes_and_undo(tmp_path, tracker_available, monkeypatch):
    from moto_blur import contour

    project, clip, tracks, truth = _contour_project(tmp_path)

    def fake_refine(project, clip, tracks, progress=None, segmenter=None):
        for t in tracks:
            t.outline = [[int(o[0]), 0, 0, 1, 0, 1, 1, 0, 1] for o in t.obs[::5]]
        return {"kept": 4, "total": 5}

    monkeypatch.setattr(contour, "refine", fake_refine)
    engine = Engine(project)
    assert engine.state()["features"] == {"contour": True}
    engine.start_contour({"clip": "c1", "label": "compteur"})
    for _ in range(200):
        if engine.job.state != "running":
            break
        time.sleep(0.02)
    assert engine.job.state == "done" and "pas fiable" in engine.job.message
    state = engine.state()
    assert all(t["contour"] for t in state["project"]["tracks"]["c1"])
    zones = engine.zones("c1", 10, 10)["frames"][10]
    assert zones[0][4] == "contour" and len(zones[0]) == 11 and len(zones[0][10]) >= 4
    engine.step(forward=False)  # « Annuler » retire le contour
    assert not any(t.outline for t in project.analysis["c1"].tracks)
    engine.step(forward=True)
    engine.start_contour({"clip": "c1", "label": "compteur", "clear": True})
    assert not any(t.outline for t in project.analysis["c1"].tracks)
    with pytest.raises(Exception, match="pas suivi"):
        engine.start_contour({"clip": "c1", "label": "GPS"})


# --- Images d'entraînement et modèles personnels -------------------------------------


def test_training_images_from_a_reviewed_project(tmp_path):
    from moto_blur.training import export_dataset

    project = make_project(tmp_path, make_video(tmp_path / "v.mp4", frames=60))
    with pytest.raises(ValueError, match="aucune plaque"):
        export_dataset(project)
    analyze_project(project, detect_fn=FakeDetector())
    (plate,) = project.analysis["c1"].tracks
    result = export_dataset(project, every_s=0.5)
    out = tmp_path / "test_entrainement"
    assert result["path"] == str(out)
    # 4 instants (toutes les demi-secondes), découpés en tuiles comme pour la détection
    files = sorted((out / "labels").glob("*.txt"))
    assert len(files) == result["images"] == len(list((out / "images").glob("*.jpg"))) >= 4
    assert result["labels"] >= 4 and 0 < result["negatives"] <= result["images"] / 2
    import cv2

    checked = 0
    for file in files:
        text = file.read_text().split()
        if not text:
            continue
        tile = cv2.imread(str(out / "images" / (file.stem + ".jpg")))
        h, w = tile.shape[:2]
        cx, cy, bw, bh = (float(v) for v in text[1:5])
        assert text[0] == "0" and bw * w > 16 and bh * h > 30
        # l'étiquette entoure bien le carré blanc de la tuile
        assert tile[round(cy * h), round((cx - bw / 2) * w) + 3].min() > 200
        checked += 1
    assert checked >= 4
    assert "train: train.txt" in (out / "data.yaml").read_text() and "ne publiez pas" in (out / "LISEZMOI.txt").read_text()
    listed = (out / "train.txt").read_text().split() + (out / "val.txt").read_text().split()
    # trop peu d'images ici pour en réserver à la mesure : les deux listes sont les mêmes
    assert len(set(listed)) == result["images"] and all((out / line).exists() for line in listed)
    again = export_dataset(project, every_s=0.5)  # un second export complète sans doublon
    assert again["images"] == result["images"]
    assert len(set((out / "train.txt").read_text().split())) == len((out / "train.txt").read_text().split())
    # une détection retirée à la main devient un contre-exemple : image sans étiquette
    plate.disabled = True
    result = export_dataset(project, tmp_path / "neg")
    assert result["labels"] == 0 and result["negatives"] == result["images"] > 0


def test_personal_model_is_listed_and_loaded(tmp_path, monkeypatch):
    import shutil

    from moto_blur import models

    try:
        source = models.ensure_model(models.get_spec("yolo11n-face", "face"))
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(f"modèle indisponible : {exc}")
    monkeypatch.setenv("MOTO_BLUR_MODELS", str(tmp_path / "modeles"))
    target = tmp_path / "modeles" / "perso" / "plate" / "mes-plaques.onnx"
    target.parent.mkdir(parents=True)
    shutil.copy(source, target)
    assert models.available("plate")[-1] == "mes-plaques"
    spec = models.get_spec("mes-plaques", "plate")
    assert spec.output == "ultralytics" and spec.dynamic and spec.input_size == 640
    assert models.ensure_model(spec) == target
    assert models.batchable(spec, target) == (spec, target)
    with pytest.raises(ValueError, match="mes-plaques"):
        models.get_spec("absent", "plate")


def test_unread_request_body_does_not_break_the_next_request(api):
    """Une route qui ignore le corps envoyé ne doit pas corrompre la requête suivante."""
    import http.client

    host = urllib.parse.urlparse(api.base).netloc
    conn = http.client.HTTPConnection(host)
    conn.request("POST", "/api/cancel", body=b"{}", headers={"Content-Type": "application/json"})
    first = conn.getresponse()
    first.read()
    conn.request("GET", "/api/state")
    second = conn.getresponse()
    assert first.status == 200 and second.status == 200
    assert "project" in json.loads(second.read())
    conn.close()


def test_hand_made_detection_settings_show_as_custom(api):
    state = api("PUT", "/api/settings", {"analysis": {"conf_new": 0.15}})
    assert state["project"]["preset"] == "perso"
    state = api("PUT", "/api/settings", {"analysis": {"conf_new": 0.20}})
    assert state["project"]["preset"] == "normal"  # retour aux valeurs du préréglage
    api("PUT", "/api/settings", {"preset": "rapide"})
    state = api("PUT", "/api/settings", {"analysis": {"kinds": ["face", "plate"]}})
    assert state["project"]["preset"] == "rapide"  # les catégories ne changent pas le préréglage
    state = api("PUT", "/api/settings", {"blur": {"margin": 0.4}})
    assert state["project"]["preset"] == "rapide"
    assert api("GET", "/api/filmstrip?clip=c1&count=4&h=20", raw=True)[:2] == b"\xff\xd8"


# --- Plus rien à valider : gêne, flous semblables, zones à ne jamais flouter ---------------


def test_nuisance_similar_blurs_and_ignore_zones(video_info):
    from moto_blur.analyze import apply_ignore_zones
    from moto_blur.postprocess import footprint, in_ignore_zone, nuisance, similar_tracks

    def blur(i, x, y, w, h, frames, kind="face"):
        return pj.Track(i, kind, [[k * F, x, y, x + w, y + h, 0.5] for k in range(frames)])

    small_brief = blur(1, 100, 100, 20, 20, 5)
    big_long = blur(2, 800, 400, 300, 200, 150)
    area, seconds = footprint(big_long, video_info)
    assert area == pytest.approx(100 * 300 * 200 / (1920 * 1080), rel=1e-6) and seconds == pytest.approx(5.0, abs=0.05)
    assert nuisance(big_long, video_info) > 100 * nuisance(small_brief, video_info)
    # le même faux flou qui revient au même endroit, sous des identifiants différents
    mirror = [blur(10 + i, 1500 + 3 * i, 600 - 2 * i, 90, 90, 12) for i in range(4)]
    elsewhere = blur(20, 300, 300, 90, 90, 12)
    plate_there = blur(21, 1500, 600, 90, 90, 12, kind="plate")
    huge_there = blur(22, 1400, 500, 400, 400, 12)
    tracks = [small_brief, big_long, *mirror, elsewhere, plate_there, huge_there]
    assert [t.id for t in similar_tracks(tracks, mirror[0])] == [11, 12, 13]
    # zone à ne jamais flouter : seuls les flous qui s'y tiennent sont retirés
    zone = [1450, 540, 1650, 720]
    assert in_ignore_zone(mirror[0], [zone]) and not in_ignore_zone(elsewhere, [zone])
    passing = pj.Track(30, "face", [[k * F, 1000 + 60 * k, 600, 1090 + 60 * k, 690, 0.8] for k in range(20)])
    assert not in_ignore_zone(passing, [zone])  # un visage qui ne fait que traverser reste flouté
    p = pj.Project(clips=[pj.Clip("c1", "a.mp4", "x", video_info)], timeline=[pj.Segment("c1", 0, 5)])
    p.analysis["c1"] = pj.ClipAnalysis("s", [], [*tracks, passing])
    p.ignore = {"c1": [zone]}
    assert apply_ignore_zones(p) == 5 and apply_ignore_zones(p) == 0
    assert {t.id for t in p.analysis["c1"].tracks if t.disabled} == {10, 11, 12, 13, 21}


def test_ignore_zone_similar_and_risk_over_the_api(api):
    state = api("GET", "/api/state")
    (track,) = state["project"]["tracks"]["c1"]
    assert track["area"] > 0 and track["nuisance"] > 0 and state["project"]["ignore"] == {}
    assert api("GET", f"/api/similar?clip=c1&id={track['id']}")["ids"] == []
    # le carré blanc se déplace de x=20 à x=240 : une zone qui le contient presque toujours
    state = api("POST", "/api/ignore", {"clip": "c1", "box": [0, 40, 260, 120]})
    assert state["ignored"] == 1 and state["project"]["tracks"]["c1"][0]["disabled"]
    assert state["project"]["ignore"] == {"c1": [[0, 40, 260, 120]]}
    assert pj.load(api.project.path).ignore == {"c1": [[0, 40, 260, 120]]}
    state = api("POST", "/api/undo")
    assert state["project"]["ignore"] == {} and not state["project"]["tracks"]["c1"][0]["disabled"]
    api("POST", "/api/redo")
    state = api("DELETE", "/api/ignore?clip=c1&index=0")
    assert state["restored"] == 1 and not state["project"]["tracks"]["c1"][0]["disabled"]
    with pytest.raises(urllib.error.HTTPError):
        api("POST", "/api/ignore", {"clip": "c1", "box": [0, 0, 3, 3]})
    # « ne plus jamais flouter cet endroit » à partir d'un flou : sa place habituelle, élargie
    track_id = state["project"]["tracks"]["c1"][0]["id"]
    state = api("POST", "/api/ignore", {"clip": "c1", "id": track_id})
    (zone,) = state["project"]["ignore"]["c1"]
    assert zone[1] < 60 and zone[3] > 100 and zone[2] - zone[0] == 60  # carré de 40 px, élargi d'un quart
    api("DELETE", "/api/ignore?clip=c1&index=0")
    # un flou douteux n'est pas un risque ; un objet suivi perdu en est un
    api.project.analysis["c1"].tracks[0].obs = [[o[0], *o[1:5], 0.3] for o in api.project.analysis["c1"].tracks[0].obs]
    api("PUT", "/api/settings", {"blur": {"plate_min_score": 0.1}})
    review = api("GET", "/api/state")["project"]["review"]
    assert review and all(r["risk"] is False for r in review)
