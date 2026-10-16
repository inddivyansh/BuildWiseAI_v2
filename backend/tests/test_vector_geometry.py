"""Tests for deterministic geometry: scale parsing, snapping, measurement."""

import pymupdf
import pytest

from planlint.ingest.vector_geometry import (
    Primitive,
    TextLabel,
    detect_pdf_type,
    extract_primitives,
    measure_asset,
    parse_dimension_label,
    parse_scale,
    snap_box,
    snap_room_box,
)
from planlint.models import Parameter

PT = 1 / 72  # one PDF point in paper inches


# ------------------------------------------------------------- parse_scale

def test_scale_quarter_inch():
    # 1/4" on paper = 12 real inches -> factor 48 -> per PDF point 48/72
    assert parse_scale('1/4" = 1\'-0"') == pytest.approx(48 / 72)


def test_scale_eighth_inch():
    assert parse_scale('1/8" = 1\'') == pytest.approx(96 / 72)


def test_scale_ratio():
    assert parse_scale("1:50") == pytest.approx(50 / 72)


def test_scale_with_prefix_text():
    assert parse_scale('SCALE: 1/4" = 1\'-0"') == pytest.approx(48 / 72)


def test_scale_garbage():
    assert parse_scale("NOT A SCALE") is None


def test_scale_empty():
    assert parse_scale("") is None


# --------------------------------------------------- parse_dimension_label

def test_dimension_inches():
    assert parse_dimension_label('36"') == 36.0


def test_dimension_feet_inches():
    assert parse_dimension_label("3'-0\"") == 36.0


def test_dimension_feet_and_six():
    assert parse_dimension_label("2'-6\"") == 30.0


def test_dimension_plain_in():
    assert parse_dimension_label("30 in") == 30.0


def test_dimension_not_a_dimension():
    assert parse_dimension_label("FIRE EXIT") is None


def test_dimension_embedded_in_door_tag():
    assert parse_dimension_label('D1 36"') == 36.0


def test_dimension_rejects_structural_note():
    # A joist spacing note carries '16"' but is not a clear width — the '@'/'OC'
    # and lumber section '2X12' both mark it as a note.
    assert parse_dimension_label('2X12 JOISTS @ 16" OC') is None


def test_dimension_rejects_material_note():
    # '5/8" GYP BD' — the '8"' must not become a measurement (word 'GYP' beside it).
    assert parse_dimension_label('5/8" GYP BD') is None


def test_dimension_keeps_qualified_callout():
    # A real dimension with a MIN qualifier is still a dimension.
    assert parse_dimension_label('3\'-6" MIN') == 42.0


# ------------------------------------------------------------- parse_slope_label

def test_parse_slope_label():
    from planlint.ingest.vector_geometry import parse_slope_label

    assert parse_slope_label("1:12") == pytest.approx(1 / 12)
    assert parse_slope_label("SLOPE 1 IN 12") == pytest.approx(1 / 12)
    assert parse_slope_label("8.3%") == pytest.approx(0.083)
    assert parse_slope_label("12:1") == pytest.approx(1 / 12)  # run:rise -> inverted
    assert parse_slope_label("no slope here") is None
    assert parse_slope_label('1/4" = 1\'-0"') is None  # a drawing scale, not a slope


# ------------------------------------------------------------------ snapping

def test_snap_box_to_nearby_segments():
    primitives = [
        Primitive(p0=(100.0, 100.0), p1=(136.0, 100.0)),  # door leaf, 36 pt long
        Primitive(p0=(100.0, 100.0), p1=(100.0, 90.0)),
        Primitive(p0=(500.0, 500.0), p1=(600.0, 500.0)),  # far away wall
    ]
    vlm_box = (95.0, 85.0, 140.0, 105.0)
    snapped, was_snapped = snap_box(vlm_box, primitives)
    assert was_snapped
    # union of the two nearby segments only
    assert snapped == (100.0, 90.0, 136.0, 100.0)


