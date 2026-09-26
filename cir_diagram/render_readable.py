"""
Readable CMOS/PTL schematic sheets.

Layout rules that keep the drawing understandable:
  - every device lives inside its own bounded card, so nothing is drawn across
    the sheet and there are no long crossing wires
  - CMOS inverter pairs (shared gate + shared drain) are stacked PMOS over NMOS
    and joined by one short vertical link
  - supplies are drawn as local VDD bars / GND symbols inside the card
  - connectivity is carried by net names printed next to each terminal
  - every text box is measured, and card sizes are derived from the widest
    label on the sheet, so labels cannot collide
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

# Width factors per character, calibrated for the fonts used below.
_MONO_FACTOR = 0.605
_SANS_FACTOR = 0.545
_SANS_BOLD_FACTOR = 0.585


def text_width(s: str, size: float, family: str = "mono", bold: bool = False) -> float:
    if family == "mono":
        factor = _MONO_FACTOR
    else:
        factor = _SANS_BOLD_FACTOR if bold else _SANS_FACTOR
    return len(s) * size * factor


class Canvas:
    """Collects SVG fragments and the bounding box of every text item."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.boxes: list[tuple[float, float, float, float, str]] = []
        self.frames: list[tuple[float, float, float, float]] = []
        self.size: tuple[float, float] = (0.0, 0.0)

    def raw(self, fragment: str) -> None:
        self.parts.append(fragment)

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        fill: str = "none",
        stroke: str = "#333",
        width: float = 1.0,
        rx: float = 0.0,
        dash: str | None = None,
    ) -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{width}"{d}/>'
        )

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        stroke: str = "#333",
        width: float = 1.4,
        dash: str | None = None,
    ) -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{stroke}" stroke-width="{width}"{d}/>'
        )

    def circle(self, cx: float, cy: float, r: float, *, fill: str = "#fff", stroke: str = "#333", width: float = 1.4) -> None:
        self.parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{width}"/>'
        )

    def text(
        self,
        x: float,
        y: float,
        s: str,
        *,
        size: float = 9,
        family: str = "mono",
        bold: bool = False,
        fill: str = "#222",
        anchor: str = "start",
        record: bool = True,
    ) -> None:
        w = text_width(s, size, family, bold)
        if anchor == "start":
            x0 = x
        elif anchor == "middle":
            x0 = x - w / 2
        else:
            x0 = x - w
        y0 = y - size * 0.80
        y1 = y + size * 0.25
        if record:
            self.boxes.append((x0, y0, x0 + w, y1, s))

        font = "Consolas,monospace" if family == "mono" else "Segoe UI,Arial,sans-serif"
        weight = ' font-weight="700"' if bold else ""
        self.parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" '
            f'font-family="{font}" font-size="{size}"{weight} fill="{fill}">'
            f"{html.escape(s)}</text>"
        )

    def svg(self, width: float, height: float, style: str, background: str = "#ffffff") -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
            f'height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}">\n'
            f"<style>{style}</style>\n"
            f'<rect width="100%" height="100%" fill="{background}"/>\n'
            + "\n".join(self.parts)
            + "\n</svg>\n"
        )


def find_text_outside_frames(cv: "Canvas", margin: float = 2.0) -> list[str]:
    """Labels that stick out of their card (or out of the sheet, for headers)."""
    width, height = cv.size
    bad: list[str] = []
    for x0, y0, x1, y1, label in cv.boxes:
        if x0 < margin or y0 < 0 or x1 > width - margin or y1 > height:
            bad.append(label)
            continue

        mid_x, mid_y = (x0 + x1) / 2, (y0 + y1) / 2
        row = [f for f in cv.frames if f[1] <= mid_y <= f[3]]
        if not row:
            continue  # header text, already bounded by the sheet

        # the owning card is the one horizontally closest to the label
        fx0, fy0, fx1, fy1 = min(
            row, key=lambda f: abs((f[0] + f[2]) / 2 - mid_x)
        )
        if x0 < fx0 + margin or x1 > fx1 - margin:
            bad.append(label)
    return bad


