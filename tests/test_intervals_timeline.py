"""Intervalles et conversion temps source <-> temps du montage."""

from fractions import Fraction

import pytest

from flouto import intervals as iv
from flouto import project as pj
from flouto.timeline import (
    format_time,
    kept_intervals,
    mute_intervals,
    parse_time,
    resolve_segments,
    sec_to_pts,
    source_to_timeline,
    timeline_duration,
    timeline_to_source,
)


def test_normalize_merges_and_drops_empty():
    assert iv.normalize([(5, 9), (0, 3), (3, 4), (8, 12), (20, 20)]) == [(0, 4), (5, 12)]


def test_subtract():
    assert iv.subtract([(0, 100)], [(10, 20), (50, 60)]) == [(0, 10), (20, 50), (60, 100)]
    assert iv.subtract([(0, 10)], [(0, 10)]) == []
    assert iv.subtract([(0, 10), (20, 30)], [(5, 25)]) == [(0, 5), (25, 30)]
    assert iv.subtract([(0, 10)], []) == [(0, 10)]


def test_intersect_pad_total():
    assert iv.intersect([(0, 10), (20, 30)], [(5, 25)]) == [(5, 10), (20, 25)]
    assert iv.pad([(10, 20), (22, 30)], 5, 0, 32) == [(5, 32)]
    assert iv.total([(0, 10), (5, 20)]) == 20


def _project(video: pj.VideoInfo, segments, mutes=()) -> pj.Project:
    clips = [pj.Clip("c1", "a.mp4", "x", video), pj.Clip("c2", "b.mp4", "y", video)]
    return pj.Project(
        clips=clips,
        timeline=[pj.Segment(*s) for s in segments],
        mutes=[pj.Mute(*m) for m in mutes],
    )


def test_sec_to_pts_snaps_to_frame_grid(video_info):
    assert sec_to_pts(0, video_info) == 0
    assert sec_to_pts(1.0, video_info) == 15360
    # 0,05 s tombe entre l'image 1 (0,0333 s) et l'image 2 (0,0667 s)
    assert sec_to_pts(0.05, video_info, "ceil") == 2 * 512
    assert sec_to_pts(0.05, video_info, "floor") == 1 * 512
    # une valeur arrondie à la main (1/30 s = 0,033333) tombe bien sur l'image 1
    assert sec_to_pts(0.033333, video_info) == 512
    # borné à la durée du clip
    assert sec_to_pts(999, video_info) == video_info.end_pts


def test_sec_to_pts_with_start_offset_and_ntsc():
    v = pj.VideoInfo("h264", 1920, 1080, "yuv420p", "60000/1001", "1/60000", 1001, 1001 * 601, 600, 0)
    assert sec_to_pts(0, v) == 1001
    assert sec_to_pts(1001 / 60000 * 10, v) == 1001 + 10 * 1001


def test_segment_duration_is_exact_frame_count(video_info):
    p = _project(video_info, [("c1", 1.0, 2.0), ("c1", 5.0, 5.5)])
    segs = resolve_segments(p)
    assert [(s.in_pts, s.out_pts) for s in segs] == [(15360, 30720), (76800, 84480)]
    assert segs[0].duration == 1 and segs[1].offset == 1
    assert timeline_duration(segs) == Fraction(3, 2)


def test_source_to_timeline_and_back(video_info):
    p = _project(video_info, [("c2", 2.0, 3.0), ("c1", 1.0, 2.0), ("c1", 5.0, 6.0)])
    segs = resolve_segments(p)
    # image du clip c1 à 5,5 s : après c2 (1 s) et le premier passage de c1 (1 s)
    pts = sec_to_pts(5.5, video_info)
    assert source_to_timeline(segs, "c1", pts) == [Fraction(5, 2)]
    assert timeline_to_source(segs, 2.5) == ("c1", pts)
    # image coupée : aucune position dans le montage
    assert source_to_timeline(segs, "c1", sec_to_pts(3.0, video_info)) == []
    # borne de fin exclusive
    assert source_to_timeline(segs, "c1", sec_to_pts(2.0, video_info)) == []
    assert timeline_to_source(segs, 3.0) is None


def test_roundtrip_every_kept_frame(video_info):
    p = _project(video_info, [("c1", 0.5, 1.5), ("c2", 0.0, 0.7), ("c1", 4.0, 4.2)])
    segs = resolve_segments(p)
    for s in segs:
        for pts in range(s.in_pts, s.out_pts, 512):
            (t,) = [x for x in source_to_timeline(segs, s.clip_id, pts) if s.offset <= x < s.end]
            assert timeline_to_source(segs, t) == (s.clip_id, pts)


def test_same_passage_twice_gives_two_positions(video_info):
    p = _project(video_info, [("c1", 1.0, 2.0), ("c1", 1.0, 2.0)])
    segs = resolve_segments(p)
    assert source_to_timeline(segs, "c1", sec_to_pts(1.5, video_info)) == [Fraction(1, 2), Fraction(3, 2)]


def test_kept_and_mute_intervals(video_info):
    p = _project(video_info, [("c1", 1.0, 2.0), ("c1", 1.5, 3.0), ("c2", 0, 1)], [("c1", 1.2, 1.4)])
    segs = resolve_segments(p)
    assert kept_intervals(segs, "c1") == [(15360, 46080)]
    (m,) = mute_intervals(p, p.clip("c1"))
    assert m == (sec_to_pts(1.2, video_info, "floor"), sec_to_pts(1.4, video_info, "ceil"))
    assert mute_intervals(p, p.clip("c2")) == []


def test_empty_segment_is_ignored(video_info):
    p = _project(video_info, [("c1", 2.0, 2.0), ("c1", 3.0, 4.0)])
    assert len(resolve_segments(p)) == 1


def test_time_formatting():
    assert format_time(83.4567) == "00:01:23.457"
    assert parse_time("1:23.5") == 83.5
    assert parse_time("0:01:23.5") == 83.5
    assert parse_time("12") == 12
    with pytest.raises(ValueError):
        parse_time("1:2:3:4")


def test_project_roundtrip(tmp_path, video_info):
    p = _project(video_info, [("c1", 1.0, 2.0)], [("c1", 1.2, 1.4)])
    p.analysis["c1"] = pj.ClipAnalysis(
        "sig", [[0, 100]], [pj.Track(1, "plate", [[0, 1.4, 2.6, 30, 40, 0.87654]])]
    )
    p.review = [{"clip": "c1", "start_pts": 0, "end_pts": 10, "kinds": ["plate"], "reasons": ["x"], "tracks": [1]}]
    pj.save(p, tmp_path / "p.json")
    q = pj.load(tmp_path / "p.json")
    assert q.timeline == p.timeline and q.mutes == p.mutes and q.review == p.review
    assert q.clips[0].video == video_info
    assert q.analysis["c1"].analyzed == [[0, 100]]
    assert q.analysis["c1"].tracks[0].obs == [[0, 1, 2, 30, 40, 0.877]]
    assert q.analysis_settings == p.analysis_settings and q.blur_settings == p.blur_settings
