"""
Wired schematic with channel routing - real connections, no wire crossings.

Layout:
  - MOSFETs placed left->right in rows (ordered by device number)
  - VDD rail above each row, GND rail below
  - Signal nets routed in channels *between* rows using the classic
    left-edge channel router: one horizontal track per net, vertical
    stubs only - horizontals never cross, verticals never cross
  - Large circuits are split into sheets of N devices so each page stays readable
"""

from __future__ import annotations

import html
import re
from collections import defaultdict
from pathlib import Path

from .spice_parser import Circuit, Component

RAILS = {"0", "GND", "VDD", "VSS", "VCC"}
SUPPLY_HIGH = {"VDD", "VCC"}
SUPPLY_LOW = {"GND", "VSS", "0"}


def _mosfets(circuit: Circuit) -> list[Component]:
    return [c for c in circuit.components if c.kind in {"m_n", "m_p"}]


def _num(name: str) -> int:
    m = re.search(r"(\d+)", name)
    return int(m.group(1)) if m else 0


# -- geometry constants ----------------------------------------------
DEV_W = 92.0          # horizontal pitch between transistor centres
SYM_H = 56.0          # MOSFET symbol height
TRACK_H = 18.0        # vertical pitch between channel tracks
ROW_GAP = 36.0        # gap between channel bottom and next VDD rail
MARGIN = 48.0
HEADER = 90.0


def _pin_x(dev_index: int, pin: str, row_x0: float) -> float:
    """X of a pin on a MOSFET drawn at column `dev_index`."""
    cx = row_x0 + dev_index * DEV_W
    if pin == "G":
        return cx - 22
    if pin == "D":
        return cx
    # S
    return cx


def _pin_side(pin: str) -> str:
    """Which face of the symbol the pin exits."""
    if pin == "G":
        return "left"
    if pin == "D":
        return "top"
    return "bottom"


def left_edge_route(nets: dict[str, list[float]]) -> dict[str, int]:
    """
    Assign each net a track index so horizontal spans do not overlap
    on the same track (left-edge channel routing -> zero crossings).
    `nets[name] = sorted list of pin x-coordinates`.
    """
    # (left, right, name)
    intervals = []
    for name, xs in nets.items():
        if len(xs) < 2:
            continue  # dangling single pin - no wire needed in channel
        intervals.append((min(xs), max(xs), name))
    intervals.sort(key=lambda t: (t[0], t[1]))

    # track_end[i] = rightmost x already using track i
    track_end: list[float] = []
    assign: dict[str, int] = {}
    for left, right, name in intervals:
        placed = False
        for i, end in enumerate(track_end):
            if left >= end + 4:  # small gap so wires don't touch
                track_end[i] = right
                assign[name] = i
                placed = True
                break
        if not placed:
            assign[name] = len(track_end)
            track_end.append(right)
    return assign


def _draw_mos(parts: list[str], cx: float, cy: float, m: Component) -> dict[str, tuple[float, float]]:
    """Draw MOSFET; return pin positions {G,D,S} -> (x,y)."""
    is_p = m.kind == "m_p"
    color = "#b26a00" if is_p else "#1a5f9e"
    half = SYM_H / 2

    # body outline (light)
    parts.append(
        f'<rect x="{cx-30:.1f}" y="{cy-half:.1f}" width="60" height="{SYM_H:.1f}" '
        f'rx="5" fill="{"#fff8ef" if is_p else "#eef5ff"}" stroke="{color}" stroke-width="1.2"/>'
    )

    gate_x = cx - 22
    chan_x = cx - 12
    top_y = cy - half
    bot_y = cy + half

    # gate
    parts.append(f'<line x1="{gate_x-10:.1f}" y1="{cy:.1f}" x2="{gate_x:.1f}" y2="{cy:.1f}" stroke="{color}" stroke-width="1.5"/>')
    parts.append(f'<line x1="{gate_x:.1f}" y1="{cy-14:.1f}" x2="{gate_x:.1f}" y2="{cy+14:.1f}" stroke="{color}" stroke-width="2"/>')
    if is_p:
        parts.append(f'<circle cx="{gate_x-7:.1f}" cy="{cy:.1f}" r="3" fill="#fff" stroke="{color}" stroke-width="1.4"/>')

    # channel + D/S
    parts.append(f'<line x1="{chan_x:.1f}" y1="{cy-14:.1f}" x2="{chan_x:.1f}" y2="{cy+14:.1f}" stroke="{color}" stroke-width="2.4"/>')
    parts.append(f'<line x1="{chan_x:.1f}" y1="{cy-11:.1f}" x2="{cx:.1f}" y2="{cy-11:.1f}" stroke="{color}" stroke-width="1.4"/>')
    parts.append(f'<line x1="{cx:.1f}" y1="{cy-11:.1f}" x2="{cx:.1f}" y2="{top_y:.1f}" stroke="{color}" stroke-width="1.4"/>')
    parts.append(f'<line x1="{chan_x:.1f}" y1="{cy+11:.1f}" x2="{cx:.1f}" y2="{cy+11:.1f}" stroke="{color}" stroke-width="1.4"/>')
    parts.append(f'<line x1="{cx:.1f}" y1="{cy+11:.1f}" x2="{cx:.1f}" y2="{bot_y:.1f}" stroke="{color}" stroke-width="1.4"/>')

    # name under symbol
    parts.append(
        f'<text x="{cx:.1f}" y="{bot_y + 14:.1f}" text-anchor="middle" '
        f'font-family="Segoe UI,Arial" font-size="10" font-weight="700" fill="{color}">'
        f'{html.escape(m.name)}</text>'
    )
    parts.append(
        f'<text x="{cx:.1f}" y="{bot_y + 26:.1f}" text-anchor="middle" '
        f'font-family="Segoe UI,Arial" font-size="8" fill="#666">'
        f'{"PMOS" if is_p else "NMOS"}</text>'
    )

    return {
        "G": (gate_x - 10, cy),
        "D": (cx, top_y),
        "S": (cx, bot_y),
    }


