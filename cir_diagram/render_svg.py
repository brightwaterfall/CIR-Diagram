"""
Render large MOSFET netlists as SVG using NetworkX + Graphviz-style layout
(or spring layout fallback). Optimized for flat CMOS/PTL circuits.
"""

from __future__ import annotations

import html
import math
from collections import defaultdict
from pathlib import Path

from .spice_parser import Circuit, Component


RAILS = {"0", "GND", "VDD", "VSS", "VCC"}


def _mosfets(circuit: Circuit) -> list[Component]:
    return [c for c in circuit.components if c.kind in {"m_n", "m_p"}]


def _try_graphviz_layout(G):
    try:
        from networkx.drawing.nx_pydot import graphviz_layout

        return graphviz_layout(G, prog="dot")
    except Exception:
        pass
    try:
        from networkx.drawing.nx_agraph import graphviz_layout

        return graphviz_layout(G, prog="dot")
    except Exception:
        return None


def _grid_layout(G):
    """Deterministic layout without numpy/scipy (Windows-friendly)."""
    mos = [n for n, d in G.nodes(data=True) if d.get("ntype") == "mos"]
    nets = [n for n, d in G.nodes(data=True) if d.get("ntype") == "net"]
    rails = [n for n, d in G.nodes(data=True) if d.get("ntype") == "rail"]

    def mos_key(name: str):
        digits = "".join(ch for ch in name if ch.isdigit())
        return int(digits) if digits else 0

    mos = sorted(mos, key=mos_key)
    cols = max(1, int(math.ceil(math.sqrt(max(len(mos), 1)) * 1.4)))
    spacing_x, spacing_y = 90.0, 70.0
    pos: dict = {}

    for i, name in enumerate(mos):
        r, c = divmod(i, cols)
        pos[name] = (c * spacing_x, -r * spacing_y)

    # Place net nodes near the average of connected MOSFET positions
    for net in nets:
        nbrs = [n for n in G.neighbors(net) if n in pos]
        if nbrs:
            ax = sum(pos[n][0] for n in nbrs) / len(nbrs)
            ay = sum(pos[n][1] for n in nbrs) / len(nbrs)
            pos[net] = (ax + 28.0, ay + 18.0)
        else:
            pos[net] = (0.0, 40.0)

    width = (cols - 1) * spacing_x if cols else 0
    for i, rail in enumerate(rails):
        pos[rail] = (width * (0.2 + 0.6 * (i / max(len(rails), 1))), 80.0)

    # Ensure every node has a position
    for n in G.nodes():
        if n not in pos:
            pos[n] = (0.0, 0.0)
    return pos


def _spring_layout(G, seed: int = 42):
    import networkx as nx

    try:
        k = 1.5 / math.sqrt(max(G.number_of_nodes(), 1))
        return nx.spring_layout(G, k=k, iterations=80, seed=seed, scale=1000)
    except Exception:
        try:
            return nx.circular_layout(G, scale=1000)
        except Exception:
            return _grid_layout(G)


def build_graph(circuit: Circuit, hide_rails: bool = True):
    import networkx as nx

    G = nx.Graph()
    mos = _mosfets(circuit)

    for m in mos:
        label = f"{m.name}\n{'PMOS' if m.kind == 'm_p' else 'NMOS'}"
        G.add_node(m.name, ntype="mos", kind=m.kind, label=label)

    # Signal nets (optionally hide pure rails to reduce clutter)
    net_members: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for m in mos:
        d, g, s = m.nodes[0], m.nodes[1], m.nodes[2]
        bulk = m.nodes[3] if len(m.nodes) > 3 else s
        for pin, net in (("D", d), ("G", g), ("S", s), ("B", bulk)):
            if hide_rails and net in RAILS:
                continue
            net_members[net].append((m.name, pin))

    for net, members in net_members.items():
        if not members:
            continue
        G.add_node(net, ntype="net", kind="net", label=net)
        for mos_name, pin in members:
            G.add_edge(mos_name, net, pin=pin)

    # Always show VDD/GND as rail hubs with light connections
    for rail in ("VDD", "GND", "0"):
        connected = False
        for m in mos:
            if rail in m.nodes or (rail == "0" and "GND" in m.nodes):
                connected = True
                break
        if not connected:
            continue
        rname = "GND" if rail == "0" else rail
        if rname not in G:
            G.add_node(rname, ntype="rail", kind="rail", label=rname)
        for m in mos:
            nodes = m.nodes
            if rail in nodes or (rname in nodes):
                # only attach if source/bulk typically
                if nodes[2] in {rail, rname} or (
                    len(nodes) > 3 and nodes[3] in {rail, rname}
                ):
                    G.add_edge(m.name, rname, pin="rail")

    return G