def test_snap_box_no_nearby_primitives():
    primitives = [Primitive(p0=(500.0, 500.0), p1=(600.0, 500.0))]
    vlm_box = (95.0, 85.0, 140.0, 105.0)
    snapped, was_snapped = snap_box(vlm_box, primitives)
    assert not was_snapped
    assert snapped == vlm_box


def _room_walls():
    """Four long axis-aligned wall runs forming a room 96..304 × 96..204."""
    return [
        Primitive(p0=(96.0, 96.0), p1=(96.0, 204.0)),  # left wall
        Primitive(p0=(304.0, 96.0), p1=(304.0, 204.0)),  # right wall
        Primitive(p0=(96.0, 96.0), p1=(304.0, 96.0)),  # top wall
        Primitive(p0=(96.0, 204.0), p1=(304.0, 204.0)),  # bottom wall
    ]


def test_snap_room_box_snaps_to_enclosing_walls():
    vlm_box = (100.0, 100.0, 300.0, 200.0)  # a few points inside the walls
    box, snapped = snap_room_box(vlm_box, _room_walls())
    assert snapped
    assert box == (96.0, 96.0, 304.0, 204.0)


def test_snap_room_box_ignores_interior_clutter():
    # The regression: a room-sized box with ONLY short interior segments (a door
    # leaf, a fixture) must NOT snap to that clutter — it falls back to the
    # wall-to-wall VLM box, not the tiny clutter union the opening snap produced.
    clutter = [
        Primitive(p0=(150.0, 150.0), p1=(180.0, 150.0)),  # 30 pt
        Primitive(p0=(150.0, 150.0), p1=(150.0, 170.0)),  # 20 pt
    ]
    vlm_box = (100.0, 100.0, 300.0, 200.0)
    box, snapped = snap_room_box(vlm_box, clutter)
    assert not snapped
    assert box == vlm_box


def test_snap_room_box_requires_two_walls():
    only_left = [Primitive(p0=(96.0, 96.0), p1=(96.0, 204.0))]
    vlm_box = (100.0, 100.0, 300.0, 200.0)
    box, snapped = snap_room_box(vlm_box, only_left)
    assert not snapped
    assert box == vlm_box


def test_snap_room_box_no_walls_keeps_vlm_box():
    vlm_box = (100.0, 100.0, 300.0, 200.0)
    box, snapped = snap_room_box(vlm_box, [])
    assert not snapped
    assert box == vlm_box


def _double_wall_room():
    """A room whose walls are drawn as double lines. Interior faces at
    100/300 (x) and 100/200 (y); outer faces 8 pt further out. The top inner
    face is BROKEN at the centre by an opening (a window) — two segments that
    end at the side walls but leave a gap across the middle."""
    return [
        # left wall: outer + inner verticals
        Primitive(p0=(92.0, 92.0), p1=(92.0, 208.0)),
        Primitive(p0=(100.0, 100.0), p1=(100.0, 200.0)),
        # right wall
        Primitive(p0=(308.0, 92.0), p1=(308.0, 208.0)),
        Primitive(p0=(300.0, 100.0), p1=(300.0, 200.0)),
        # top wall: continuous OUTER line, and an INNER line broken at the centre
        Primitive(p0=(92.0, 92.0), p1=(308.0, 92.0)),
        Primitive(p0=(100.0, 100.0), p1=(180.0, 100.0)),  # inner, left of the opening
        Primitive(p0=(220.0, 100.0), p1=(300.0, 100.0)),  # inner, right of the opening
        # bottom wall: outer + (continuous) inner
        Primitive(p0=(92.0, 208.0), p1=(308.0, 208.0)),
        Primitive(p0=(100.0, 200.0), p1=(300.0, 200.0)),
    ]


