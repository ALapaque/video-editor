"""Suivi, interpolation, prolongation et marges : là où se jouent les fuites."""

import numpy as np
import pytest

from flouto import project as pj
from flouto.detect import Crop, make_crops, merge_detections, to_frame_coords
from flouto.postprocess import TrackZones, ZoneIndex, build_review, track_reasons
from flouto.tracking import Tracker

F = 512  # PTS par image (30 i/s, base 1/15360)


def det(x, y, w=40, h=20, score=0.9):
    return np.array([[x, y, x + w, y + h, score]], dtype=np.float32)


def none():
    return np.zeros((0, 5), dtype=np.float32)


def covers(zone, box) -> bool:
    return zone is not None and zone[0] <= box[0] and zone[1] <= box[1] and zone[2] >= box[2] and zone[3] >= box[3]


# --- Suivi ----------------------------------------------------------------


def test_one_moving_object_is_one_track():
    t = Tracker("plate", conf_new=0.2, max_gap_pts=30 * F)
    rng = np.random.default_rng(0)
    for i in range(60):
        jitter = rng.integers(-3, 4, 2)  # vibrations
        t.update(i * F, det(100 + 6 * i + jitter[0], 300 + jitter[1]))
    tracks = t.finish()
    assert len(tracks) == 1 and len(tracks[0].obs) == 60


def test_low_score_extends_track_but_never_creates_one():
    t = Tracker("plate", conf_new=0.2, max_gap_pts=30 * F)
    t.update(0, det(500, 500, score=0.06))  # faible, aucune piste : ignorée
    assert t.finish() == []
    t = Tracker("plate", conf_new=0.2, max_gap_pts=30 * F)
    t.update(0, det(100, 100, score=0.8))
    t.update(F, det(104, 100, score=0.06))  # faible mais au bon endroit : la piste continue
    t.update(2 * F, det(900, 700, score=0.06))  # faible et ailleurs : ignorée
    (track,) = t.finish()
    assert [o[0] for o in track.obs] == [0, F]


def test_track_resumes_after_gap_and_expires_after_max_gap():
    t = Tracker("face", conf_new=0.2, max_gap_pts=30 * F)
    for i in range(10):
        t.update(i * F, det(100 + 5 * i, 100))
    for i in range(10, 25):
        t.update(i * F, none())  # détection perdue pendant 15 images
    for i in range(25, 35):
        t.update(i * F, det(100 + 5 * i, 100))
    assert len(t.finish()) == 1

    t = Tracker("face", conf_new=0.2, max_gap_pts=30 * F)
    t.update(0, det(100, 100))
    t.update(40 * F, det(100, 100))  # au-delà du trou maximal : nouvelle piste
    assert len(t.finish()) == 2


def test_two_objects_stay_separate_and_all_detections_are_kept():
    t = Tracker("face", conf_new=0.2, max_gap_pts=30 * F)
    for i in range(30):
        dets = np.concatenate([det(100 + 4 * i, 100), det(1500 - 4 * i, 800)])
        t.update(i * F, dets)
    tracks = t.finish()
    assert len(tracks) == 2
    assert sum(len(tr.obs) for tr in tracks) == 60


def test_small_object_with_strong_vibration_keeps_its_track():
    """Une plaque de 12 px qui saute de 10 px : sans élargissement, aucun recouvrement."""
    t = Tracker("plate", conf_new=0.2, max_gap_pts=30 * F)
    for i in range(20):
        t.update(i * F, det(800 + (10 if i % 2 else 0), 400, w=24, h=12))
    assert len(t.finish()) == 1


# --- Zones de flou --------------------------------------------------------


def track(obs) -> pj.Track:
    return pj.Track(1, "plate", [[p, x1, y1, x2, y2, s] for p, (x1, y1, x2, y2), s in obs])


def settings(**kw) -> pj.BlurSettings:
    base = dict(margin=0.0, min_margin_px=0, extend_s=0.0, extend_growth=0.0, temporal_pad_frames=0)
    base.update(kw)
    return pj.BlurSettings(**base)