def render_wired_sheet(
    circuit: Circuit,
    devices: list[Component],
    sheet_index: int,
    sheet_total: int,
    out_path: Path,
    per_row: int = 10,
) -> Path:
    """
    One sheet: devices in rows, signal nets channel-routed (no crossings),
    VDD/GND as rails with short stubs.
    """
    devices = sorted(devices, key=lambda m: _num(m.name))
    rows: list[list[Component]] = [
        devices[i : i + per_row] for i in range(0, len(devices), per_row)
    ]

    # Collect signal-net pin columns (device index in sheet, pin) per net
    # Global column index across the sheet for routing within each inter-row channel
    # We route separately per channel (above each row for D/G connections that
    # span devices in that row; multi-row nets get a label bridge).

    parts: list[str] = []
    # First pass: compute channel track counts so we know heights
    row_meta: list[dict] = []

    for r, row in enumerate(rows):
        row_x0 = MARGIN + 40
        # pin x positions for signal nets touching this row
        net_xs: dict[str, list[float]] = defaultdict(list)
        pin_map: dict[str, list[tuple[float, str, int]]] = defaultdict(list)
        # pin_map[net] = [(x, pin_type, col_index), ...]

        for col, m in enumerate(row):
            d, g, s = m.nodes[0], m.nodes[1], m.nodes[2]
            cx = row_x0 + col * DEV_W
            # Escape X: where the vertical drop to the channel runs.
            # Different pins use different columns so verticals never share an X.
            escapes = {"G": cx - 34, "D": cx + 34, "S": cx}
            for pin, net in (("D", d), ("G", g), ("S", s)):
                if net in RAILS:
                    continue
                x = escapes[pin]
                net_xs[net].append(x)
                pin_map[net].append((x, pin, col))

        # Only route nets with >=2 pins *in this row* inside the channel
        local = {n: sorted(set(xs)) for n, xs in net_xs.items() if len(set(xs)) >= 2}
        tracks = left_edge_route(local)
        n_tracks = (max(tracks.values()) + 1) if tracks else 0

        row_meta.append(
            {
                "row": row,
                "x0": row_x0,
                "tracks": tracks,
                "n_tracks": n_tracks,
                "pin_map": pin_map,
                "local": local,
            }
        )

    # Vertical layout
    y = HEADER
    for meta in row_meta:
        meta["vdd_y"] = y
        y += 22  # rail -> device top
        meta["cy"] = y + SYM_H / 2
        y += SYM_H + 30  # symbol + name
        meta["gnd_y"] = y
        y += 18
        meta["chan_top"] = y
        y += max(meta["n_tracks"], 1) * TRACK_H + 8
        meta["chan_bot"] = y
        y += ROW_GAP

    width = MARGIN * 2 + 40 + max(len(r) for r in rows) * DEV_W + 20
    height = y + 40

    # Header
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}">'
    )
    parts.append(
        "<style>"
        ".title{font:700 17px Segoe UI,Arial;fill:#111}"
        ".sub{font:12px Segoe UI,Arial;fill:#555}"
        ".note{font:11px Segoe UI,Arial;fill:#777}"
        ".rail{stroke:#222;stroke-width:2.5}"
        ".rail-lbl{font:700 11px Segoe UI,Arial;fill:#111}"
        ".wire{stroke:#333;stroke-width:1.35;fill:none}"
        ".netlbl{font:8px Consolas,monospace;fill:#444}"
        "</style>"
    )
    parts.append(f'<rect width="100%" height="100%" fill="#fafbfc"/>')
    parts.append(f'<text class="title" x="{MARGIN}" y="28">{html.escape(circuit.title)}</text>')
    parts.append(
        f'<text class="sub" x="{MARGIN}" y="48">'
        f"Sheet {sheet_index}/{sheet_total} | {len(devices)} transistors | "
        f"wired schematic (channel-routed - wires do not cross)</text>"
    )
    parts.append(
        f'<text class="note" x="{MARGIN}" y="66">'
        f"VDD top / GND bottom rails. Signal nets use horizontal tracks between rows. "
        f"Same net name on another sheet = same connection.</text>"
    )

    # Multi-row nets: nets that appear on more than one row - label them, don't cross-row wire
    net_rows: dict[str, set[int]] = defaultdict(set)
    for ri, meta in enumerate(row_meta):
        for net, pins in meta["pin_map"].items():
            if net not in RAILS and pins:
                net_rows[net].add(ri)

    # Draw each row
    for ri, meta in enumerate(row_meta):
        row = meta["row"]
        x0 = meta["x0"]
        vdd_y = meta["vdd_y"]
        gnd_y = meta["gnd_y"]
        cy = meta["cy"]
        row_right = x0 + (len(row) - 1) * DEV_W + 40

        # rails
        parts.append(f'<line class="rail" x1="{x0-30:.1f}" y1="{vdd_y:.1f}" x2="{row_right:.1f}" y2="{vdd_y:.1f}"/>')
        parts.append(f'<text class="rail-lbl" x="{x0-30:.1f}" y="{vdd_y-6:.1f}">VDD</text>')
        parts.append(f'<line class="rail" x1="{x0-30:.1f}" y1="{gnd_y:.1f}" x2="{row_right:.1f}" y2="{gnd_y:.1f}"/>')
        parts.append(f'<text class="rail-lbl" x="{x0-30:.1f}" y="{gnd_y+14:.1f}">GND</text>')

        pin_pos: list[dict[str, tuple[float, float]]] = []
        for col, m in enumerate(row):
            cx = x0 + col * DEV_W
            pins = _draw_mos(parts, cx, cy, m)
            pin_pos.append(pins)

            # supply stubs (vertical only -> never cross each other)
            d, g, s = m.nodes[0], m.nodes[1], m.nodes[2]
            # drain side (top)
            if d in SUPPLY_HIGH:
                parts.append(f'<line class="wire" x1="{pins["D"][0]:.1f}" y1="{pins["D"][1]:.1f}" x2="{pins["D"][0]:.1f}" y2="{vdd_y:.1f}"/>')
            if s in SUPPLY_HIGH:
                # unusual but possible - route up via left offset? keep vertical from S needs detour
                # route: S goes down normally; if S is VDD, go up from D-side... better: stub from S upward beside device
                sx = pins["S"][0] + 10
                parts.append(f'<line class="wire" x1="{pins["S"][0]:.1f}" y1="{pins["S"][1]:.1f}" x2="{sx:.1f}" y2="{pins["S"][1]:.1f}"/>')
                parts.append(f'<line class="wire" x1="{sx:.1f}" y1="{pins["S"][1]:.1f}" x2="{sx:.1f}" y2="{vdd_y:.1f}"/>')
            if d in SUPPLY_LOW:
                dx = pins["D"][0] - 10
                parts.append(f'<line class="wire" x1="{pins["D"][0]:.1f}" y1="{pins["D"][1]:.1f}" x2="{dx:.1f}" y2="{pins["D"][1]:.1f}"/>')
                parts.append(f'<line class="wire" x1="{dx:.1f}" y1="{pins["D"][1]:.1f}" x2="{dx:.1f}" y2="{gnd_y:.1f}"/>')
            if s in SUPPLY_LOW:
                parts.append(f'<line class="wire" x1="{pins["S"][0]:.1f}" y1="{pins["S"][1]:.1f}" x2="{pins["S"][0]:.1f}" y2="{gnd_y:.1f}"/>')

        # Channel routing for local multi-pin nets
        tracks = meta["tracks"]
        chan_top = meta["chan_top"]
        for net, track_i in tracks.items():
            ty = chan_top + track_i * TRACK_H + TRACK_H / 2
            xs = meta["local"][net]
            x_left, x_right = min(xs), max(xs)
            # horizontal trunk
            parts.append(
                f'<line class="wire" x1="{x_left:.1f}" y1="{ty:.1f}" x2="{x_right:.1f}" y2="{ty:.1f}"/>'
            )
            # label at the LEFT end of the trunk (avoids stacking mid-track)
            parts.append(
                f'<text class="netlbl" x="{x_left - 4:.1f}" y="{ty + 3:.1f}" text-anchor="end">'
                f"{html.escape(net)}</text>"
            )
            # stubs: short jog from pin -> escape X, then pure vertical to track
            # (verticals at distinct X never cross; horizontals on distinct tracks never cross)
            for escape_x, pin, col in meta["pin_map"][net]:
                px, py = pin_pos[col][pin]
                if abs(px - escape_x) > 0.5:
                    parts.append(
                        f'<line class="wire" x1="{px:.1f}" y1="{py:.1f}" '
                        f'x2="{escape_x:.1f}" y2="{py:.1f}"/>'
                    )
                parts.append(
                    f'<line class="wire" x1="{escape_x:.1f}" y1="{py:.1f}" '
                    f'x2="{escape_x:.1f}" y2="{ty:.1f}"/>'
                )

        # Single-pin signal nets in this row that continue on other rows: label only
        for net, pins in meta["pin_map"].items():
            if net in tracks:
                continue
            if len(net_rows.get(net, set())) <= 1 and len(pins) < 2:
                # floating single endpoint - still label
                x, pin, col = pins[0]
                px, py = pin_pos[col][pin]
                if pin == "G":
                    parts.append(
                        f'<text class="netlbl" x="{px-4:.1f}" y="{py-4:.1f}" text-anchor="end">'
                        f"{html.escape(net)}</text>"
                    )
                elif pin == "D":
                    parts.append(
                        f'<text class="netlbl" x="{px+4:.1f}" y="{py-4:.1f}">'
                        f"{html.escape(net)}</text>"
                    )
                else:
                    parts.append(
                        f'<text class="netlbl" x="{px+4:.1f}" y="{py+12:.1f}">'
                        f"{html.escape(net)}</text>"
                    )
            elif len(net_rows.get(net, set())) > 1:
                # cross-row net: label at each pin (connection continues by name)
                for x, pin, col in pins:
                    px, py = pin_pos[col][pin]
                    tag = f"v {net}"
                    if pin == "G":
                        parts.append(
                            f'<text class="netlbl" x="{px-4:.1f}" y="{py-4:.1f}" text-anchor="end" fill="#1a5f9e">'
                            f"{html.escape(tag)}</text>"
                        )
                    elif pin == "D":
                        parts.append(
                            f'<text class="netlbl" x="{px+4:.1f}" y="{py-4:.1f}" fill="#1a5f9e">'
                            f"{html.escape(tag)}</text>"
                        )
                    else:
                        parts.append(
                            f'<text class="netlbl" x="{px+4:.1f}" y="{py+12:.1f}" fill="#1a5f9e">'
                            f"{html.escape(tag)}</text>"
                        )

    parts.append("</svg>")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path