def test_snap_room_box_takes_inner_face_of_double_wall():
    # The VLM box is biased UP — its top sits nearer the outer top line (92) than
    # the inner one (100). Because the inner line is broken by the opening it can't
    # be reached by spanning the centre, so the naive nearest picks the outer line.
    # Corner refinement must pull the top down onto the inner face at y=100.
    vlm_box = (110.0, 94.0, 290.0, 196.0)
    box, snapped = snap_room_box(vlm_box, _double_wall_room())
    assert snapped
    assert box == (100.0, 100.0, 300.0, 200.0)  # every edge on the interior face


def _room_with_center_doorway():
    """Single-line walls, but the top wall is broken by a wide central doorway —
    two wall segments that leave a gap across the middle, so no top segment crosses
    the room's centre though together they cover most of the width."""
    return [
        Primitive(p0=(96.0, 96.0), p1=(96.0, 204.0)),    # left
        Primitive(p0=(304.0, 96.0), p1=(304.0, 204.0)),  # right
        Primitive(p0=(96.0, 96.0), p1=(170.0, 96.0)),    # top, left of the doorway
        Primitive(p0=(230.0, 96.0), p1=(304.0, 96.0)),   # top, right of the doorway
        Primitive(p0=(96.0, 204.0), p1=(304.0, 204.0)),  # bottom
    ]


def test_snap_room_box_wall_broken_at_centre_still_snaps():
    # The room's centre (x=200) lands in the top-wall doorway, so no top segment
    # crosses it; coverage over the room's extent (not a centre-crossing test) must
    # still recover the top wall at y=96. This is the AF_Raster failure mode.
    vlm_box = (100.0, 100.0, 300.0, 200.0)
    box, snapped = snap_room_box(vlm_box, _room_with_center_doorway())
    assert snapped
    assert box == (96.0, 96.0, 304.0, 204.0)


def test_snap_box_ignores_leader_touching_text():
    # A door leaf + a callout leader whose far end sits under its bubble text.
    # Without the text filter the leader would stretch the box to x=160.
    primitives = [
        Primitive(p0=(100.0, 100.0), p1=(136.0, 100.0)),  # door leaf
        Primitive(p0=(100.0, 100.0), p1=(100.0, 90.0)),  # jamb
        Primitive(p0=(130.0, 102.0), p1=(160.0, 102.0)),  # leader to a callout
    ]
    labels = [TextLabel(text="7", bbox=(156.0, 98.0, 164.0, 106.0))]  # bubble text
    vlm_box = (95.0, 85.0, 150.0, 110.0)
    snapped, was = snap_box(vlm_box, primitives, labels)
    assert was
    assert snapped == (100.0, 90.0, 136.0, 100.0)  # leader excluded


def test_snap_room_box_ignores_label_line_as_wall():
    # Three real walls plus a long horizontal label underline near the open
    # bottom edge. The underline must not be taken as the bottom wall.
    walls = [
        Primitive(p0=(96.0, 96.0), p1=(96.0, 204.0)),  # left
        Primitive(p0=(304.0, 96.0), p1=(304.0, 204.0)),  # right
        Primitive(p0=(96.0, 96.0), p1=(304.0, 96.0)),  # top
    ]
    label_line = Primitive(p0=(120.0, 210.0), p1=(280.0, 210.0))  # 10pt below edge
    labels = [TextLabel(text="NOTE", bbox=(116.0, 206.0, 140.0, 214.0))]
    vlm_box = (100.0, 100.0, 300.0, 200.0)
    box, snapped = snap_room_box(vlm_box, walls + [label_line], labels)
    assert snapped
    assert box == (96.0, 96.0, 304.0, 200.0)  # bottom stays at the VLM edge


# --------------------------------------------------------------- measurement

def test_measure_from_label():
    """A dimension label near the box wins over geometry."""
    labels = [TextLabel(text='30"', bbox=(140.0, 95.0, 155.0, 105.0))]
    m = measure_asset(
        asset_box=(100.0, 90.0, 136.0, 100.0),
        primitives=[],
        labels=labels,
        scale=48 / 72,
    )
    assert m is not None
    measurements, from_label = m
    assert from_label
    assert measurements[Parameter.CLEAR_WIDTH] == 30.0


