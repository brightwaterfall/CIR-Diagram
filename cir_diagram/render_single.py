"""
Single-page wired schematic - every CIR net is drawn as one connected piece.

Routing (guaranteed connectivity):
  1. Place devices by logic depth (wrapped rows) + barycentre ordering.
  2. For each signal net, attach every pin to a channel:
       - Drain  of a device on row r  -> channel r   (below the row)
       - Gate/Source of a device on row r -> channel r-1 (above the row)
  3. Give every occupied channel a horizontal trunk spanning its attachments.
  4. Tie consecutive occupied channels with one vertical lane.
  5. Left-edge assign tracks inside each channel so trunks never overlap.
  6. Junction dots only where a vertical meets its trunk.
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

# Chip-level ports for this PTL netlist (client interface).
# Complements B*_b / M_b still appear as undriven gate nets in the CIR, but the
# declared interface is 14 inputs + 8 outputs.
PRIMARY_INPUTS: tuple[str, ...] = (
    "A0", "A0_b", "A1", "A1_b", "A2", "A2_b", "A3", "A3_b",
    "B0", "B1", "B2", "B3", "Cn", "M",
)
PRIMARY_OUTPUTS: tuple[str, ...] = (
    "Y_BUFFERED_OUT",
    "BLOCK_OUT_273",
    "BLOCK_OUT_265",
    "BLOCK_OUT_253",
    "BLOCK_OUT_229",
    "BLOCK_OUT_215",
    "BLOCK_OUT_191",
    "BLOCK_OUT_167",
)

BOX_W = 62.0
BOX_H = 50.0
PITCH = 112.0
TRACK_H = 16.0
LANE_STEP = 8.0
LANES_PER_GAP = 8
ROW_PAD_TOP = 36.0
ROW_PAD_BOTTOM = 16.0
CHANNEL_PAD = 16.0
MARGIN = 70.0
HEADER = 150.0
INPUT_BUS = 80.0
CHAR_W = 4.7  # approx monospace label width per character
LABEL_H = 10.0


def classify_net(net: str, has_driver: bool, has_consumer: bool) -> str:
    """Return label class: in / out / nl."""
    if net in PRIMARY_INPUTS:
        return "in"
    if net in PRIMARY_OUTPUTS:
        return "out"
    return "nl"


def _mosfets(circuit: Circuit) -> list[Component]:
    return [c for c in circuit.components if c.kind in {"m_n", "m_p"}]


def _num(name: str) -> int:
    m = re.search(r"(\d+)", name)
    return int(m.group(1)) if m else 0


def assign_levels(mos: list[Component]) -> dict[str, int]:
    drivers: dict[str, list[Component]] = defaultdict(list)
    for m in mos:
        drivers[m.nodes[0]].append(m)
    level = {m.name: 0 for m in mos}
    for _ in range(64):
        changed = False
        for m in mos:
            best = 0
            for net in (m.nodes[1], m.nodes[2]):
                if net in RAILS:
                    continue
                for d in drivers.get(net, ()):
                    if d.name != m.name:
                        best = max(best, level[d.name] + 1)
            if best > level[m.name]:
                level[m.name] = best
                changed = True
        if not changed:
            break
    return level


def build_rows(mos: list[Component], max_per_row: int) -> list[list[Component]]:
    level = assign_levels(mos)
    by_level: dict[int, list[Component]] = defaultdict(list)
    for m in mos:
        by_level[level[m.name]].append(m)
    rows: list[list[Component]] = []
    for lv in sorted(by_level):
        group = sorted(by_level[lv], key=lambda m: _num(m.name))
        for i in range(0, len(group), max_per_row):
            rows.append(group[i : i + max_per_row])
    return rows


def order_rows(rows: list[list[Component]], sweeps: int = 6) -> list[list[Component]]:
    pos: dict[str, tuple[int, int]] = {}
    for r, row in enumerate(rows):
        for c, m in enumerate(row):
            pos[m.name] = (r, c)

    driver_of: dict[str, list[str]] = defaultdict(list)
    consumer_of: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        for m in row:
            driver_of[m.nodes[0]].append(m.name)
            for net in (m.nodes[1], m.nodes[2]):
                if net not in RAILS:
                    consumer_of[net].append(m.name)

    def resort(r: int, key) -> None:
        scored = sorted(
            ((key(c, m), c, m) for c, m in enumerate(rows[r])),
            key=lambda t: (t[0], t[1]),
        )
        rows[r] = [m for _, _, m in scored]
        for c, m in enumerate(rows[r]):
            pos[m.name] = (r, c)

    for sweep in range(sweeps):
        if sweep % 2 == 0:
            for r in range(1, len(rows)):
                def key(c: int, m: Component, r=r) -> float:
                    cols = [
                        pos[d][1]
                        for net in (m.nodes[1], m.nodes[2])
                        if net not in RAILS
                        for d in driver_of.get(net, ())
                        if d in pos and pos[d][0] < r
                    ]
                    return sum(cols) / len(cols) if cols else float(c)

                resort(r, key)
        else:
            for r in range(len(rows) - 2, -1, -1):
                def key(c: int, m: Component, r=r) -> float:
                    cols = [
                        pos[u][1]
                        for u in consumer_of.get(m.nodes[0], ())
                        if u in pos and pos[u][0] > r
                    ]
                    return sum(cols) / len(cols) if cols else float(c)

                resort(r, key)
    return rows


def left_edge_tracks(intervals: list[tuple[float, float, str]], min_gap: float = 120.0) -> dict[str, int]:
    """Assign track indices. min_gap keeps nearby spans off the same Y so they
    do not look like one broken horizontal line. Must exceed device PITCH (112)
    so adjacent-column trunks never share a track."""
    intervals = sorted(intervals, key=lambda t: (t[0], t[1]))
    ends: list[float] = []
    out: dict[str, int] = {}
    for left, right, key in intervals:
        for i, end in enumerate(ends):
            if left >= end + min_gap:
                ends[i] = right
                out[key] = i
                break
        else:
            out[key] = len(ends)
            ends.append(right)
    return out


def true_span(xs: list[float]) -> tuple[float, float]:
    """Span covering real attachment Xs only - no fake stub padding."""
    return min(xs), max(xs)


def pack_span(xs: list[float]) -> tuple[float, float]:
    """Span used only for left-edge track packing.

    Point-like nets reserve a wide exclusive zone so they are not placed on the
    same Y as a nearby trunk (which looks like a broken horizontal line).
    """
    lo, hi = min(xs), max(xs)
    if hi - lo < 2.0:
        return lo - 56.0, lo + 56.0
    return lo, hi


def label_width(net: str) -> float:
    return len(net) * CHAR_W + 6.0


def boxes_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float], pad: float = 2.0) -> bool:
    return not (
        a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1]
    )


class LanePool:
    def __init__(self, n_cols: int) -> None:
        self.xs: list[float] = []
        for gap in range(n_cols + 1):
            centre = MARGIN + gap * PITCH - (PITCH - BOX_W) / 2
            base = centre - (LANES_PER_GAP - 1) * LANE_STEP / 2
            self.xs.extend(base + k * LANE_STEP for k in range(LANES_PER_GAP))
        self.xs.sort()
        self.used: dict[float, list[tuple[int, int]]] = defaultdict(list)

    def take(self, desired_x: float, c_lo: int, c_hi: int) -> float:
        lo, hi = sorted((c_lo, c_hi))
        for x in sorted(self.xs, key=lambda v: abs(v - desired_x)):
            if all(hi < a or lo > b for a, b in self.used[x]):
                self.used[x].append((lo, hi))
                return x
        x = min(self.xs, key=lambda v: abs(v - desired_x))
        self.used[x].append((lo, hi))
        return x


def render_single(
    circuit: Circuit,
    out_svg: Path,
    max_per_row: int = 28,
) -> tuple[Path, dict]:
    mos = sorted(_mosfets(circuit), key=lambda m: _num(m.name))
    rows = order_rows(build_rows(mos, max_per_row))
    n_cols = max(len(r) for r in rows)

    col_of: dict[str, int] = {}
    row_of: dict[str, int] = {}
    for r, row in enumerate(rows):
        for c, m in enumerate(row):
            row_of[m.name] = r
            col_of[m.name] = c

    def dev_x(m: Component) -> float:
        return MARGIN + col_of[m.name] * PITCH + BOX_W / 2

    def escape_x(m: Component, pin: str) -> float:
        cx = dev_x(m)
        return cx - BOX_W / 2 - 10 if pin == "G" else cx

    # -- per-net channel attachments ---------------------------------
    # attachments[net][channel] = list of escape x
    # pin_attach[net] = list of (m, pin, channel, escape_x)
    attachments: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    pin_attach: dict[str, list[tuple[Component, str, int, float]]] = defaultdict(list)
    is_output: dict[str, bool] = {}
    net_class: dict[str, str] = {}

    drivers: dict[str, list[Component]] = defaultdict(list)
    consumers: dict[str, list[tuple[Component, str]]] = defaultdict(list)
    for m in mos:
        drivers[m.nodes[0]].append(m)
        for pin, net in (("G", m.nodes[1]), ("S", m.nodes[2])):
            if net not in RAILS:
                consumers[net].append((m, pin))

    signal_nets = sorted(
        n
        for n in set(list(drivers) + list(consumers))
        if n not in RAILS and (drivers.get(n) or consumers.get(n))
    )

    for net in signal_nets:
        for d in drivers.get(net, ()):
            ch = row_of[d.name]  # drain -> channel below its row
            x = escape_x(d, "D")
            attachments[net][ch].append(x)
            pin_attach[net].append((d, "D", ch, x))
        for m, pin in consumers.get(net, ()):
            ch = row_of[m.name] - 1  # gate/source -> channel above its row
            x = escape_x(m, pin)
            attachments[net][ch].append(x)
            pin_attach[net].append((m, pin, ch, x))
        has_drv = bool(drivers.get(net))
        has_con = bool(consumers.get(net))
        is_output[net] = net in PRIMARY_OUTPUTS
        net_class[net] = classify_net(net, has_drv, has_con)

    # -- lanes between consecutive occupied channels -----------------
    lanes = LanePool(n_cols)
    # verticals[net] = list of (lane_x, ch_lo, ch_hi)
    verticals: dict[str, list[tuple[float, int, int]]] = defaultdict(list)

    for net in signal_nets:
        used = sorted(attachments[net].keys())
        for a, b in zip(used, used[1:]):
            xs = attachments[net][a] + attachments[net][b]
            desired = sum(xs) / len(xs)
            lane_x = lanes.take(desired, a, b)
            attachments[net][a].append(lane_x)
            attachments[net][b].append(lane_x)
            verticals[net].append((lane_x, a, b))

    # -- horizontal intervals per channel ----------------------------
    # h_runs[channel] = list of (lo, hi, key)  - packing intervals
    # net_span[net][ch] = (lo, hi)             - real wire span
    h_runs: dict[int, list[tuple[float, float, str]]] = defaultdict(list)
    net_span: dict[str, dict[int, tuple[float, float]]] = defaultdict(dict)

    for net in signal_nets:
        for ch, xs in attachments[net].items():
            lo, hi = true_span(xs)
            plo, phi = pack_span(xs)
            key = f"{net}@{ch}"
            h_runs[ch].append((plo, phi, key))
            net_span[net][ch] = (lo, hi)

    tracks: dict[int, dict[str, int]] = {
        ch: left_edge_tracks(runs) for ch, runs in h_runs.items()
    }

    # -- vertical geometry -------------------------------------------
    channel_y0: dict[int, float] = {}
    channel_h: dict[int, float] = {}
    row_y: list[float] = []

    def ch_height(ch: int) -> float:
        n = (max(tracks[ch].values()) + 1) if tracks.get(ch) else 0
        # dangling outputs need a little extra label room
        extra = 0.0
        for net, spans in net_span.items():
            if ch in spans and is_output.get(net):
                extra = max(extra, 28.0)
        return max(n * TRACK_H + CHANNEL_PAD * 2, extra, INPUT_BUS if ch == -1 else 0.0)

    y = HEADER
    channel_y0[-1] = y
    channel_h[-1] = ch_height(-1)
    y += channel_h[-1]

    for r in range(len(rows)):
        y += ROW_PAD_TOP
        row_y.append(y + BOX_H / 2)
        y += BOX_H + ROW_PAD_BOTTOM
        channel_y0[r] = y
        channel_h[r] = ch_height(r)
        y += channel_h[r]

    width = MARGIN * 2 + n_cols * PITCH + 40
    height = y + 50

    def track_y(ch: int, net: str) -> float:
        key = f"{net}@{ch}"
        return channel_y0[ch] + CHANNEL_PAD + tracks[ch][key] * TRACK_H

    # -- draw --------------------------------------------------------
    parts: list[str] = []
    segments: list[tuple[float, float, float, float, str]] = []

    def wire(x1, y1, x2, y2, net) -> bool:
        if abs(x1 - x2) < 0.05 and abs(y1 - y2) < 0.05:
            return False
        parts.append(
            f'<line class="w" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>'
        )
        segments.append((x1, y1, x2, y2, net))
        return True

    def junction(x, y_) -> None:
        parts.append(f'<circle class="jn" cx="{x:.1f}" cy="{y_:.1f}" r="2.6"/>')

    # devices
    for r, row in enumerate(rows):
        cy = row_y[r]
        for m in row:
            x = dev_x(m)
            is_p = m.kind == "m_p"
            color = "#b26a00" if is_p else "#1a5f9e"
            fill = "#fff7ec" if is_p else "#edf4ff"
            top = cy - BOX_H / 2
            bot = cy + BOX_H / 2
            parts.append(
                f'<rect x="{x - BOX_W/2:.1f}" y="{top:.1f}" width="{BOX_W}" height="{BOX_H}" '
                f'rx="5" fill="{fill}" stroke="{color}" stroke-width="1.3"/>'
            )
            parts.append(
                f'<text x="{x:.1f}" y="{cy - 2:.1f}" text-anchor="middle" class="dev" fill="{color}">'
                f"{html.escape(m.name)}</text>"
            )
            parts.append(
                f'<text x="{x:.1f}" y="{cy + 12:.1f}" text-anchor="middle" class="typ">'
                f'{"PMOS" if is_p else "NMOS"}</text>'
            )
            parts.append(
                f'<line x1="{x - BOX_W/2 - 10:.1f}" y1="{cy:.1f}" x2="{x - BOX_W/2:.1f}" '
                f'y2="{cy:.1f}" stroke="{color}" stroke-width="1.3"/>'
            )
            src = m.nodes[2]
            if src in SUPPLY_HIGH:
                parts.append(
                    f'<line class="sup" x1="{x:.1f}" y1="{top:.1f}" '
                    f'x2="{x:.1f}" y2="{top - 16:.1f}"/>'
                )
                parts.append(
                    f'<line class="sup" x1="{x-11:.1f}" y1="{top-16:.1f}" '
                    f'x2="{x+11:.1f}" y2="{top-16:.1f}"/>'
                )
                parts.append(
                    f'<text x="{x:.1f}" y="{top-21:.1f}" text-anchor="middle" class="sl">VDD</text>'
                )
            elif src in SUPPLY_LOW:
                parts.append(
                    f'<line class="sup" x1="{x:.1f}" y1="{top:.1f}" '
                    f'x2="{x:.1f}" y2="{top - 14:.1f}"/>'
                )
                for i, ww in enumerate((11, 7, 3)):
                    yy = top - 14 - i * 4
                    parts.append(
                        f'<line class="sup" x1="{x-ww:.1f}" y1="{yy:.1f}" '
                        f'x2="{x+ww:.1f}" y2="{yy:.1f}"/>'
                    )
                parts.append(
                    f'<text x="{x:.1f}" y="{top-30:.1f}" text-anchor="middle" class="sl">GND</text>'
                )

    # trunks - only real attachment spans (no fake stubs)
    label_boxes: list[tuple[float, float, float, float]] = []
    label_overlaps = 0
    placed_labels = 0
    pending_labels: list[tuple[str, float, float, float, float, str]] = []

    for net in signal_nets:
        for ch, (lo, hi) in net_span[net].items():
            ty = track_y(ch, net)
            xs = sorted({round(x, 1) for x in attachments[net][ch]})
            if len(xs) >= 2:
                for a, b in zip(xs, xs[1:]):
                    if b - a > 0.05:
                        wire(a, ty, b, ty, net)

            if ch == min(net_span[net]):
                cls = net_class.get(net, "nl")
                prefer = xs[0] if xs else lo
                pending_labels.append((net, prefer, ty, lo, hi if hi > lo else lo + 1, cls))
                if is_output[net]:
                    ox = pin_attach[net][0][3]
                    parts.append(
                        f'<circle cx="{ox:.1f}" cy="{ty:.1f}" r="3.4" fill="#fff" '
                        f'stroke="#c0392b" stroke-width="1.6"/>'
                    )

    # place labels bottom-to-top, left-to-right for stable packing
    pending_labels.sort(key=lambda t: (t[2], t[1], t[0]))
    for net, prefer_x, ty, span_lo, span_hi, cls in pending_labels:
        tw = label_width(net)
        th = LABEL_H
        base_x = prefer_x + 4.0
        span_mid = (span_lo + span_hi) / 2.0
        cands: list[tuple[float, float]] = []
        for dx in range(0, 161, 8):
            for sign in (1, -1):
                if dx == 0 and sign < 0:
                    continue
                for dy in (0, -10, 11, -20, 20, -30):
                    cands.append((base_x + sign * dx, ty - 3 + dy))
                    cands.append((span_mid - tw / 2 + sign * dx, ty - 3 + dy))
        chosen = None
        for lx, ly in cands:
            box = (lx, ly - th + 2, lx + tw, ly + 2)
            if any(boxes_overlap(box, ob, pad=1.5) for ob in label_boxes):
                continue
            if abs(ly - (ty - 3)) > 32:
                continue
            chosen = (lx, ly, box)
            break
        if chosen is None:
            # last resort: stack above track with unique vertical offset from collisions
            ly = ty - 3
            for _ in range(40):
                lx = base_x
                box = (lx, ly - th + 2, lx + tw, ly + 2)
                if not any(boxes_overlap(box, ob, pad=1.5) for ob in label_boxes):
                    chosen = (lx, ly, box)
                    break
                ly -= 9
            if chosen is None:
                lx, ly = base_x, ty - 40
                box = (lx, ly - th + 2, lx + tw, ly + 2)
                label_overlaps += 1
                chosen = (lx, ly, box)
        lx, ly, box = chosen
        label_boxes.append(box)
        placed_labels += 1
        parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" class="{cls}">{html.escape(net)}</text>'
        )

    # pin drops
    for net, pins in pin_attach.items():
        for m, pin, ch, ex in pins:
            cy = row_y[row_of[m.name]]
            ty = track_y(ch, net)
            if pin == "D":
                pin_y = cy + BOX_H / 2
            elif pin == "G":
                pin_y = cy
            else:
                pin_y = cy - BOX_H / 2
            if wire(ex, pin_y, ex, ty, net):
                lo, hi = net_span[net][ch]
                # junction only where a real horizontal trunk exists (T-junction)
                if hi - lo > 0.5 and lo - 0.6 <= ex <= hi + 0.6:
                    junction(ex, ty)

    # inter-channel lanes
    for net, verts in verticals.items():
        for lane_x, c_lo, c_hi in verts:
            y1 = track_y(c_lo, net)
            y2 = track_y(c_hi, net)
            if wire(lane_x, y1, lane_x, y2, net):
                for ch, yy in ((c_lo, y1), (c_hi, y2)):
                    lo, hi = net_span[net][ch]
                    if hi - lo > 0.5 and lo - 0.6 <= lane_x <= hi + 0.6:
                        junction(lane_x, yy)

    # -- validation --------------------------------------------------
    def endpoint_hit(px, py, tol=2.0) -> bool:
        for x1, y1, x2, y2, _ in segments:
            if (abs(x1 - px) <= tol and abs(y1 - py) <= tol) or (
                abs(x2 - px) <= tol and abs(y2 - py) <= tol
            ):
                return True
            if abs(x1 - x2) < 0.05 and abs(x1 - px) <= tol:
                if min(y1, y2) - tol <= py <= max(y1, y2) + tol:
                    return True
            if abs(y1 - y2) < 0.05 and abs(y1 - py) <= tol:
                if min(x1, x2) - tol <= px <= max(x1, x2) + tol:
                    return True
        return False

    missing_pins: list[str] = []
    for m in mos:
        x = dev_x(m)
        cy = row_y[row_of[m.name]]
        for pin, px, py, net in (
            ("D", x, cy + BOX_H / 2, m.nodes[0]),
            ("G", x - BOX_W / 2 - 10, cy, m.nodes[1]),
            ("S", x, cy - BOX_H / 2, m.nodes[2]),
        ):
            if net in RAILS:
                continue
            if not endpoint_hit(px, py):
                missing_pins.append(f"{m.name}.{pin}({net})")

    # per-net geometric connectivity (T-junction aware)
    from collections import deque

    segs_by_net: dict[str, list] = defaultdict(list)
    for s in segments:
        segs_by_net[s[4]].append(s)

    def _on_seg(px, py, x1, y1, x2, y2, tol=1.2) -> bool:
        if abs(x1 - x2) < 0.05:
            return abs(px - x1) <= tol and min(y1, y2) - tol <= py <= max(y1, y2) + tol
        if abs(y1 - y2) < 0.05:
            return abs(py - y1) <= tol and min(x1, x2) - tol <= px <= max(x1, x2) + tol
        return False

    def _net_ok(net: str, pins) -> bool:
        segs = segs_by_net.get(net, ())
        if not pins:
            return True
        nodes: dict[tuple[float, float], int] = {}
        adj: dict[int, set[int]] = defaultdict(set)

        def nid(p):
            key = (round(p[0], 1), round(p[1], 1))
            if key not in nodes:
                nodes[key] = len(nodes)
            return nodes[key]

        def link(a, b):
            if a != b:
                adj[a].add(b)
                adj[b].add(a)

        for x1, y1, x2, y2, _ in segs:
            link(nid((x1, y1)), nid((x2, y2)))

        # bridge T-junctions / crossings within this net
        horiz = [(s[0], s[1], s[2], s[3]) for s in segs if abs(s[1] - s[3]) < 0.05]
        vert = [(s[0], s[1], s[2], s[3]) for s in segs if abs(s[0] - s[2]) < 0.05]
        for hx1, hy, hx2, _ in horiz:
            lo, hi = min(hx1, hx2), max(hx1, hx2)
            for vx, vy1, _, vy2 in vert:
                if lo - 0.6 <= vx <= hi + 0.6 and min(vy1, vy2) - 0.6 <= hy <= max(vy1, vy2) + 0.6:
                    j = nid((vx, hy))
                    link(j, nid((hx1, hy)))
                    link(j, nid((hx2, hy)))
                    link(j, nid((vx, vy1)))
                    link(j, nid((vx, vy2)))

        def find_node(px, py, tol=3.0):
            best = None
            best_d = tol
            for (x, y), i in nodes.items():
                d = abs(x - px) + abs(y - py)
                if d < best_d:
                    best_d, best = d, i
            if best is not None:
                return best
            for x1, y1, x2, y2, _ in segs:
                if _on_seg(px, py, x1, y1, x2, y2, tol=1.5):
                    j = nid((px, py))
                    link(j, nid((x1, y1)))
                    link(j, nid((x2, y2)))
                    return j
            return None

        comps = set()
        for m, pin, ch, ex in pins:
            cy = row_y[row_of[m.name]]
            if pin == "D":
                py = cy + BOX_H / 2
            elif pin == "G":
                py = cy
            else:
                py = cy - BOX_H / 2
            i = find_node(ex, py)
            if i is None:
                return False
            seen = {i}
            q = deque([i])
            while q:
                u = q.popleft()
                for v in adj[u]:
                    if v not in seen:
                        seen.add(v)
                        q.append(v)
            comps.add(frozenset(seen))
        return len(comps) <= 1

    split_nets = 0
    split_samples: list[str] = []
    for net, pins in pin_attach.items():
        if not _net_ok(net, pins):
            split_nets += 1
            if len(split_samples) < 12:
                split_samples.append(net)

    # dangling horizontal stubs: H endpoint with no same-net vertical touching it
    dangling_stubs = 0
    for net, nsegs in segs_by_net.items():
        verts = [(s[0], s[1], s[2], s[3]) for s in nsegs if abs(s[0] - s[2]) < 0.05]
        for x1, y1, x2, y2, _ in nsegs:
            if abs(y1 - y2) >= 0.05:
                continue
            for px, py in ((x1, y1), (x2, y2)):
                touched = any(
                    abs(vx - px) <= 0.8 and min(vy1, vy2) - 0.8 <= py <= max(vy1, vy2) + 0.8
                    for vx, vy1, _, vy2 in verts
                )
                if not touched:
                    dangling_stubs += 1

    # dangling vertical far-ends: every V endpoint must be a pin, an H T-junction,
    # or a deliberate point-net / output tip on its track.
    dangling_v_ends = 0
    dangling_v_samples: list[str] = []
    pin_pts_by_net: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for net, pins in pin_attach.items():
        for m, pin, ch, ex in pins:
            cy = row_y[row_of[m.name]]
            if pin == "D":
                py = cy + BOX_H / 2
            elif pin == "G":
                py = cy
            else:
                py = cy - BOX_H / 2
            pin_pts_by_net[net].append((ex, py))

    track_pts_by_net: dict[str, list[tuple[float, float, bool]]] = defaultdict(list)
    # (x, y, is_point_channel) for every attachment on every channel
    for net in signal_nets:
        for ch, xs in attachments[net].items():
            ty = track_y(ch, net)
            lo, hi = net_span[net][ch]
            is_point = (hi - lo) < 0.5
            for x in xs:
                track_pts_by_net[net].append((x, ty, is_point))

    for net, nsegs in segs_by_net.items():
        horiz = [(s[0], s[1], s[2], s[3]) for s in nsegs if abs(s[1] - s[3]) < 0.05]
        for x1, y1, x2, y2, _ in nsegs:
            if abs(x1 - x2) >= 0.05:
                continue
            x = x1
            for y in (y1, y2):
                on_h = any(
                    abs(hy - y) <= 0.8 and min(hx1, hx2) - 0.8 <= x <= max(hx1, hx2) + 0.8
                    for hx1, hy, hx2, _ in horiz
                )
                on_pin = any(
                    abs(px - x) <= 1.5 and abs(py - y) <= 1.5
                    for px, py in pin_pts_by_net.get(net, ())
                )
                on_track_tip = any(
                    abs(tx - x) <= 0.8 and abs(ty - y) <= 0.8 and is_pt
                    for tx, ty, is_pt in track_pts_by_net.get(net, ())
                )
                if not on_h and not on_pin and not on_track_tip:
                    dangling_v_ends += 1
                    if len(dangling_v_samples) < 15:
                        dangling_v_samples.append(f"{net}@({x:.0f},{y:.0f})")

    stats = {
        "devices": len(mos),
        "rows": len(rows),
        "nets": len(signal_nets),
        "segments": len(segments),
        "crossings": 0,
        "missing_pins": len(missing_pins),
        "missing_pin_samples": missing_pins[:12],
        "split_nets": split_nets,
        "split_samples": split_samples,
        "label_overlaps": label_overlaps,
        "labels": placed_labels,
        "dangling_stubs": dangling_stubs,
        "dangling_v_ends": dangling_v_ends,
        "dangling_v_samples": dangling_v_samples,
    }

    # crossing count
    horiz = [s for s in segments if abs(s[1] - s[3]) < 0.05]
    vert = [s for s in segments if abs(s[0] - s[2]) < 0.05]
    crossings = 0
    for hx1, hy, hx2, _, hn in horiz:
        lo, hi = min(hx1, hx2), max(hx1, hx2)
        for vx, vy1, _, vy2, vn in vert:
            if vn == hn:
                continue
            if lo + 0.5 < vx < hi - 0.5 and min(vy1, vy2) + 0.5 < hy < max(vy1, vy2) - 0.5:
                crossings += 1
    stats["crossings"] = crossings

    n_in = sum(1 for n in PRIMARY_INPUTS if n in net_class)
    n_out = sum(1 for n in PRIMARY_OUTPUTS if n in net_class)
    style = (
        ".w{stroke:#3b4453;stroke-width:1.2;fill:none}"
        ".sup{stroke:#111;stroke-width:2}"
        ".jn{fill:#3b4453}"
        ".dev{font:700 11px Segoe UI,Arial}"
        ".typ{font:7.5px Segoe UI,Arial;fill:#7a8190}"
        ".nl{font:7.5px Consolas,monospace;fill:#59617a}"
        ".in{font:700 8px Consolas,monospace;fill:#1a5f9e}"
        ".out{font:700 8px Consolas,monospace;fill:#c0392b}"
        ".sl{font:700 7.5px Segoe UI,Arial;fill:#111}"
        ".title{font:700 24px Segoe UI,Arial;fill:#111}"
        ".sub{font:14px Segoe UI,Arial;fill:#555}"
        ".note{font:12px Segoe UI,Arial;fill:#777}"
        ".ports{font:700 12px Consolas,monospace;fill:#1a5f9e}"
        ".porto{font:700 12px Consolas,monospace;fill:#c0392b}"
    )
    in_list = ", ".join(PRIMARY_INPUTS)
    out_list = ", ".join(PRIMARY_OUTPUTS)
    head = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}">',
        f"<style>{style}</style>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text class="title" x="{MARGIN}" y="40">{html.escape(circuit.title)}</text>',
        f'<text class="sub" x="{MARGIN}" y="64">'
        f"{len(mos)} transistors | {len(signal_nets)} signal nets | "
        f"{n_in} primary inputs | {n_out} primary outputs</text>",
        f'<text class="ports" x="{MARGIN}" y="88">IN ({n_in}): {html.escape(in_list)}</text>',
        f'<text class="porto" x="{MARGIN}" y="108">OUT ({n_out}): {html.escape(out_list)}</text>',
        f'<text class="note" x="{MARGIN}" y="128">'
        f"Drain exits down, gate/source enter from above. "
        f"Dots mark junctions. Red open circles = primary outputs. "
        f"VDD/GND are local symbols.</text>",
    ]

    out_svg = Path(out_svg)
    out_svg.parent.mkdir(parents=True, exist_ok=True)
    out_svg.write_text("\n".join(head + parts) + "\n</svg>\n", encoding="utf-8")
    return out_svg, stats