def find_text_overlaps(
    boxes: list[tuple[float, float, float, float, str]], tolerance: float = 0.5
) -> list[tuple[str, str]]:
    """Return pairs of text labels whose bounding boxes intersect."""
    hits: list[tuple[str, str]] = []
    ordered = sorted(range(len(boxes)), key=lambda i: boxes[i][0])
    for a_i, i in enumerate(ordered):
        ax0, ay0, ax1, ay1, alabel = boxes[i]
        for j in ordered[a_i + 1 :]:
            bx0, by0, bx1, by1, blabel = boxes[j]
            if bx0 >= ax1 - tolerance:
                break
            if ay0 < by1 - tolerance and by0 < ay1 - tolerance:
                hits.append((alabel, blabel))
    return hits


def _mosfets(circuit: Circuit) -> list[Component]:
    return [c for c in circuit.components if c.kind in {"m_n", "m_p"}]


def _num(name: str) -> int:
    m = re.search(r"(\d+)", name)
    return int(m.group(1)) if m else 0


def _find_inverters(mos: list[Component]) -> list[tuple[Component, Component]]:
    """PMOS + NMOS sharing both gate and drain form a CMOS inverter."""
    by_gate: dict[str, list[Component]] = defaultdict(list)
    for m in mos:
        by_gate[m.nodes[1]].append(m)

    pairs: list[tuple[Component, Component]] = []
    used: set[str] = set()
    for group in by_gate.values():
        for p in (m for m in group if m.kind == "m_p"):
            if p.name in used:
                continue
            for n in (m for m in group if m.kind == "m_n"):
                if n.name in used or n.nodes[0] != p.nodes[0]:
                    continue
                pairs.append((p, n))
                used.add(p.name)
                used.add(n.name)
                break
    return pairs


def _units(mos: list[Component]) -> list[list[Component]]:
    """Drawing units in netlist order: inverter pairs stay together."""
    partner: dict[str, Component] = {}
    for p, n in _find_inverters(mos):
        partner[p.name] = n
        partner[n.name] = p

    seen: set[str] = set()
    units: list[list[Component]] = []
    for m in sorted(mos, key=lambda x: _num(x.name)):
        if m.name in seen:
            continue
        q = partner.get(m.name)
        if q is not None and q.name not in seen:
            p, n = (m, q) if m.kind == "m_p" else (q, m)
            units.append([p, n])
            seen.update({p.name, n.name})
        else:
            units.append([m])
            seen.add(m.name)
    return units


def _terminals(m: Component) -> tuple[tuple[str, str], tuple[str, str]]:
    """
    Return ((top_role, top_net), (bottom_role, bottom_net)).

    A PMOS is drawn source-up so a CMOS inverter reads the conventional way;
    every other device keeps drain-up.
    """
    drain, _gate, source = m.nodes[0], m.nodes[1], m.nodes[2]
    if m.kind == "m_p":
        return ("S", source), ("D", drain)
    return ("D", drain), ("S", source)


def _unit_labels(unit: list[Component]) -> tuple[list[str], list[str]]:
    """Labels that sit left of the symbols, and labels that sit right of them."""
    left: list[str] = []
    right: list[str] = []
    for m in unit:
        left.append(f"G {m.nodes[1]}")
        top, bottom = _terminals(m)
        right.append(f"{top[0]} {top[1]}")
        right.append(f"{bottom[0]} {bottom[1]}")
        right.append(f"{m.name} {'PMOS' if m.kind == 'm_p' else 'NMOS'}")
    return left, right


# Fixed vertical rhythm inside a card.
_TITLE_Y = 20.0
_SUB_Y = 35.0
_BODY_TOP = _SUB_Y + 16.0  # first usable y inside the card body
_SYM_HALF = 30.0           # symbol half height, stub tip to stub tip
_MARK_TOP = 36.0           # room reserved above a symbol for a supply marker
_MARK_BOTTOM = 42.0        # room reserved below a symbol for a ground marker
_PAIR_GAP = 34.0           # gap between the two symbol stubs in a pair
_LABEL_SIZE = 9.0
_NAME_SIZE = 9.0
_CARD_BOTTOM_PAD = 12.0