def test_measure_far_label_ignored():
    labels = [TextLabel(text='30"', bbox=(400.0, 400.0, 415.0, 410.0))]
    m = measure_asset(
        asset_box=(100.0, 90.0, 136.0, 100.0),
        primitives=[],
        labels=labels,
        scale=48 / 72,
    )
    assert m is None


def test_measure_from_geometry():
    """Longest segment inside the box × scale. 54 pt at 1/4"=1'-0" (×48/72) = 36 in."""
    primitives = [
        Primitive(p0=(100.0, 100.0), p1=(154.0, 100.0)),  # 54 pt
        Primitive(p0=(100.0, 100.0), p1=(100.0, 110.0)),  # 10 pt
    ]
    m = measure_asset(
        asset_box=(95.0, 90.0, 160.0, 115.0),
        primitives=primitives,
        labels=[],
        scale=48 / 72,
    )
    assert m is not None
    measurements, from_label = m
    assert not from_label
    assert measurements[Parameter.CLEAR_WIDTH] == pytest.approx(36.0)


def test_measure_ignores_curve_chord_for_clear_width():
    """A door's swing-arc chord (√2× the leaf) must not be taken as the clear
    width — the straight leaf is the opening width. This is the 36"→51" bug."""
    leaf = Primitive(p0=(100.0, 100.0), p1=(136.0, 100.0))  # 36 pt straight leaf
    swing = Primitive(p0=(100.0, 100.0), p1=(136.0, 136.0), is_curve=True)  # 36√2 ≈ 51 pt
    m = measure_asset(
        asset_box=(95.0, 90.0, 145.0, 145.0),
        primitives=[leaf, swing],
        labels=[],
        scale=1.0,  # 1 real inch per point, to read pts as inches
    )
    assert m is not None
    measurements, from_label = m
    assert not from_label
    assert measurements[Parameter.CLEAR_WIDTH] == pytest.approx(36.0)  # leaf, not the 51 chord


def test_measure_no_scale_no_label():
    """Without a scale, geometry cannot produce real-world inches."""
    primitives = [Primitive(p0=(100.0, 100.0), p1=(154.0, 100.0))]
    m = measure_asset(
        asset_box=(95.0, 90.0, 160.0, 115.0),
        primitives=primitives,
        labels=[],
        scale=None,
    )
    assert m is None


# ------------------------------------------------------- pdf type detection

def _vector_page(doc):
    page = doc.new_page(width=612, height=792)
    for i in range(5):
        page.draw_line((50, 50 + i * 10), (500, 50 + i * 10))
    return page


def test_detect_vector_pdf():
    doc = pymupdf.open()
    page = _vector_page(doc)
    assert detect_pdf_type(page) == "vector"


def test_detect_raster_pdf():
    # A page with no vector drawings (as a scan would be: just an image)
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    assert detect_pdf_type(page) == "raster"


def test_extract_primitives_and_labels():
    doc = pymupdf.open()
    page = _vector_page(doc)
    page.insert_text((100, 200), 'SCALE: 1/4" = 1\'-0"', fontsize=10)
    primitives, labels = extract_primitives(page)
    assert len(primitives) >= 5
    assert any("SCALE" in label.text for label in labels)


def test_build_wall_runs_groups_collinear_and_merges_intervals():
    from planlint.ingest.vector_geometry import build_wall_runs
    prims = [
        Primitive(p0=(100.0, 100.0), p1=(160.0, 100.0)),  # horizontal wall @ y=100
        Primitive(p0=(155.0, 100.0), p1=(220.0, 100.0)),  # overlaps -> merges
        Primitive(p0=(300.0, 100.0), p1=(360.0, 100.0)),  # separate run segment @ y=100
        Primitive(p0=(100.0, 100.0), p1=(100.0, 180.0)),  # vertical wall @ x=100
        Primitive(p0=(120.0, 120.0), p1=(125.0, 120.0)),  # too short -> ignored
        Primitive(p0=(100.0, 100.0), p1=(140.0, 140.0), is_curve=True),  # curve -> ignored
    ]
    runs = build_wall_runs(prims)
    horiz = [r for r in runs if r.orient == "h"]
    assert len(horiz) == 1
    assert horiz[0].offset == 100.0
    assert sorted(horiz[0].intervals) == [(100.0, 220.0), (300.0, 360.0)]
    assert any(r.orient == "v" and r.offset == 100.0 for r in runs)


