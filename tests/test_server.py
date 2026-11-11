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


@pytest.fixture
def api(tmp_path):
    video = make_video(tmp_path / "clip.mp4", frames=90)
    project = make_project(tmp_path, video)
    analyze_project(project, detect_fn=FakeDetector())
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
    yield call
    server.shutdown()
    server.server_close()


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