def render_svg(circuit: Circuit, out_svg: Path, hide_rails: bool = True) -> Path:
    import networkx as nx

    out_svg = Path(out_svg)
    out_svg.parent.mkdir(parents=True, exist_ok=True)

    G = build_graph(circuit, hide_rails=hide_rails)
    if G.number_of_nodes() == 0:
        out_svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><text x="20" y="40">Empty circuit</text></svg>',
            encoding="utf-8",
        )
        return out_svg

    pos = _try_graphviz_layout(G)
    if not pos:
        # Prefer fast deterministic grid for large MOSFET netlists
        if G.number_of_nodes() > 120:
            pos = _grid_layout(G)
        else:
            pos = _spring_layout(G)

    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    pad = 80
    width = max(max_x - min_x, 1) + 2 * pad
    height = max(max_y - min_y, 1) + 2 * pad

    def tx(x, y):
        return (x - min_x + pad, max_y - y + pad)  # flip Y for SVG

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}">',
        "<style>",
        "  .title { font: 700 22px Segoe UI, Arial, sans-serif; fill: #1a1a1a; }",
        "  .sub { font: 12px Segoe UI, Arial, sans-serif; fill: #555; }",
        "  .mos-p { fill: #ffe8cc; stroke: #c47a00; stroke-width: 1.6; }",
        "  .mos-n { fill: #dceeff; stroke: #1a5f9e; stroke-width: 1.6; }",
        "  .net { fill: #f4f4f4; stroke: #666; stroke-width: 1; }",
        "  .rail { fill: #eee; stroke: #333; stroke-width: 2; }",
        "  .lbl { font: 9px Consolas, monospace; fill: #222; text-anchor: middle; }",
        "  .wire { stroke: #444; stroke-width: 1.1; fill: none; opacity: 0.75; }",
        "</style>",
        f'<rect width="100%" height="100%" fill="#fafafa"/>',
        f'<text class="title" x="{pad}" y="36">{html.escape(circuit.title)}</text>',
        f'<text class="sub" x="{pad}" y="56">'
        f"{sum(1 for c in circuit.components if c.kind=='m_p')} PMOS | "
        f"{sum(1 for c in circuit.components if c.kind=='m_n')} NMOS | "
        f"{len(circuit.nets)} nets</text>",
    ]

    # Edges
    for u, v in G.edges():
        x1, y1 = tx(*pos[u])
        x2, y2 = tx(*pos[v])
        parts.append(
            f'<line class="wire" x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>'
        )

    # Nodes
    for node, data in G.nodes(data=True):
        x, y = tx(*pos[node])
        ntype = data.get("ntype", "net")
        label = html.escape(str(data.get("label", node)).replace("\n", " "))
        if ntype == "mos":
            cls = "mos-p" if data.get("kind") == "m_p" else "mos-n"
            w, h = 54, 28
            parts.append(
                f'<rect class="{cls}" x="{x - w/2:.1f}" y="{y - h/2:.1f}" '
                f'width="{w}" height="{h}" rx="4"/>'
            )
            parts.append(f'<text class="lbl" x="{x:.1f}" y="{y + 3:.1f}">{label}</text>')
        elif ntype == "rail":
            parts.append(f'<rect class="rail" x="{x-28:.1f}" y="{y-12:.1f}" width="56" height="24" rx="3"/>')
            parts.append(f'<text class="lbl" x="{x:.1f}" y="{y + 3:.1f}">{label}</text>')
        else:
            parts.append(f'<circle class="net" cx="{x:.1f}" cy="{y:.1f}" r="10"/>')
            # only label short / important nets to reduce clutter
            if len(str(node)) <= 14 or str(node).startswith(("A", "B", "Y", "Cn", "M")):
                parts.append(
                    f'<text class="lbl" x="{x:.1f}" y="{y - 14:.1f}">{html.escape(str(node))}</text>'
                )

    parts.append("</svg>")
    out_svg.write_text("\n".join(parts), encoding="utf-8")
    return out_svg