def test_gap_in_run_finds_flanked_opening():
    from planlint.ingest.vector_geometry import WallRun, _gap_in_run
    # wall present on [100,150] and [186,260]; gap [150,186] = 36pt opening
    run = WallRun(orient="h", offset=100.0, intervals=((100.0, 150.0), (186.0, 260.0)))
    assert _gap_in_run(run, span_lo=150.0, span_hi=186.0) == (150.0, 186.0)
    # a solid run (one interval) has no interior gap
    solid = WallRun(orient="h", offset=100.0, intervals=((100.0, 260.0),))
    assert _gap_in_run(solid, span_lo=150.0, span_hi=186.0) is None
    # a gap too wide to be one opening is rejected
    wide = WallRun(orient="h", offset=100.0, intervals=((100.0, 150.0), (500.0, 560.0)))
    assert _gap_in_run(wide, span_lo=150.0, span_hi=500.0) is None


def test_gap_is_occupied_detects_hatched_solid():
    from planlint.ingest.vector_geometry import _gap_is_occupied
    box = (150.0, 90.0, 186.0, 120.0)
    # a window: two thin glazing lines PARALLEL to the wall (horizontal) — not occupied
    glazing = [
        Primitive(p0=(150.0, 102.0), p1=(186.0, 102.0)),
        Primitive(p0=(150.0, 108.0), p1=(186.0, 108.0)),
    ]
    assert _gap_is_occupied(box, glazing) is False
    # a chimney: dense short hatching (brick) inside the gap — occupied
    hatch = [
        Primitive(p0=(150.0 + i * 3, 92.0), p1=(156.0 + i * 3, 118.0))
        for i in range(8)
    ]
    assert _gap_is_occupied(box, hatch) is True


def test_classify_opening_vector_snapped_refuted_unknown():
    from planlint.ingest.vector_geometry import classify_opening_vector
    # SNAPPED: horizontal wall @ y=100 broken [150,186]; glazing lines in the gap
    window = [
        Primitive(p0=(60.0, 100.0), p1=(150.0, 100.0)),
        Primitive(p0=(186.0, 100.0), p1=(280.0, 100.0)),
        Primitive(p0=(150.0, 103.0), p1=(186.0, 103.0)),
    ]
    res = classify_opening_vector((150.0, 90.0, 186.0, 110.0), window)
    assert res.kind == "snapped"
    assert res.width_pt == pytest.approx(36.0)
    assert res.bbox[0] == pytest.approx(150.0) and res.bbox[2] == pytest.approx(186.0)

    # REFUTED (chimney breaking the wall): gap in the wall line but hatching fills it
    chimney_gap = [
        Primitive(p0=(60.0, 100.0), p1=(150.0, 100.0)),
        Primitive(p0=(186.0, 100.0), p1=(280.0, 100.0)),
    ] + [Primitive(p0=(150.0 + i * 3, 92.0), p1=(156.0 + i * 3, 118.0)) for i in range(8)]
    assert classify_opening_vector((150.0, 90.0, 186.0, 120.0), chimney_gap).kind == "refuted"

    # REFUTED (chimney on a continuous wall): no gap, but hatching fills the box
    chimney_solid = [Primitive(p0=(60.0, 100.0), p1=(280.0, 100.0))] + [
        Primitive(p0=(150.0 + i * 3, 92.0), p1=(156.0 + i * 3, 118.0)) for i in range(8)
    ]
    assert classify_opening_vector((150.0, 90.0, 186.0, 120.0), chimney_solid).kind == "refuted"

    # UNKNOWN (un-cut wall, no hatching): a door drawn over a solid wall — kept for review
    solid = [Primitive(p0=(60.0, 100.0), p1=(280.0, 100.0))]
    assert classify_opening_vector((150.0, 90.0, 186.0, 110.0), solid).kind == "unknown"

    # UNKNOWN: no wall geometry at all
    assert classify_opening_vector((150.0, 90.0, 186.0, 110.0), []).kind == "unknown"