def _card_height(max_devices: int) -> float:
    body = _MARK_TOP + _SYM_HALF * 2 + _MARK_BOTTOM
    if max_devices == 2:
        body += _SYM_HALF * 2 + _PAIR_GAP
    return _BODY_TOP + body + _CARD_BOTTOM_PAD


def _role(unit: list[Component]) -> str:
    if len(unit) == 2:
        return "CMOS inverter"
    m = unit[0]
    kind = "PMOS" if m.kind == "m_p" else "NMOS"
    drain, source = m.nodes[0], m.nodes[2]
    if source in SUPPLY_HIGH:
        return f"{kind} pull-up"
    if source in SUPPLY_LOW:
        return f"{kind} pull-down"
    if drain not in RAILS and source not in RAILS:
        return f"{kind} pass transistor"
    return kind


def _draw_symbol(
    cv: Canvas,
    cx: float,
    cy: float,
    m: Component,
    left_x: float,
    right_x: float,
    *,
    show_top_label: bool = True,
    show_bottom_label: bool = True,
) -> tuple[float, float]:
    """Draw one MOSFET; return (top_stub_y, bottom_stub_y)."""
    is_p = m.kind == "m_p"
    color = "#b26a00" if is_p else "#1a5f9e"
    (top_role, top_net), (bottom_role, bottom_net) = _terminals(m)

    gate_x = cx - 22
    chan_x = cx - 12
    top_y = cy - _SYM_HALF
    bot_y = cy + _SYM_HALF

    # gate lead and plate
    cv.line(left_x + 4, cy, gate_x - 6, cy, stroke=color, width=1.4)
    cv.line(gate_x, cy - 15, gate_x, cy + 15, stroke=color, width=2.0)
    if is_p:
        cv.circle(gate_x - 10, cy, 3.4, fill="#fff", stroke=color, width=1.4)
    else:
        cv.line(gate_x - 6, cy, gate_x, cy, stroke=color, width=1.4)

    # channel and terminals
    cv.line(chan_x, cy - 15, chan_x, cy + 15, stroke=color, width=2.4)
    cv.line(chan_x, cy - 12, cx + 6, cy - 12, stroke=color, width=1.4)
    cv.line(cx + 6, cy - 12, cx + 6, top_y, stroke=color, width=1.4)
    cv.line(chan_x, cy + 12, cx + 6, cy + 12, stroke=color, width=1.4)
    cv.line(cx + 6, cy + 12, cx + 6, bot_y, stroke=color, width=1.4)
    cv.line(chan_x, cy, cx + 6, cy, stroke=color, width=1.2)

    # labels sit on three separate rows, so they cannot touch each other
    cv.text(left_x, cy + 3, f"G {m.nodes[1]}", size=_LABEL_SIZE, anchor="end", fill="#333")
    if show_top_label:
        cv.text(right_x, cy - 22, f"{top_role} {top_net}", size=_LABEL_SIZE, fill="#333")
    cv.text(
        right_x,
        cy + 3,
        f"{m.name} {'PMOS' if is_p else 'NMOS'}",
        size=_NAME_SIZE,
        family="sans",
        bold=True,
        fill=color,
    )
    if show_bottom_label:
        cv.text(right_x, cy + 28, f"{bottom_role} {bottom_net}", size=_LABEL_SIZE, fill="#333")

    return top_y, bot_y


