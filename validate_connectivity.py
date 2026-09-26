"""Validate every signal net is one connected wire tree (T-junction aware)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from cir_diagram.render_single import render_single  # noqa: E402
from cir_diagram.spice_parser import parse_cir_file  # noqa: E402


def main() -> int:
    circuit = parse_cir_file(ROOT / "CIR.cir")
    svg_path, stats = render_single(
        circuit, ROOT / "CIR_full.svg", max_per_row=80
    )
    svg = svg_path.read_text(encoding="utf-8")

    all_segs = [
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

    # Filled junctions must be true T-junctions (H + V).
    # Output open-circles use a different element (no class="jn").
    floating = 0
    for jx, jy in junc:
        horiz = any(
            abs(s[1] - s[3]) < 0.05
            and abs(s[1] - jy) < 0.8
            and min(s[0], s[2]) - 0.5 <= jx <= max(s[0], s[2]) + 0.5
            and abs(s[0] - s[2]) > 1
            for s in all_segs
        )
        vert = any(
            abs(s[0] - s[2]) < 0.05
            and abs(s[0] - jx) < 0.8
            and min(s[1], s[3]) - 0.5 <= jy <= max(s[1], s[3]) + 0.5
            and abs(s[1] - s[3]) > 1
            for s in all_segs
        )
        if not (horiz and vert):
            floating += 1

    zero = sum(1 for s in all_segs if abs(s[0] - s[2]) < 0.05 and abs(s[1] - s[3]) < 0.05)
    devices = len(re.findall(r">M\d+</text>", svg))

    print(f"devices in SVG: {devices}")
    print(f"wire segments: {len(all_segs)}")
    print(f"junctions: {len(junc)}")
    print(f"floating junctions: {floating}")
    print(f"zero-length wires: {zero}")
    print(f"renderer missing_pins: {stats['missing_pins']}")
    print(f"renderer split_nets: {stats['split_nets']}")
    print(f"label_overlaps: {stats.get('label_overlaps')}")
    print(f"dangling_stubs: {stats.get('dangling_stubs')}")
    if stats.get("split_samples"):
        print("  samples:", ", ".join(stats["split_samples"]))

    bad = (
        floating
        or zero
        or stats["missing_pins"]
        or stats["split_nets"]
        or stats.get("label_overlaps", 0)
        or stats.get("dangling_stubs", 0)
        or devices != 326
    )
    print("RESULT:", "PASS" if not bad else "FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
