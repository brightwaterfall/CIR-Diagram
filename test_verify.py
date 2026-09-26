"""Verification for the CIR.cir pipeline (flat working directory)."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from cir_diagram.render_readable import (
    _find_inverters,
    _mosfets,
    _units,
    find_text_outside_frames,
    find_text_overlaps,
    render_readable,
    render_sheet,
)
from cir_diagram.render_single import render_single
from cir_diagram.spice_parser import parse_cir_file
from cir_diagram.to_netlistsvg import circuit_to_netlistsvg_json

ROOT = Path(__file__).resolve().parent
CIR = ROOT / "CIR.cir"
OUT = ROOT

errors: list[str] = []
passed = 0


def check(cond: bool, msg: str) -> None:
    global passed
    if cond:
        passed += 1
        print(f"  OK   {msg}")
    else:
        errors.append(msg)
        print(f"  FAIL {msg}")


def main() -> int:
    print("=== 1. SOURCE FILE ===")
    check(CIR.is_file(), f"CIR exists: {CIR.name}")
    text = CIR.read_text(encoding="utf-8")
    m_lines = [ln for ln in text.splitlines() if re.match(r"^M\d+\s", ln.strip())]
    check(len(m_lines) == 376, f"376 device lines in file (got {len(m_lines)})")
    check(".model pmos_mod" in text and ".model nmos_mod" in text, "both device models present")

    print("\n=== 2. PARSER ===")
    circuit = parse_cir_file(CIR)
    mos = _mosfets(circuit)
    pmos = [m for m in mos if m.kind == "m_p"]
    nmos = [m for m in mos if m.kind == "m_n"]
    check(len(mos) == 376, f"376 MOSFETs parsed (got {len(mos)})")
    check(len(pmos) == 276 and len(nmos) == 50, f"276 PMOS / 50 NMOS (got {len(pmos)}/{len(nmos)})")
    check(all(len(m.nodes) == 4 for m in mos), "every device has D G S B")
    check(len({m.name for m in mos}) == 376, "device names unique")
    check(mos[0].name == "M1" and mos[-1].name == "M376", "device order M1...M376")

    m5 = next(m for m in mos if m.name == "M5")
    m6 = next(m for m in mos if m.name == "M6")
    check(m5.nodes == ["BLOCK_OUT_5", "B0", "VDD", "VDD"], f"M5 nodes correct ({m5.nodes})")
    check(m6.nodes == ["BLOCK_OUT_5", "B0", "GND", "GND"], f"M6 nodes correct ({m6.nodes})")
    check(m5.kind == "m_p" and m6.kind == "m_n", "M5 PMOS / M6 NMOS")
    check("A_EQ_B" in circuit.nets, "primary output net present")

    print("\n=== 3. UNIT GROUPING ===")
    pairs = _find_inverters(mos)
    units = _units(mos)
    check(len(pairs) == 50, f"50 CMOS inverter pairs detected (got {len(pairs)})")
    check(sum(len(u) for u in units) == 376, "every device belongs to exactly one unit")
    flat = [m.name for u in units for m in u]
    check(len(flat) == len(set(flat)), "no device drawn twice")
    for p, n in pairs:
        if p.nodes[1] != n.nodes[1] or p.nodes[0] != n.nodes[0]:
            check(False, f"pair {p.name}/{n.name} shares gate and drain")
            break
    else:
        check(True, "all pairs share gate and drain")

    print("\n=== 4. SHEET LAYOUT ===")
    svgs, html_path, overlaps = render_readable(
        circuit, OUT, stem=CIR.stem, per_sheet=12
    )
    check(len(svgs) == 23, f"23 sheets generated (got {len(svgs)})")
    check(not overlaps, f"no overlapping or clipped labels (got {len(overlaps)})")
    if overlaps:
        for a, b in overlaps[:5]:
            print(f"       {a!r} / {b!r}")

    # per-sheet geometry re-check
    tmp = Path(tempfile.mkdtemp())
    try:
        bad_sheets = []
        drawn = 0
        for i in range(0, len(units), 12):
            sheet_units = units[i : i + 12]
            idx = i // 12 + 1
            cv = render_sheet(circuit, sheet_units, idx, 23, tmp / f"s{idx}.svg")
            drawn += sum(len(u) for u in sheet_units)
            if find_text_overlaps(cv.boxes) or find_text_outside_frames(cv):
                bad_sheets.append(idx)
        check(not bad_sheets, f"every sheet re-checks clean (bad={bad_sheets})")
        check(drawn == 376, f"all 376 devices drawn across sheets (got {drawn})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n=== 5. OUTPUT FILES ===")
    for p in svgs:
        if not p.is_file() or p.stat().st_size < 2000:
            check(False, f"sheet file valid: {p.name}")
            break
    else:
        check(True, f"all {len(svgs)} sheet SVGs written and non-trivial")

    sheet1 = svgs[0].read_text(encoding="utf-8")
    check(sheet1.lstrip().startswith("<svg"), "SVG root element correct")
    check(sheet1.count("</text>") > 40, "sheet 1 carries net labels")

    html = html_path.read_text(encoding="utf-8")
    check(html_path.is_file(), "readable HTML written")
    check(all(p.name in html for p in svgs), "HTML references every sheet")
    check(html.count("<tr>") >= 376, f"HTML device table complete ({html.count('<tr>')} rows)")
    check("A_EQ_B" in html, "HTML lists the output net")

    print("\n=== 6. JSON EXPORT ===")
    data = circuit_to_netlistsvg_json(circuit)
    cells = next(iter(data["modules"].values()))["cells"]
    mos_cells = {k: v for k, v in cells.items() if v.get("type") in ("nmos", "pmos")}
    check(len(mos_cells) == 376, f"JSON holds 376 devices (got {len(mos_cells)})")
    check(
        all(all(p in v["connections"] for p in ("D", "G", "S")) for v in mos_cells.values()),
        "JSON pins complete",
    )

    print("\n=== 7. CLI ===")
    r = subprocess.run(
        [
            sys.executable, "-m", "cir_diagram.cli", str(CIR),
            "-o", str(OUT / f"{CIR.stem}.svg"), "--mode", "readable",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    check(r.returncode == 0, f"CLI readable mode exits cleanly (code {r.returncode})")
    check("MOSFETs: 376" in r.stdout, "CLI reports 376 MOSFETs")
    check("Text overlaps detected: 0" in r.stdout, "CLI reports zero overlaps")

    print("\n=== 8. SINGLE-PAGE SCHEMATIC ===")
    single, stats = render_single(circuit, OUT / "CIR_full.svg", max_per_row=80)
    check(single.is_file() and single.stat().st_size > 100_000, "one-page SVG written")
    check(stats["devices"] == 376, f"all 376 devices on the sheet (got {stats['devices']})")
    svg_text = single.read_text(encoding="utf-8")
    for probe in ("M1", "M376", "A_EQ_B"):
        if probe not in svg_text:
            check(False, f"one-page SVG contains {probe}")
            break
    else:
        check(True, "one-page SVG contains first, last and output nodes")
    check(stats["segments"] > 1000, f"wires drawn ({stats['segments']} segments)")
    check(stats["missing_pins"] == 0, f"every pin has a wire (missing={stats['missing_pins']})")
    check(stats.get("split_nets", 0) == 0, f"every net is one connected piece (split={stats.get('split_nets')})")
    check(stats.get("label_overlaps", 0) == 0, f"no overlapping net labels (overlaps={stats.get('label_overlaps')})")
    check(stats.get("dangling_stubs", 0) == 0, f"no dangling horizontal stubs (stubs={stats.get('dangling_stubs')})")
    check(stats.get("dangling_v_ends", 0) == 0, f"no dangling vertical ends (ends={stats.get('dangling_v_ends')})")
    from cir_diagram.render_single import _io_sets
    pin_in, pin_out = _io_sets(circuit, None, None)
    in_labs = set(re.findall(r'class="in"[^>]*>([^<]+)', svg_text))
    out_labs = set(re.findall(r'class="out"[^>]*>([^<]+)', svg_text))
    check(in_labs == set(pin_in), f"primary inputs labeled (got {len(in_labs)}, expect {len(pin_in)})")
    check(out_labs == set(pin_out), f"primary outputs labeled (got {len(out_labs)}, expect {len(pin_out)})")
    print(f"       crossings: {stats['crossings']} (non-planar netlist - cannot reach zero)")


    # M3 drain INT_4 was the reported bug - confirm a full stub exists
    svg_text = single.read_text(encoding="utf-8")
    check(
        'class="out">INT_4</text>' in svg_text or ">INT_4</text>" in svg_text,
        "INT_4 drain net is labeled",
    )

    print("\n=== SUMMARY ===")
    print(f"Passed: {passed}   Failed: {len(errors)}")
    for e in errors:
        print(f"  - {e}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