def render_html_report(circuit: Circuit, svg_path: Path, out_html: Path) -> Path:
    mos = _mosfets(circuit)
    rows = []
    for m in mos:
        d, g, s = m.nodes[0], m.nodes[1], m.nodes[2]
        b = m.nodes[3] if len(m.nodes) > 3 else ""
        typ = "PMOS" if m.kind == "m_p" else "NMOS"
        w = m.params.get("w", "")
        L = m.params.get("l", "")
        rows.append(
            f"<tr><td>{html.escape(m.name)}</td><td>{typ}</td>"
            f"<td>{html.escape(d)}</td><td>{html.escape(g)}</td>"
            f"<td>{html.escape(s)}</td><td>{html.escape(b)}</td>"
            f"<td>{html.escape(w)}</td><td>{html.escape(L)}</td></tr>"
        )

    svg_rel = svg_path.name
    out_html = Path(out_html)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(
        f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>{html.escape(circuit.title)}</title>
<style>
body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; background: #111; color: #eee; }}
h1 {{ font-size: 1.4rem; }}
.meta {{ color: #aaa; margin-bottom: 1rem; }}
.frame {{ background: #fff; border-radius: 8px; overflow: auto; max-height: 80vh; }}
table {{ border-collapse: collapse; width: 100%; font-size: 12px; margin-top: 24px; }}
th, td {{ border: 1px solid #333; padding: 4px 8px; }}
th {{ background: #222; position: sticky; top: 0; }}
tr:nth-child(even) {{ background: #1a1a1a; }}
</style></head><body>
<h1>{html.escape(circuit.title)}</h1>
<p class="meta">{len(mos)} MOSFETs | {len(circuit.nets)} nets | zoom/scroll the diagram</p>
<div class="frame"><img src="{html.escape(svg_rel)}" alt="circuit diagram"/></div>
<h2>Device table</h2>
<table>
<thead><tr><th>Name</th><th>Type</th><th>Drain</th><th>Gate</th><th>Source</th><th>Bulk</th><th>W</th><th>L</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody></table>
</body></html>
""",
        encoding="utf-8",
    )
    return out_html


def write_stats(circuit: Circuit, path: Path) -> Path:
    mos = _mosfets(circuit)
    p = sum(1 for m in mos if m.kind == "m_p")
    n = sum(1 for m in mos if m.kind == "m_n")
    inputs = sorted(
        {
            net
            for net in circuit.nets
            if net
            in {
                "A0",
                "A1",
                "A2",
                "A3",
                "B0",
                "B1",
                "B2",
                "B3",
                "A0_b",
                "A1_b",
                "A2_b",
                "A3_b",
                "B0_b",
                "B1_b",
                "B2_b",
                "B3_b",
                "Cn",
                "M",
                "M_b",
            }
            or net.endswith("_b")
            and len(net) <= 5
        }
    )
    path = Path(path)
    path.write_text(
        "\n".join(
            [
                f"Title: {circuit.title}",
                f"MOSFETs: {len(mos)} (PMOS={p}, NMOS={n})",
                f"Nets: {len(circuit.nets)}",
                f"Primary I/O-like nets: {', '.join(inputs) if inputs else '(see table)'}",
                f"Output candidate: Y_BUFFERED_OUT"
                if "Y_BUFFERED_OUT" in circuit.nets
                else "",
            ]
        ),
        encoding="utf-8",
    )
    return path