def _draw_rail_marker(cv: Canvas, x: float, stub_y: float, net: str, *, upward: bool) -> bool:
    """Draw a VDD bar or ground symbol beyond a stub. Returns True if drawn."""
    if net not in RAILS:
        return False

    y = stub_y - 14 if upward else stub_y + 14
    cv.line(x, stub_y, x, y, stroke="#111", width=1.4)

    if net in SUPPLY_HIGH:
        cv.line(x - 14, y, x + 14, y, stroke="#111", width=2.6)
        label_y = y - 7 if upward else y + 15
        cv.text(x, label_y, net, size=8.5, family="sans", bold=True, anchor="middle", fill="#111")
        return True

    step = -5 if upward else 5
    cv.line(x - 14, y, x + 14, y, stroke="#111", width=2.6)
    cv.line(x - 9, y + step, x + 9, y + step, stroke="#111", width=2.0)
    cv.line(x - 4, y + 2 * step, x + 4, y + 2 * step, stroke="#111", width=1.6)
    label_y = y + 3 * step - 4 if upward else y + 3 * step + 9
    cv.text(
        x,
        label_y,
        "GND" if net == "0" else net,
        size=8.5,
        family="sans",
        bold=True,
        anchor="middle",
        fill="#111",
    )
    return True


def _draw_card(
    cv: Canvas,
    x0: float,
    y0: float,
    w: float,
    h: float,
    unit: list[Component],
    left_pad: float,
    right_pad: float,
) -> None:
    is_pair = len(unit) == 2
    cv.rect(x0, y0, w, h, fill="#ffffff", stroke="#c9ced6", width=1.2, rx=10)
    cv.frames.append((x0, y0, x0 + w, y0 + h))

    names = " + ".join(m.name for m in unit)
    cv.text(
        x0 + 12,
        y0 + _TITLE_Y,
        f"{names} | {_role(unit)}",
        size=11,
        family="sans",
        bold=True,
        fill="#111",
    )
    cv.text(x0 + 12, y0 + _SUB_Y, f"output (drain): {unit[0].nodes[0]}", size=9, fill="#5a6270")

    cx = x0 + left_pad + 34
    left_x = x0 + left_pad - 10
    right_x = cx + 16
    stub_x = cx + 6
    body_top = y0 + _BODY_TOP

    if is_pair:
        p, n = unit
        cy_p = body_top + _MARK_TOP + _SYM_HALF
        cy_n = cy_p + _SYM_HALF * 2 + _PAIR_GAP
        p_top_net = _terminals(p)[0][1]
        n_bot_net = _terminals(n)[1][1]

        p_top, p_bot = _draw_symbol(
            cv, cx, cy_p, p, left_x, right_x,
            show_top_label=p_top_net not in RAILS,
        )
        # the NMOS top terminal is the same net as the PMOS bottom one - label it once
        n_top, n_bot = _draw_symbol(
            cv, cx, cy_n, n, left_x, right_x,
            show_top_label=False,
            show_bottom_label=n_bot_net not in RAILS,
        )

        # the only wire on the card: the short drain-to-drain link
        cv.line(stub_x, p_bot, stub_x, n_top, stroke="#222", width=1.8)
        cv.circle(stub_x, (p_bot + n_top) / 2, 3.2, fill="#222", stroke="#222", width=1.0)

        _draw_rail_marker(cv, stub_x, p_top, p_top_net, upward=True)
        _draw_rail_marker(cv, stub_x, n_bot, n_bot_net, upward=False)
    else:
        m = unit[0]
        cy = y0 + (_BODY_TOP + h - _CARD_BOTTOM_PAD) / 2
        (_, top_net), (_, bottom_net) = _terminals(m)
        top_y, bot_y = _draw_symbol(
            cv, cx, cy, m, left_x, right_x,
            show_top_label=top_net not in RAILS,
            show_bottom_label=bottom_net not in RAILS,
        )
        _draw_rail_marker(cv, stub_x, top_y, top_net, upward=True)
        _draw_rail_marker(cv, stub_x, bot_y, bottom_net, upward=False)


