"""Render circuit with SchemDraw (pure Python fallback, no Node.js)."""

from __future__ import annotations

from pathlib import Path

from .spice_parser import Circuit, Component


def _try_import_schemdraw():
    import schemdraw
    import schemdraw.elements as elm

    return schemdraw, elm


def render_schemdraw(circuit: Circuit, out_svg: str | Path) -> Path:
    """
    Simple auto layout: place transistors in the center column, passives around.
    Good enough for small educational transistor circuits.
    """
    schemdraw, elm = _try_import_schemdraw()
    out_svg = Path(out_svg)
    out_svg.parent.mkdir(parents=True, exist_ok=True)

    actives = [c for c in circuit.components if c.kind.startswith(("q_", "m_", "j_"))]
    passives = [
        c
        for c in circuit.components
        if c.kind in {"r", "c", "l", "v", "i", "d"} and not c.name.startswith("gnd")
    ]

    d = schemdraw.Drawing(unit=2.5)
    d.config(fontsize=11)

    # Title
    d += elm.Label().label(circuit.title, loc="center", fontsize=14).at((0, 4))

    y = 2.0
    x_active = 0.0

    def draw_bjt(comp: Component, xy):
        # nodes: C B E
        if comp.kind == "q_pnp":
            q = elm.BjtPnp().at(xy).label(comp.name, loc="right")
        else:
            q = elm.BjtNpn().at(xy).label(comp.name, loc="right")
        d.add(q)
        return q

    def draw_mos(comp: Component, xy):
        if comp.kind == "m_p":
            m = elm.PFet().at(xy).label(comp.name, loc="right")
        else:
            m = elm.NFet().at(xy).label(comp.name, loc="right")
        d.add(m)
        return m

    placed = 0
    for comp in actives:
        xy = (x_active, y - placed * 3.2)
        if comp.kind.startswith("q_"):
            draw_bjt(comp, xy)
        else:
            draw_mos(comp, xy)
        placed += 1

    # Place passives in a left column with labels (net connectivity as text)
    px, py = -4.5, 2.0
    for i, comp in enumerate(passives):
        label = f"{comp.name}"
        if comp.value:
            label += f"={comp.value}"
        nets = "-".join(comp.nodes)
        xy = (px, py - i * 1.4)

        if comp.kind == "r":
            d += elm.Resistor().at(xy).theta(-90).label(label).label(nets, loc="bottom", fontsize=9)
        elif comp.kind == "c":
            d += elm.Capacitor().at(xy).theta(-90).label(label).label(nets, loc="bottom", fontsize=9)
        elif comp.kind == "l":
            d += elm.Inductor2().at(xy).theta(-90).label(label).label(nets, loc="bottom", fontsize=9)
        elif comp.kind == "v":
            d += elm.SourceV().at(xy).theta(-90).label(label).label(nets, loc="bottom", fontsize=9)
        elif comp.kind == "i":
            d += elm.SourceI().at(xy).theta(-90).label(label).label(nets, loc="bottom", fontsize=9)
        elif comp.kind == "d":
            d += elm.Diode().at(xy).theta(-90).label(label).label(nets, loc="bottom", fontsize=9)

    # Ground symbol
    if any(c.kind == "gnd" for c in circuit.components):
        d += elm.Ground().at((x_active, y - max(placed, 1) * 3.2 - 0.5))

    # Net legend
    legend = "Nets: " + ", ".join(sorted(circuit.nets, key=lambda n: (n != "0", n)))
    d += elm.Label().label(legend, loc="center", fontsize=9).at((0, y - max(placed, 1) * 3.2 - 2.0))

    d.save(str(out_svg))
    return out_svg