def test_classify_opening_rejects_oversize_gap():
    # A small door box (36pt) inside a huge wall gap (210pt) must NOT snap to the
    # whole gap — that is a wall section, not this opening.
    from planlint.ingest.vector_geometry import classify_opening_vector
    wall = [
        Primitive(p0=(60.0, 100.0), p1=(90.0, 100.0)),
        Primitive(p0=(300.0, 100.0), p1=(360.0, 100.0)),  # 210pt gap 90..300
    ]
    assert classify_opening_vector((150.0, 90.0, 186.0, 110.0), wall).kind == "unknown"


def test_classify_opening_scale_cap_rejects_large_real_opening():
    # A gap within the box-size slack but implausibly large in reality (scale
    # known) is rejected; the same gap with no scale snaps.
    from planlint.ingest.vector_geometry import classify_opening_vector
    wall = [
        Primitive(p0=(40.0, 100.0), p1=(100.0, 100.0)),
        Primitive(p0=(250.0, 100.0), p1=(320.0, 100.0)),  # 150pt gap 100..250
    ]
    box = (120.0, 90.0, 230.0, 110.0)  # 110pt wide → 150 <= 3×110 (slack passes)
    assert classify_opening_vector(box, wall).kind == "snapped"
    # scale 1.5 in/pt → 150pt = 225 in > _MAX_OPENING_IN (200) → rejected
    assert classify_opening_vector(box, wall, scale=1.5).kind == "unknown"


def test_classify_opening_thickness_from_paired_walls():
    # Two parallel wall faces 10pt apart, both cut by the opening: the snapped box
    # thickness is the wall thickness (10), not the tall VLM box (40).
    from planlint.ingest.vector_geometry import classify_opening_vector
    walls = [
        Primitive(p0=(60.0, 100.0), p1=(150.0, 100.0)),
        Primitive(p0=(186.0, 100.0), p1=(280.0, 100.0)),
        Primitive(p0=(60.0, 110.0), p1=(150.0, 110.0)),
        Primitive(p0=(186.0, 110.0), p1=(280.0, 110.0)),
    ]
    res = classify_opening_vector((150.0, 85.0, 186.0, 125.0), walls)
    assert res.kind == "snapped"
    assert res.bbox[3] - res.bbox[1] == pytest.approx(10.0)


def test_classify_opening_repositions_onto_single_nearby_gap():
    # A box sitting on solid wall beside ONE clear opening is repositioned onto it.
    from planlint.ingest.vector_geometry import classify_opening_vector
    one_gap = [
        Primitive(p0=(60.0, 100.0), p1=(200.0, 100.0)),
        Primitive(p0=(236.0, 100.0), p1=(340.0, 100.0)),  # gap 200..236
    ]
    res = classify_opening_vector((150.0, 90.0, 186.0, 110.0), one_gap)
    assert res.kind == "snapped"
    assert res.bbox[0] == pytest.approx(200.0) and res.bbox[2] == pytest.approx(236.0)

    # Two nearby gaps flanking the box → ambiguous → kept for review, not moved.
    two_gaps = [
        Primitive(p0=(60.0, 100.0), p1=(100.0, 100.0)),
        Primitive(p0=(136.0, 100.0), p1=(200.0, 100.0)),
        Primitive(p0=(236.0, 100.0), p1=(340.0, 100.0)),  # gaps 100..136 and 200..236
    ]
    assert classify_opening_vector((150.0, 90.0, 186.0, 110.0), two_gaps).kind == "unknown"