def render_sheet(
    circuit: Circuit,
    units: list[list[Component]],
    sheet_index: int,
    sheet_total: int,
    out_path: Path,
    cols: int = 4,
) -> Canvas:
    left_labels: list[str] = []
    right_labels: list[str] = []
    for unit in units:
        left, right = _unit_labels(unit)
        left_labels.extend(left)
        right_labels.extend(right)

    max_left = max((text_width(s, _LABEL_SIZE) for s in left_labels), default=40.0)
    max_right = max((text_width(s, _LABEL_SIZE) for s in right_labels), default=40.0)
    left_pad = max_left + 22
    right_pad = max_right + 22

    card_w = left_pad + 34 + right_pad
    title_w = max(
        (
            text_width(f"{' + '.join(m.name for m in u)} | {_role(u)}", 11, "sans", True)
            for u in units
        ),
        default=120.0,
    )
    sub_w = max((text_width(f"output (drain): {u[0].nodes[0]}", 9) for u in units), default=120.0)
    card_w = max(card_w, title_w + 24, sub_w + 24)

    card_h = _card_height(max(len(u) for u in units))
    gap_x, gap_y = 26.0, 26.0
    margin = 30.0
    header_h = 96.0

    n_cols = max(1, min(cols, len(units)))
    n_rows = (len(units) + n_cols - 1) // n_cols
    width = margin * 2 + n_cols * card_w + (n_cols - 1) * gap_x
    height = header_h + margin + n_rows * card_h + (n_rows - 1) * gap_y

    cv = Canvas()
    cv.size = (width, height)
    cv.text(margin, 34, circuit.title, size=18, family="sans", bold=True, fill="#111")
    cv.text(
        margin,
        56,
        f"Sheet {sheet_index} of {sheet_total} | {sum(len(u) for u in units)} transistors on this sheet",
        size=11,
        family="sans",
        fill="#4a5058",
    )
    cv.text(
        margin,
        74,
        "Connections are by net name: terminals sharing a name are the same node. G = gate, D = drain, S = source.",
        size=10,
        family="sans",
        fill="#6b7280",
    )

    for i, unit in enumerate(units):
        r, c = divmod(i, n_cols)
        x0 = margin + c * (card_w + gap_x)
        y0 = header_h + r * (card_h + gap_y)
        _draw_card(cv, x0, y0, card_w, card_h, unit, left_pad, right_pad)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(cv.svg(width, height, "", background="#f7f8fa"), encoding="utf-8")
    return cv


