"""
CLI: SPICE .CIR -> circuit diagram SVG (+ optional PNG).

  py -m cir_diagram.cli CIR.cir
  py -m cir_diagram.cli CIR.cir -o CIR_full.svg

Outputs are written in the working directory (no output/ folder, no zip).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .render_readable import render_readable
from .render_single import render_single
from .render_svg import render_html_report, render_svg, write_stats
from .render_wired import render_wired
from .spice_parser import parse_cir_file
from .to_netlistsvg import write_netlist_json

# Repo / working-directory root (parent of cir_diagram/)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_png(svg_path: Path, png_path: Path) -> bool:
    """Convert SVG to PNG. Returns False if optional PNG deps are missing or fail.

    SVG generation never depends on this. Install: pip install rlPyCairo
    (or the older reportlab renderPM binary) if you want a PNG preview.
    """
    try:
        from reportlab.graphics import renderPM
        from svglib.svglib import svg2rlg
    except ImportError:
        return False
    try:
        drawing = svg2rlg(str(svg_path))
        if drawing is None:
            return False
        renderPM.drawToFile(drawing, str(png_path), fmt="PNG", dpi=72)
        return png_path.is_file()
    except Exception as exc:  # noqa: BLE001 - PNG is optional; keep SVG deliverable
        print(f"  PNG skipped ({type(exc).__name__}: {exc})")
        print("  Tip: pip install rlPyCairo   then re-run for a PNG preview")
        return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Draw circuit diagram from SPICE .CIR")
    p.add_argument("cir", type=Path, help="Input .CIR file")
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output SVG path (default: <cir_stem>_full.svg in the working directory)",
    )
    p.add_argument(
        "--mode",
        choices=("single", "wired", "readable", "graph"),
        default="single",
        help="single = whole circuit wired on one sheet (default); "
        "wired = channel-routed multi-sheet; readable = labeled cards; "
        "graph = force-directed connectivity",
    )
    p.add_argument(
        "--max-per-row",
        type=int,
        default=28,
        help="Single mode: devices per row before wrapping (default 28)",
    )
    p.add_argument(
        "--per-sheet",
        type=int,
        default=20,
        help="Devices per sheet for wired/readable modes (default 20)",
    )
    p.add_argument(
        "--per-row",
        type=int,
        default=10,
        help="Devices per row in wired mode (default 10)",
    )
    p.add_argument(
        "--show-rails",
        action="store_true",
        help="Graph mode: connect every device to VDD/GND hubs",
    )
    p.add_argument("--json", type=Path, default=None, help="Also write netlistsvg JSON")
    args = p.parse_args(argv)

    if not args.cir.is_file():
        print(f"File not found: {args.cir}", file=sys.stderr)
        return 1

    # Flat working directory: SVG/PNG/JSON next to the CIR (or cwd), never output/
    out_svg = args.output or Path(f"{args.cir.stem}_full.svg")
    if not out_svg.is_absolute():
        out_svg = Path.cwd() / out_svg
    out_dir = out_svg.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    circuit = parse_cir_file(args.cir)
    mos = sum(1 for c in circuit.components if c.kind in {"m_n", "m_p"})
    print(f"Parsed: {circuit.title}")
    print(f"  MOSFETs: {mos}  nets: {len(circuit.nets)}")

    json_path = args.json or out_dir / f"{args.cir.stem}.json"
    write_netlist_json(circuit, json_path)
    print(f"  JSON: {json_path}")

    if args.mode == "single":
        svg, stats = render_single(circuit, out_svg, max_per_row=args.max_per_row)
        print(f"  SVG:   {svg}")
        print(
            f"  Layout: {stats['rows']} rows | {stats['nets']} signal nets | "
            f"{stats['segments']} wire segments"
        )
        print(f"  Wire crossings: {stats['crossings']}")
        print(f"  Missing pin wires: {stats['missing_pins']}")
        print(f"  Split nets (disconnected): {stats.get('split_nets', '?')}")
        print(f"  Label overlaps: {stats.get('label_overlaps', '?')}")
        print(f"  Dangling H stubs: {stats.get('dangling_stubs', '?')}")
        print(f"  Dangling V ends: {stats.get('dangling_v_ends', '?')}")
        for sample in stats.get("dangling_v_samples", [])[:8]:
            print(f"    - dangling V: {sample}")
        for sample in stats.get("split_samples", [])[:8]:
            print(f"    - split: {sample}")
        for sample in stats.get("missing_pin_samples", []):
            print(f"    - {sample}")
        png = svg.with_suffix(".png")
        if _write_png(svg, png):
            print(f"  PNG:   {png}")
        print(f"  Deliverable: {svg}" + (f" + {png.name}" if png.is_file() else ""))
    elif args.mode == "wired":
        svgs, html = render_wired(
            circuit,
            out_dir,
            stem=args.cir.stem,
            per_sheet=args.per_sheet,
            per_row=args.per_row,
        )
        print(f"  Sheets: {len(svgs)} (first: {svgs[0].name})")
        print(f"  HTML:  {html}")
    elif args.mode == "readable":
        svgs, html, overlaps = render_readable(
            circuit, out_dir, stem=args.cir.stem, per_sheet=min(args.per_sheet, 12)
        )
        print(f"  Sheets: {len(svgs)} (first: {svgs[0].name}, last: {svgs[-1].name})")
        print(f"  HTML:  {html}")
        print(f"  Text overlaps detected: {len(overlaps)}")
        for a, b in overlaps[:5]:
            print(f"    overlap: {a!r} / {b!r}")
    else:
        render_svg(circuit, out_svg, hide_rails=not args.show_rails)
        print(f"  SVG:  {out_svg}")
        out_html = out_svg.with_suffix(".html")
        render_html_report(circuit, out_svg, out_html)
        print(f"  HTML: {out_html}")

    stats_path = out_dir / f"{args.cir.stem}_stats.txt"
    write_stats(circuit, stats_path)
    print(f"  Stats: {stats_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