def test_gap_is_filled_by_linear_interpolation(video_info):
    """Objet en mouvement uniforme, détection perdue 10 images : chaque image reste couverte."""
    def true_box(i):
        return (100 + 8 * i, 200, 160 + 8 * i, 230)

    obs = [(i * F, true_box(i), 0.9) for i in list(range(5)) + list(range(15, 20))]
    z = TrackZones(track(obs), settings(), video_info)
    for i in range(20):
        assert covers(z.zone(i * F), true_box(i)), f"image {i} non couverte"


def test_interpolation_between_non_frame_aligned_times(video_info):
    z = TrackZones(track([(0, (0, 0, 10, 10), 0.9), (4 * F, (40, 0, 50, 10), 0.9)]), settings(), video_info)
    assert z.zone(F) == (10, 0, 20, 10)
    assert z.zone(2 * F + F // 2) == (25, 0, 35, 10)


def test_blur_is_extended_before_and_after(video_info):
    """Le flou commence avant la première détection et continue après la dernière."""
    def true_box(i):
        return (500 + 10 * i, 300, 560 + 10 * i, 330)

    obs = [(i * F, true_box(i), 0.9) for i in range(10, 20)]
    z = TrackZones(track(obs), settings(extend_s=0.2, extend_growth=1.5), video_info)
    for i in range(4, 26):  # 6 images de prolongation de chaque côté (0,2 s à 30 i/s)
        assert covers(z.zone(i * F), true_box(i)), f"image {i} non couverte"
    assert z.zone(3 * F) is None and z.zone(27 * F) is None


def test_extension_covers_object_that_stopped(video_info):
    """Si l'objet s'arrête au lieu de continuer, la prolongation le couvre quand même."""
    obs = [(i * F, (500 + 10 * i, 300, 560 + 10 * i, 330), 0.9) for i in range(10)]
    z = TrackZones(track(obs), settings(extend_s=0.2), video_info)
    last = obs[-1][1]
    for i in range(10, 16):
        assert covers(z.zone(i * F), last)


def test_single_detection_is_still_blurred_and_extended(video_info):
    z = TrackZones(track([(10 * F, (100, 100, 140, 120), 0.3)]), settings(extend_s=0.2), video_info)
    for i in range(4, 17):
        assert covers(z.zone(i * F), (100, 100, 140, 120))


def test_margin_and_clamping(video_info):
    z = TrackZones(track([(0, (100, 100, 200, 140), 0.9)]), settings(margin=0.25), video_info)
    assert z.zone(0) == (75, 90, 225, 150)
    z = TrackZones(track([(0, (100, 100, 110, 104), 0.9)]), settings(margin=0.25, min_margin_px=6), video_info)
    assert z.zone(0) == (94, 94, 116, 110)  # marge minimale en pixels pour les petits objets
    z = TrackZones(track([(0, (0, 0, 100, 40), 0.9), (F, (1900, 1060, 1920, 1080), 0.9)]), settings(margin=0.5), video_info)
    assert z.zone(0)[:2] == (0, 0) and z.zone(F)[2:] == (1920, 1080)


def test_temporal_pad_covers_neighbouring_frames(video_info):
    """Objet rapide : la zone couvre aussi sa position à l'image précédente et suivante."""
    obs = [(i * F, (100 + 60 * i, 100, 140 + 60 * i, 120), 0.9) for i in range(5)]
    z = TrackZones(track(obs), settings(temporal_pad_frames=1), video_info)
    assert z.zone(2 * F) == (160, 100, 320, 120)
    # et le flou déborde d'une image avant la première détection
    assert z.zone(-F) is not None and z.zone(-2 * F) is None


def test_zone_index_finds_all_active_zones(video_info):
    a = pj.Track(1, "plate", [[i * F, 10, 10, 50, 30, 0.9] for i in range(100)])
    b = pj.Track(2, "face", [[i * F, 500, 500, 540, 540, 0.9] for i in range(50, 200)])
    index = ZoneIndex([a, b], settings(extend_s=0.3), video_info)
    assert [k for k, _ in index.zones(10 * F)] == ["plate"]
    assert sorted(k for k, _ in index.zones(75 * F)) == ["face", "plate"]
    assert [k for k, _ in index.zones(150 * F)] == ["face"]
    assert index.zones(260 * F) == []
    # le découpage en cases d'une seconde ne perd rien : comparaison avec un calcul direct
    for i in range(-20, 230):
        direct = [z.zone(i * F) for z in index.items if z.zone(i * F)]
        assert sorted(z for _, z in index.zones(i * F)) == sorted(direct)


def test_default_settings_never_shrink_below_detection(video_info):
    obs = [(i * F, (300 + 3 * i, 300, 380 + 3 * i, 330), 0.9) for i in range(30)]
    z = TrackZones(track(obs), pj.BlurSettings(), video_info)
    for i in range(30):
        assert covers(z.zone(i * F), obs[i][1])


# --- Passages douteux -----------------------------------------------------


def test_review_reasons(video_info):
    good = pj.Track(1, "plate", [[i * F, 500, 500, 600, 540, 0.9] for i in range(30)])
    assert track_reasons(good, video_info) == []
    weak = pj.Track(2, "plate", [[i * F, 500, 500, 600, 540, 0.2] for i in range(30)])
    assert any("confiance faible" in r for r in track_reasons(weak, video_info))
    gap = pj.Track(3, "plate", [[i * F, 500, 500, 600, 540, 0.9] for i in list(range(10)) + list(range(25, 35))])
    assert any("perdu puis retrouvé" in r for r in track_reasons(gap, video_info))
    small = pj.Track(4, "plate", [[i * F, 500, 500, 520, 508, 0.9] for i in range(30)])
    assert any("très petit" in r for r in track_reasons(small, video_info))
    short = pj.Track(5, "plate", [[0, 500, 500, 600, 540, 0.9]])
    assert any("très courte" in r for r in track_reasons(short, video_info))
    edge = pj.Track(6, "plate", [[i * F, 0, 500, 60, 540, 0.9] for i in range(30)])
    assert any("bord" in r for r in track_reasons(edge, video_info))


def test_gap_reason_accounts_for_detect_every(video_info):
    every3 = pj.Track(1, "plate", [[i * F, 500, 500, 600, 540, 0.9] for i in range(0, 60, 3)])
    assert track_reasons(every3, video_info, detect_every=3) == []
    assert track_reasons(every3, video_info, detect_every=1) == []  # 3 images = pas encore un trou
    every5 = pj.Track(1, "plate", [[i * F, 500, 500, 600, 540, 0.9] for i in range(0, 60, 5)])
    assert track_reasons(every5, video_info, detect_every=1) != []


def test_build_review_merges_close_passages(video_info):
    weak = [
        pj.Track(1, "plate", [[i * F, 500, 500, 600, 540, 0.2] for i in range(30)]),
        pj.Track(2, "face", [[i * F, 100, 100, 140, 140, 0.2] for i in range(40, 60)]),
        pj.Track(3, "face", [[i * F, 100, 100, 140, 140, 0.2] for i in range(200, 220)]),
        pj.Track(4, "face", [[i * F, 100, 100, 140, 140, 0.9] for i in range(100, 150)]),
    ]
    review = build_review("c1", weak, video_info)
    assert [(r["start_pts"], r["end_pts"]) for r in review] == [(0, 59 * F), (200 * F, 219 * F)]
    assert review[0]["kinds"] == ["plate", "face"] and review[0]["tracks"] == [1, 2]


# --- Tuiles ---------------------------------------------------------------


@pytest.mark.parametrize("size", [(3840, 2160), (1920, 1080), (2720, 1530), (640, 360), (1080, 1920), (1440, 2560)])
def test_tiles_cover_every_pixel_with_overlap(size):
    w, h = size
    crops = make_crops(w, h, 640, 0, 0.2, full_pass=True)
    tiles, full = crops[:-1], crops[-1]
    covered = np.zeros((h, w), dtype=np.uint8)
    for c in tiles:
        assert c.x0 >= 0 and c.x0 + c.w <= w and c.y0 >= 0 and c.y0 + c.h <= h
        covered[c.y0 : c.y0 + c.h, c.x0 : c.x0 + c.w] += 1
    assert covered.min() >= 1
    assert (full.w, full.h) == (w, h)
    # un objet à cheval sur deux tuiles est vu en entier par au moins l'une d'elles :
    # le chevauchement vaut au moins 10 % du côté de la tuile
    xs = sorted({c.x0 for c in tiles})
    for a, b in zip(xs, xs[1:], strict=False):
        assert a + tiles[0].w - b >= 0.1 * tiles[0].w


def test_4k_default_grid():
    crops = make_crops(3840, 2160, 640, 0, 0.2, True)
    assert len(crops) == 9  # 4 x 2 tuiles de 1280 px + l'image entière
    assert crops[0].scale == 0.5


def test_no_tiles_gives_single_full_pass():
    crops = make_crops(1920, 1080, 640, -1, 0.2, True)
    assert len(crops) == 1 and crops[0].pad_y == pytest.approx((640 - 360) / 2)


def test_crop_coordinates_roundtrip():
    tile = Crop(1280, 640, 1280, 1280, 0.5)
    back = to_frame_coords(np.array([[100, 50, 200, 80, 0.7]], dtype=np.float32), tile)
    assert back.tolist() == [[1480, 740, 1680, 800, pytest.approx(0.7)]]
    full = Crop(0, 0, 1920, 1080, 640 / 1920, 0, 140)
    back = to_frame_coords(np.array([[320, 320, 330, 330, 0.5]], dtype=np.float32), full)
    assert back[0, :4].tolist() == pytest.approx([960, 540, 990, 570])


def test_merge_detections_never_shrinks_coverage():
    boxes = np.array(
        [
            [100, 100, 200, 140, 0.9],  # objet vu en entier
            [150, 100, 200, 140, 0.5],  # même objet coupé par le bord d'une tuile
            [95, 98, 205, 143, 0.6],  # même objet, boîte un peu plus large
            [800, 800, 840, 820, 0.3],  # autre objet
        ],
        dtype=np.float32,
    )
    merged = merge_detections(boxes)
    assert len(merged) == 2
    assert merged[0].tolist() == [95, 98, 205, 143, pytest.approx(0.9)]
    assert merged[1, :4].tolist() == [800, 800, 840, 820]


# --- Formes du flou et corrections manuelles -------------------------------


def _inside_ellipse(zone, x, y) -> bool:
    x1, y1, x2, y2 = zone.box
    cx, cy, a, b = (x1 + x2) / 2, (y1 + y2) / 2, (x2 - x1) / 2, (y2 - y1) / 2
    return ((x - cx) / a) ** 2 + ((y - cy) / b) ** 2 <= 1.0


@pytest.mark.parametrize("margin", [0.0, 0.1, 0.3, 0.6])
def test_ellipse_always_contains_the_whole_detected_box(video_info, margin):
    t = pj.Track(1, "face", [[0, 400, 300, 460, 380, 0.9]])
    s = settings(margin=margin, face_shape="ellipse", feather=0.12)
    zone = TrackZones(t, s, video_info).shaped(0)
    assert zone.shape == "ellipse"
    for x, y in [(400, 300), (460, 300), (400, 380), (460, 380)]:
        # même en retirant le bord adouci, les coins de la boîte restent dans l'ellipse
        cx, cy = (zone.box[0] + zone.box[2]) / 2, (zone.box[1] + zone.box[3]) / 2
        assert _inside_ellipse(zone, cx + (x - cx) / (1 - 0.12), cy + (y - cy) / (1 - 0.12))


def test_rounded_corners_never_uncover_the_detected_box(video_info):
    from flouto.blur import shape_mask

    t = pj.Track(1, "plate", [[0, 500, 400, 620, 440, 0.9]])
    for margin, min_px in [(0.0, 3), (0.1, 6), (0.3, 6)]:
        zone = TrackZones(t, settings(margin=margin, min_margin_px=min_px), video_info).shaped(0)
        assert zone.shape == "rounded"
        x1, y1, x2, y2 = zone.box
        mask = shape_mask(y2 - y1, x2 - x1, "rounded", (0, 0, x2 - x1, y2 - y1), zone.radius, 0.12)
        covered = np.ones((y2 - y1, x2 - x1)) if mask is None else mask
        assert covered[400 - y1 : 440 - y1, 500 - x1 : 620 - x1].min() >= 0.99


def test_disabled_track_is_not_blurred_and_extension_can_be_overridden(video_info):
    t = pj.Track(1, "plate", [[i * F, 500, 400, 620, 440, 0.9] for i in range(10, 20)])
    index = ZoneIndex([t], settings(extend_s=0.1), video_info)
    assert index.shaped(15 * F) and not index.shaped(2 * F)
    t.extend_before_s = 0.3  # flou prolongé à la main : 9 images avant la première détection
    index = ZoneIndex([t], settings(extend_s=0.1), video_info)
    assert index.shaped(2 * F) and not index.shaped(0)
    assert index.shaped(22 * F) and not index.shaped(25 * F)  # la fin garde le réglage général
    t.disabled = True
    assert ZoneIndex([t], settings(), video_info).shaped(15 * F) == []


def test_manual_zone_is_exactly_what_was_drawn(video_info):
    t = pj.Track(7, "manual", [[10 * F, 100, 100, 300, 200, 1.0], [20 * F, 200, 100, 400, 200, 1.0]], source="manual")
    index = ZoneIndex([t], pj.BlurSettings(), video_info)
    (zone,) = index.shaped(15 * F)
    assert zone.box == (150, 100, 350, 200) and zone.shape == "rect"
    assert index.shaped(9 * F) == [] and index.shaped(21 * F) == []
    assert build_review("c1", [t], video_info) == []


def test_track_corrections_survive_save_and_load(tmp_path, video_info):
    p = pj.Project(clips=[pj.Clip("c1", "a.mp4", "x", video_info)])
    p.analysis["c1"] = pj.ClipAnalysis("s", [], [
        pj.Track(1, "face", [[0, 1, 2, 3, 4, 0.5]], disabled=True, extend_after_s=1.5),
        pj.Track(2, "dash", [[0, 1, 2, 3, 4, 1.0]], source="manual"),
    ])
    pj.save(p, tmp_path / "p.json")
    a, b = pj.load(tmp_path / "p.json").analysis["c1"].tracks
    assert a.disabled and a.extend_after_s == 1.5 and a.extend_before_s is None and a.source == "auto"
    assert b.kind == "dash" and b.source == "manual" and not b.disabled


def test_review_passages_are_capped_in_busy_scenes(video_info):
    # 40 pistes douteuses qui se chevauchent sans interruption pendant 30 s
    tracks = [pj.Track(i, "face", [[(i * 22 + k) * F, 100, 100, 140, 140, 0.2] for k in range(30)]) for i in range(40)]
    review = build_review("c1", tracks, video_info)
    assert len(review) >= 4
    assert all((r["end_pts"] - r["start_pts"]) / 15360 < 8 for r in review)
    assert sorted(t for r in review for t in r["tracks"]) == list(range(40))


def test_portrait_video_is_not_over_tiled():
    """Vidéo verticale 1440x2560 : 2 x 4 tuiles et l'image entière, pas 16 ou 19 zones."""
    assert len(make_crops(1440, 2560, 640, 0, 0.2, True)) == 9
    assert len(make_crops(1440, 2560, 608, 0, 0.2, True)) == 9
    assert len(make_crops(2560, 1440, 640, 0, 0.2, True)) == 9


# --- Score minimal d'une piste ------------------------------------------------


def test_low_score_tracks_are_not_blurred_unless_forced(video_info):
    from flouto.postprocess import below_min_score

    weak = pj.Track(1, "face", [[i * F, 100, 100, 140, 150, 0.22] for i in range(20)])
    real = pj.Track(2, "face", [[i * F, 600, 100, 640, 150, 0.25 if i not in (7, 8, 12) else 0.52] for i in range(20)])
    plate = pj.Track(3, "plate", [[i * F, 900, 500, 1000, 530, 0.3] for i in range(20)])
    s = pj.BlurSettings()
    assert below_min_score(weak, s) and not below_min_score(real, s) and not below_min_score(plate, s)
    ids = {z.track_id for z in ZoneIndex([weak, real, plate], s, video_info).shaped(5 * F)}
    assert ids == {2, 3}  # trois images assez sûres suffisent à garder toute la piste
    # une piste écartée ne figure pas dans les passages à vérifier
    assert all(1 not in r["tracks"] for r in build_review("c1", [weak, real, plate], video_info, settings=s))
    weak.forced = True
    assert {z.track_id for z in ZoneIndex([weak, real, plate], s, video_info).shaped(5 * F)} == {1, 2, 3}
    # réglage à zéro : comportement d'avant, tout est flouté
    weak.forced = False
    zero = pj.BlurSettings(face_min_score=0.0, plate_min_score=0.0)
    assert len(ZoneIndex([weak, real, plate], zero, video_info).shaped(5 * F)) == 3


def test_min_score_never_hides_manual_or_tracked_zones(video_info):
    from flouto.postprocess import below_min_score

    s = pj.BlurSettings(face_min_score=0.9, plate_min_score=0.9)
    manual = pj.Track(1, "manual", [[0, 1, 1, 50, 50, 1.0], [10 * F, 1, 1, 50, 50, 1.0]], source="manual")
    dash = pj.Track(2, "dash", [[i * F, 100, 100, 300, 200, 0.1] for i in range(11)], source="tracker")
    assert not below_min_score(manual, s) and not below_min_score(dash, s)
    assert len(ZoneIndex([manual, dash], s, video_info).shaped(5 * F)) == 2


# --- Flous superposés ------------------------------------------------------------


def _coverage(zones, w=400, h=300):
    """Part de chaque pixel couverte par une liste de zones (0 à 1), calculée par le vrai masque."""
    from flouto.blur import shape_mask

    total = np.zeros((h, w), dtype=np.float32)
    for z in zones:
        x1, y1, x2, y2 = (max(0, z.box[0]), max(0, z.box[1]), min(w, z.box[2]), min(h, z.box[3]))
        local = (z.box[0] - x1, z.box[1] - y1, z.box[2] - x1, z.box[3] - y1)
        mask = shape_mask(y2 - y1, x2 - x1, z.shape, local, z.radius, 0.0)
        part = np.ones((y2 - y1, x2 - x1), dtype=np.float32) if mask is None else mask
        total[y1:y2, x1:x2] = np.maximum(total[y1:y2, x1:x2], part)
    return total


def _nothing_uncovered(before, after) -> bool:
    """Tout pixel pleinement flouté avant la fusion l'est encore après (à l'anticrénelage près)."""
    return not ((_coverage(before) >= 0.99) & (_coverage(after) < 0.5)).any()


def test_zone_inside_another_is_dropped_and_overlapping_zones_become_one():
    from flouto.postprocess import Zone, merge_zones

    face = Zone("face", (100, 60, 300, 260), "ellipse", 0, 1)  # tableau de bord pris pour un visage
    plate = Zone("plate", (80, 110, 320, 210), "rounded", 20, 2)  # ... et pour une plaque
    inner = Zone("plate", (90, 130, 150, 190), "rounded", 8, 3)  # petite plaque dans la grande
    far = Zone("plate", (350, 10, 390, 30), "rounded", 4, 4)
    merged = merge_zones([face, plate, inner, far])
    assert len(merged) == 2
    big = max(merged, key=lambda z: z.box[2] - z.box[0])
    assert big.box == (80, 60, 320, 260) and big.kind == "plate"
    assert sorted(big.tracks) == [1, 2, 3]
    assert [z for z in merged if z is not big][0].box == far.box
    # zéro fuite : tout pixel flouté avant la fusion l'est encore après
    assert _nothing_uncovered([face, plate, inner, far], merged)


def test_zones_that_barely_touch_stay_separate_and_corners_are_respected():
    from flouto.postprocess import Zone, merge_zones

    a = Zone("face", (100, 100, 200, 200), "ellipse", 0, 1)
    b = Zone("face", (190, 100, 290, 200), "ellipse", 0, 2)  # 10 % de recouvrement
    assert len(merge_zones([a, b])) == 2
    # une petite zone dans le COIN de la boîte d'une ellipse n'est pas couverte par l'ellipse :
    # elle ne doit pas être supprimée
    corner = Zone("plate", (102, 102, 118, 112), "rect", 0, 3)
    merged = merge_zones([a, corner])
    assert _nothing_uncovered([a, corner], merged)


def test_detection_inside_a_tracked_object_gives_a_single_blur(video_info):
    dash = pj.Track(1, "dash", [[i * F, 800, 600, 1100, 800, 1.0] for i in range(10)], source="tracker", label="compteur") if "label" in pj.Track.__dataclass_fields__ else pj.Track(1, "dash", [[i * F, 800, 600, 1100, 800, 1.0] for i in range(10)], source="tracker")
    face = pj.Track(2, "face", [[i * F, 880, 650, 960, 740, 0.7] for i in range(10)])
    zones = ZoneIndex([dash, face], pj.BlurSettings(), video_info).shaped(5 * F)
    assert len(zones) == 1 and zones[0].kind == "dash" and sorted(zones[0].tracks) == [1, 2]
    apart = ZoneIndex([dash, face], pj.BlurSettings(merge_overlaps=False), video_info).shaped(5 * F)
    assert len(apart) == 2


def test_merging_random_zones_never_uncovers_a_pixel():
    """Tirage de nombreuses configurations : la fusion ne retire jamais de flou."""
    from flouto.postprocess import Zone, merge_zones

    rng = np.random.default_rng(7)
    for _ in range(150):
        zones = []
        for i in range(int(rng.integers(2, 7))):
            x, y = int(rng.integers(0, 300)), int(rng.integers(0, 220))
            w, h = int(rng.integers(8, 160)), int(rng.integers(8, 120))
            shape = ["rect", "rounded", "ellipse"][int(rng.integers(0, 3))]
            radius = int(rng.integers(1, max(2, min(w, h) // 2))) if shape == "rounded" else 0
            zones.append(Zone(["face", "plate", "dash"][int(rng.integers(0, 3))], (x, y, x + w, y + h), shape, radius, i + 1))
        merged = merge_zones(zones)
        assert len(merged) <= len(zones)
        assert sorted(t for z in merged for t in z.tracks) == list(range(1, len(zones) + 1))
        assert _nothing_uncovered(zones, merged), zones


def test_tracked_object_covered_by_a_larger_detection_keeps_its_name(video_info):
    plate = pj.Track(1, "plate", [[i * F, 700, 500, 1200, 800, 0.9] for i in range(10)])
    phone = pj.Track(2, "dash", [[i * F, 850, 580, 1000, 700, 1.0] for i in range(10)], source="tracker", label="téléphone")
    (zone,) = ZoneIndex([plate, phone], pj.BlurSettings(), video_info).shaped(5 * F)
    assert zone.kind == "dash" and zone.track_id == 2 and sorted(zone.tracks) == [1, 2]
    assert zone.box[0] <= 700 and zone.box[2] >= 1200  # la zone large est conservée


def test_flickering_detections_are_dropped_but_a_clear_one_is_kept(video_info):
    from flouto.postprocess import below_min_score

    s = pj.BlurSettings()  # visages : 0,40 sur 3 images, ou 0,65 une fois
    flicker = pj.Track(1, "face", [[0, 100, 100, 140, 150, 0.45]])
    twice = pj.Track(2, "face", [[0, 100, 100, 140, 150, 0.5], [F, 100, 100, 140, 150, 0.55]])
    clear = pj.Track(3, "face", [[0, 100, 100, 140, 150, 0.7]])
    seen = pj.Track(4, "face", [[i * F, 100, 100, 140, 150, 0.42] for i in range(3)])
    assert below_min_score(flicker, s) and below_min_score(twice, s)
    assert not below_min_score(clear, s) and not below_min_score(seen, s)
    # réglage à une image : comportement d'avant
    assert not below_min_score(flicker, pj.BlurSettings(min_hits=1))
