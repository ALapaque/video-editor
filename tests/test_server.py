"""Serveur local de l'interface : état, montage, zones, image exacte, lecture par plages."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from conftest import FakeDetector, make_project, make_video
from moto_blur import project as pj
from moto_blur.analyze import analyze_project
from moto_blur.proxy import build_proxy
from moto_blur.server import Engine, make_server


def _serve(tmp_path, detector):
    video = make_video(tmp_path / "clip.mp4", frames=90)
    project = make_project(tmp_path, video)
    analyze_project(project, detect_fn=detector)
    engine = Engine(project)
    server = make_server(engine)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(method, path, body=None, headers=None, raw=False):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(base + path, data=data, method=method, headers=headers or {})
        with urllib.request.urlopen(request) as response:
            content = response.read()
            return (response, content) if raw else json.loads(content)

    call.engine = engine
    call.project = project
    call.close = lambda: (server.shutdown(), server.server_close())
    return call


@pytest.fixture
def api(tmp_path):
    call = _serve(tmp_path, FakeDetector())
    yield call
    call.close()


@pytest.fixture
def doubtful(tmp_path):
    """Même projet, mais la plaque est détectée avec une confiance faible : un passage à vérifier."""
    call = _serve(tmp_path, FakeDetector(score=0.22))
    yield call
    call.close()


def test_state_describes_the_project(api):
    state = api("GET", "/api/state")
    p = state["project"]
    assert p["clips"][0]["frames"] == 90 and p["clips"][0]["needs_analysis"] is False
    assert p["timeline"] == [{"clip": "c1", "start": 0.0, "end": 3.0}]
    (track,) = p["tracks"]["c1"]
    assert track["kind"] == "plate" and track["count"] == 90 and track["source"] == "auto"
    assert p["problem"] is None


def test_montage_edits_are_saved_and_flag_missing_analysis(api, tmp_path):
    api("PUT", "/api/montage", {"timeline": [{"clip": "c1", "start": 0.5, "end": 1.0}], "mutes": [{"clip": "c1", "start": 0.6, "end": 0.8}]})
    saved = pj.load(api.project.path)
    assert saved.timeline == [pj.Segment("c1", 0.5, 1.0)] and saved.mutes == [pj.Mute("c1", 0.6, 0.8)]
    # un passage hors du clip est borné, un passage vide est ignoré
    state = api("PUT", "/api/montage", {"timeline": [{"clip": "c1", "start": 2.0, "end": 99}, {"clip": "c1", "start": 1, "end": 1}]})
    assert state["project"]["timeline"] == [{"clip": "c1", "start": 2.0, "end": 3.0}]
    assert state["project"]["clips"][0]["needs_analysis"] is False  # tout le clip est déjà analysé


def test_zones_follow_corrections(api):
    zones = api("GET", "/api/zones?clip=c1&from=10&to=12")["frames"]
    assert set(zones) == {"10", "11", "12"}
    x1, y1, x2, y2, shape, _radius, kind, track_id, score = zones["10"][0]
    assert kind == "plate" and shape == "rounded" and score == 0.9 and x1 < 40 and x2 > 80
    api("PATCH", "/api/tracks", {"clip": "c1", "id": track_id, "disabled": True})
    assert api("GET", "/api/zones?clip=c1&from=10&to=12")["frames"] == {}
    assert pj.load(api.project.path).analysis["c1"].tracks[0].disabled
    state = api("PATCH", "/api/tracks", {"clip": "c1", "id": track_id, "disabled": False, "extend_after_s": 1.5})
    assert state["project"]["tracks"]["c1"][0]["extend_after_s"] == 1.5


def test_manual_zone_lifecycle(api):
    state = api("POST", "/api/manual", {"clip": "c1", "keyframes": [[1.0, 200, 20, 260, 50], [2.0, 220, 20, 280, 50]]})
    track_id = state["created"]
    zones = api("GET", "/api/zones?clip=c1&from=45&to=45")["frames"]["45"]
    manual = [z for z in zones if z[7] == track_id]
    assert manual[0][:5] == [210, 20, 270, 50, "rect"]  # interpolée à mi-chemin, sans marge
    api("POST", "/api/manual", {"clip": "c1", "id": track_id, "keyframes": [[1.0, 200, 20, 260, 50], [1.5, 200, 20, 260, 50]]})
    assert not any(z[7] == track_id for z in api("GET", "/api/zones?clip=c1&from=55&to=55")["frames"].get("55", []))
    api("DELETE", f"/api/tracks?clip=c1&id={track_id}")
    assert len(api("GET", "/api/state")["project"]["tracks"]["c1"]) == 1
    with pytest.raises(urllib.error.HTTPError) as error:
        api("POST", "/api/manual", {"clip": "c1", "keyframes": [[1.0, 200, 20, 201, 21], [2.0, 200, 20, 201, 21]]})
    assert error.value.code == 400


def test_deleting_a_detection_only_disables_it(api):
    track_id = api("GET", "/api/state")["project"]["tracks"]["c1"][0]["id"]
    state = api("DELETE", f"/api/tracks?clip=c1&id={track_id}")
    assert state["project"]["tracks"]["c1"][0]["disabled"] is True


def test_exact_frame_and_proxy_ranges(api):
    response, jpeg = api("GET", "/api/frame?clip=c1&frame=30&blur=1", raw=True)
    assert response.headers["Content-Type"] == "image/jpeg" and jpeg[:2] == b"\xff\xd8"
    build_proxy(api.project, api.project.clips[0])
    response, part = api("GET", "/media/c1", headers={"Range": "bytes=10-109"}, raw=True)
    assert response.status == 206 and len(part) == 100
    assert response.headers["Content-Range"].startswith("bytes 10-109/")
    assert api("GET", "/api/state")["project"]["clips"][0]["proxy"] is True


def test_settings_and_fixed_dash_zone(api):
    state = api("PUT", "/api/settings", {"blur": {"margin": 0.5, "face_shape": "rect"}})
    assert state["project"]["settings"]["blur"]["margin"] == 0.5
    with pytest.raises(urllib.error.HTTPError):
        api("PUT", "/api/settings", {"blur": {"inconnu": 1}})
    state = api("POST", "/api/dash", {"clip": "c1", "mode": "fixed", "time": 0, "box": [10, 120, 110, 170]})
    kinds = [t["kind"] for t in state["project"]["tracks"]["c1"]]
    assert kinds == ["plate", "dash"]


def test_requests_from_other_websites_are_refused(api):
    with pytest.raises(urllib.error.HTTPError) as error:
        api("PUT", "/api/montage", {"timeline": []}, headers={"Origin": "https://exemple.com"})
    assert error.value.code == 403
    assert len(pj.load(api.project.path).timeline) == 1


def test_browse_lists_videos_and_projects(api, tmp_path):
    listing = api("GET", "/api/browse")
    names = [f["name"] for f in listing["files"]]
    assert "clip.mp4" in names and "test.motoblur.json" in names


def test_review_validation_is_saved_in_the_project(doubtful):
    (item,) = doubtful("GET", "/api/state")["project"]["review"]
    assert item["done"] is False and item["key"].startswith("c1:")
    assert any(r.startswith("confiance faible") for r in item["reasons"])
    version = doubtful.engine.version
    state = doubtful("PUT", "/api/review", {"key": item["key"], "done": True})
    assert state["project"]["review"][0]["done"] is True
    assert state["version"] == version  # les zones n'ont pas changé : le cache de l'interface reste valable
    assert pj.load(doubtful.project.path).review_done == [item["key"]]
    # un projet rouvert retrouve la validation
    reopened = Engine(pj.load(doubtful.project.path))
    assert reopened.state()["project"]["review"][0]["done"] is True
    state = doubtful("PUT", "/api/review", {"key": item["key"], "done": False})
    assert state["project"]["review"][0]["done"] is False
    assert pj.load(doubtful.project.path).review_done == []
    with pytest.raises(urllib.error.HTTPError) as error:
        doubtful("PUT", "/api/review", {"key": "c1:0:1", "done": True})
    assert error.value.code == 404


def test_validation_is_forgotten_when_the_passage_changes(doubtful):
    state = doubtful("GET", "/api/state")
    (item,) = state["project"]["review"]
    doubtful("PUT", "/api/review", {"key": item["key"], "done": True})
    # la piste douteuse est écartée : le passage disparaît, sa validation aussi
    track_id = state["project"]["tracks"]["c1"][0]["id"]
    state = doubtful("PATCH", "/api/tracks", {"clip": "c1", "id": track_id, "disabled": True})
    assert state["project"]["review"] == []
    assert pj.load(doubtful.project.path).review_done == []
    # rétablie, elle est redemandée
    state = doubtful("PATCH", "/api/tracks", {"clip": "c1", "id": track_id, "disabled": False})
    assert [r["done"] for r in state["project"]["review"]] == [False]


def test_marks_lifecycle(api):
    assert api("GET", "/api/state")["project"]["marks"] == []
    state = api("POST", "/api/marks", {"clip": "c1", "time": 1.25})
    first = state["created"]
    assert state["project"]["marks"] == [{"id": first, "clip": "c1", "time": 1.25}]
    second = api("POST", "/api/marks", {"clip": "c1", "time": 99})["created"]  # borné à la durée du clip
    saved = pj.load(api.project.path).marks
    assert [m["id"] for m in saved] == [first, second] and saved[1]["time"] == 3.0
    state = api("DELETE", f"/api/marks?id={first}")
    assert [m["id"] for m in state["project"]["marks"]] == [second]
    assert [m["id"] for m in pj.load(api.project.path).marks] == [second]
    with pytest.raises(urllib.error.HTTPError) as error:
        api("DELETE", f"/api/marks?id={first}")
    assert error.value.code == 404
    with pytest.raises(urllib.error.HTTPError):
        api("POST", "/api/marks", {"clip": "inconnu", "time": 1})
    # un signalement dont le clip est retiré du projet disparaît avec lui
    api("PUT", "/api/montage", {"timeline": [], "mutes": [], "drop_unused_clips": True})
    assert api("GET", "/api/state")["project"]["marks"] == []


def test_older_project_files_load_without_review_state(api):
    path = api.project.path
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["review_done"] == [] and doc["marks"] == []
    del doc["review_done"], doc["marks"]
    path.write_text(json.dumps(doc), encoding="utf-8")
    old = pj.load(path)
    assert old.review_done == [] and old.marks == []
    state = Engine(old).state()
    assert state["project"]["marks"] == [] and all(r["done"] is False for r in state["project"]["review"])
