"""Full visual + electrical audit after readability fixes."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from cir_diagram.render_single import (  # noqa: E402
    BOX_H,
    BOX_W,
    CHAR_W,
    LABEL_H,
    RAILS,
    label_width,
    boxes_overlap,
    render_single,
)
from cir_diagram.spice_parser import parse_cir_file  # noqa: E402


def main() -> int:
    circuit = parse_cir_file(ROOT / "CIR.cir")
    svg_path, stats = render_single(
        circuit, ROOT / "CIR_full.svg", max_per_row=80
    )
    svg = svg_path.read_text(encoding="utf-8")
    mos = [c for c in circuit.components if c.kind in {"m_n", "m_p"}]

    centres = {
        name: (float(x), float(y) + 2)
        for x, y, name in re.findall(
            r'<text x="([0-9.]+)" y="([0-9.]+)"[^>]*>(M\d+)</text>', svg
        )
    }
    labels = [
        (float(x), float(y), lab)
        for x, y, lab in re.findall(
            r'<text x="([0-9.]+)" y="([0-9.]+)" class="(?:nl|out|in)"[^>]*>([^<]+)</text>',
            svg,
        )
    ]
    segs = [
        tuple(map(float, m.groups()))
        for m in re.finditer(
            r'<line class="w" x1="([0-9.]+)" y1="([0-9.]+)" x2="([0-9.]+)" y2="([0-9.]+)"/>',
            svg,
        )
    ]
    junc = [
        (float(m.group(1)), float(m.group(2)))
        for m in re.finditer(r'<circle class="jn" cx="([0-9.]+)" cy="([0-9.]+)"', svg)
    ]

    # geometric label overlaps from SVG positions
    boxes = []
    geo_overlaps = 0
    for lx, ly, lab in labels:
        tw = label_width(lab)
        box = (lx, ly - LABEL_H + 2, lx + tw, ly + 2)
        if any(boxes_overlap(box, ob, pad=1.0) for ob in boxes):
            geo_overlaps += 1
        boxes.append(box)

    floating = 0
    for jx, jy in junc:
        horiz = any(
            abs(s[1] - s[3]) < 0.05
            and abs(s[1] - jy) < 0.8
            and min(s[0], s[2]) - 0.5 <= jx <= max(s[0], s[2]) + 0.5
            and abs(s[0] - s[2]) > 1
            for s in segs
        )
        vert = any(
            abs(s[0] - s[2]) < 0.05
            and abs(s[0] - jx) < 0.8
            and min(s[1], s[3]) - 0.5 <= jy <= max(s[1], s[3]) + 0.5
            and abs(s[1] - s[3]) > 1
            for s in segs
        )
        if not (horiz and vert):
            floating += 1

    signal = set()
    for m in mos:
        for net in m.nodes[:3]:
            if net not in RAILS:
                signal.add(net)
    labeled = {lab for _, _, lab in labels}
    unlabeled = sorted(signal - labeled)

    def on_wire(px, py, tol=2.0):
        for x1, y1, x2, y2 in segs:
            if abs(x1 - x2) < 0.05 and abs(x1 - px) <= tol:
                if min(y1, y2) - tol <= py <= max(y1, y2) + tol:
                    return True
            if abs(y1 - y2) < 0.05 and abs(y1 - py) <= tol:
                if min(x1, x2) - tol <= px <= max(x1, x2) + tol:
                    return True
        return False

    missing = []
    for m in mos:
        cx, cy = centres[m.name]
        for pin, net, px, py in (
            ("D", m.nodes[0], cx, cy + BOX_H / 2),
            ("G", m.nodes[1], cx - BOX_W / 2 - 10, cy),
            ("S", m.nodes[2], cx, cy - BOX_H / 2),
        ):
            if net in RAILS:
                continue
            if not on_wire(px, py):
                missing.append(f"{m.name}.{pin}")

    m3 = centres["M3"]
    int4_ok = "INT_4" in labeled and on_wire(m3[0], m3[1] + BOX_H / 2)

    print("=== FULL AUDIT ===")
    print(f"devices: {len(centres)}")
    print(f"labels: {len(labels)} / nets {len(signal)}")
    print(f"unlabeled: {len(unlabeled)}")
    print(f"missing pins: {len(missing)} (renderer {stats['missing_pins']})")
    print(f"split nets: {stats['split_nets']}")
    print(f"renderer label_overlaps: {stats.get('label_overlaps')}")
    print(f"svg label overlaps: {geo_overlaps}")
    print(f"dangling H stubs: {stats.get('dangling_stubs')}")
    print(f"floating junctions (no V): {floating}")
    print(f"INT_4/M3 ok: {int4_ok}")

    bad = (
        len(centres) != 326
        or unlabeled
        or missing
        or stats["missing_pins"]
        or stats["split_nets"]
        or stats.get("label_overlaps", 0) > 0
        or geo_overlaps > 0
        or stats.get("dangling_stubs", 0) > 0
        or not int4_ok
    )
    print("RESULT:", "PASS" if not bad else "FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