def render_wired(
    circuit: Circuit,
    out_dir: Path,
    stem: str = "circuit",
    per_sheet: int = 20,
    per_row: int = 10,
) -> tuple[list[Path], Path]:
    """Generate wired (channel-routed) sheets + HTML index."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mos = sorted(_mosfets(circuit), key=lambda m: _num(m.name))
    sheets = [mos[i : i + per_sheet] for i in range(0, len(mos), per_sheet)]
    svgs: list[Path] = []

    for i, chunk in enumerate(sheets, start=1):
        path = out_dir / f"{stem}_wired_{i:02d}.svg"
        render_wired_sheet(circuit, chunk, i, len(sheets), path, per_row=per_row)
        svgs.append(path)

    html_path = out_dir / f"{stem}_wired.html"
    thumbs = "\n".join(
        f'<section class="sheet" id="s{i}"><h2>Sheet {i} / {len(svgs)}</h2>'
        f'<div class="frame"><img src="{html.escape(p.name)}" alt="sheet {i}"/></div></section>'
        for i, p in enumerate(svgs, start=1)
    )
    html_path.write_text(
        f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>{html.escape(circuit.title)} - wired schematic</title>
<style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#111;color:#eee}}
header{{padding:20px 24px;border-bottom:1px solid #333}}
h1{{margin:0 0 6px;font-size:1.25rem}}
.meta{{color:#9aa}}
nav{{padding:12px 24px}}
nav a{{color:#9cf;margin-right:10px;font-size:.85rem}}
.sheet{{padding:16px 24px}}
.frame{{background:#fff;border-radius:8px;overflow:auto;padding:8px}}
.frame img{{max-width:100%;height:auto;display:block}}
</style></head><body>
<header>
<h1>{html.escape(circuit.title)}</h1>
<p class="meta">{len(mos)} MOSFETs | {len(svgs)} sheets | channel-routed wiring (no crossing wires within a channel)</p>
<p class="meta">Blue v labels mark nets that continue on another row/sheet (same name = connected).</p>
</header>
<nav>{' '.join(f'<a href="#s{i}">Sheet {i}</a>' for i in range(1, len(svgs)+1))}</nav>
{thumbs}
</body></html>
""",
        encoding="utf-8",
    )
    return svgs, html_path