def render_readable(
    circuit: Circuit,
    out_dir: Path,
    stem: str = "circuit",
    per_sheet: int = 12,
    cols: int = 4,
) -> tuple[list[Path], Path, list[tuple[str, str]]]:
    """Write one SVG per sheet plus an index HTML. Returns (svgs, html, overlaps)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    mos = _mosfets(circuit)
    units = _units(mos)
    sheets = [units[i : i + per_sheet] for i in range(0, len(units), per_sheet)]

    svg_paths: list[Path] = []
    overlaps: list[tuple[str, str]] = []
    net_sheets: dict[str, set[int]] = defaultdict(set)

    for i, sheet_units in enumerate(sheets, start=1):
        path = out_dir / f"{stem}_sheet_{i:02d}.svg"
        cv = render_sheet(circuit, sheet_units, i, len(sheets), path, cols=cols)
        overlaps.extend(find_text_overlaps(cv.boxes))
        overlaps.extend((label, f"outside card on sheet {i}") for label in find_text_outside_frames(cv))
        svg_paths.append(path)
        for unit in sheet_units:
            for m in unit:
                for net in m.nodes[:3]:
                    if net not in RAILS:
                        net_sheets[net].add(i)

    html_path = out_dir / f"{stem}_readable.html"
    html_path.write_text(
        _build_html(circuit, mos, svg_paths, net_sheets), encoding="utf-8"
    )
    return svg_paths, html_path, overlaps


def _build_html(
    circuit: Circuit,
    mos: list[Component],
    svg_paths: list[Path],
    net_sheets: dict[str, set[int]],
) -> str:
    sheets_html = "\n".join(
        f'<section class="sheet" id="sheet{i}">'
        f"<h2>Sheet {i} of {len(svg_paths)}</h2>"
        f'<div class="frame"><img src="{html.escape(p.name)}" alt="schematic sheet {i}"/></div>'
        f"</section>"
        for i, p in enumerate(svg_paths, start=1)
    )

    device_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(m.name)}</td>"
        f"<td>{'PMOS' if m.kind == 'm_p' else 'NMOS'}</td>"
        f"<td>{html.escape(m.nodes[0])}</td>"
        f"<td>{html.escape(m.nodes[1])}</td>"
        f"<td>{html.escape(m.nodes[2])}</td>"
        f"<td>{html.escape(m.nodes[3] if len(m.nodes) > 3 else '')}</td>"
        f"<td>{html.escape(m.params.get('w', ''))}</td>"
        f"<td>{html.escape(m.params.get('l', ''))}</td>"
        "</tr>"
        for m in sorted(mos, key=lambda x: _num(x.name))
    )

    shared = {net: s for net, s in net_sheets.items() if len(s) > 1}
    net_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(net)}</td>"
        f"<td>{', '.join(f'<a href=\"#sheet{i}\">{i}</a>' for i in sorted(sheets))}</td>"
        "</tr>"
        for net, sheets in sorted(shared.items(), key=lambda kv: kv[0])
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html.escape(circuit.title)}</title>
<style>
 body{{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#eef1f5;color:#1b1f24}}
 header{{background:#fff;padding:22px 28px;border-bottom:1px solid #d6dbe2}}
 h1{{margin:0 0 6px;font-size:1.3rem}}
 .meta{{color:#5a6270;font-size:.95rem;margin:0 0 4px}}
 .legend{{color:#6b7280;font-size:.85rem;margin:6px 0 0}}
 nav{{padding:14px 28px;background:#fff;border-bottom:1px solid #d6dbe2}}
 nav a{{display:inline-block;margin:2px 6px 2px 0;padding:3px 9px;border:1px solid #ccd2da;
       border-radius:6px;color:#25406b;text-decoration:none;font-size:.82rem}}
 nav a:hover{{background:#25406b;color:#fff}}
 .sheet{{padding:22px 28px}}
 .sheet h2{{font-size:1rem;color:#333;margin:0 0 10px}}
 .frame{{background:#fff;border:1px solid #d6dbe2;border-radius:10px;overflow:auto;padding:10px}}
 .frame img{{display:block;max-width:100%;height:auto}}
 table{{border-collapse:collapse;width:calc(100% - 56px);margin:12px 28px 32px;background:#fff;font-size:12px}}
 th,td{{border:1px solid #dde1e7;padding:5px 9px;text-align:left}}
 th{{background:#f2f5f9;position:sticky;top:0}}
 h2.section{{padding:0 28px;font-size:1.05rem;margin:26px 0 0}}
</style></head><body>
<header>
 <h1>{html.escape(circuit.title)}</h1>
 <p class="meta">{len(mos)} MOSFETs | {len(svg_paths)} sheets</p>
 <p class="legend">Each card is one device or one CMOS inverter pair. Terminals are labelled
 G (gate), D (drain), S (source) followed by the net name - terminals carrying the same net name
 are electrically connected, which is why no long wires cross the sheet. Bulk connections are
 listed in the device table below.</p>
</header>
<nav>{' '.join(f'<a href="#sheet{i}">Sheet {i}</a>' for i in range(1, len(svg_paths) + 1))}</nav>
{sheets_html}
<h2 class="section">Net cross-reference (nets appearing on more than one sheet)</h2>
<table><thead><tr><th>Net</th><th>Sheets</th></tr></thead><tbody>
{net_rows}
</tbody></table>
<h2 class="section">Device table</h2>
<table><thead><tr><th>Name</th><th>Type</th><th>Drain</th><th>Gate</th><th>Source</th><th>Bulk</th><th>W</th><th>L</th></tr></thead><tbody>
{device_rows}
</tbody></table>
</body></html>
"""
