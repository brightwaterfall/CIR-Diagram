"""
Convert parsed SPICE circuit -> Yosys-like JSON for netlistsvg (analog skin).

netlistsvg cell types (analog.svg) include:
  r_v, r_h, c_v, c_h, l_v, l_h, v, i, d, q_npn, q_pnp, gnd, vcc, ...
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .spice_parser import Circuit, Component


# Map our kinds -> netlistsvg analog skin types + pin order
_PIN_MAP: dict[str, tuple[str, list[str]]] = {
    "r": ("r_v", ["A", "B"]),
    "c": ("c_v", ["A", "B"]),
    "l": ("l_v", ["A", "B"]),
    "v": ("v", ["+", "-"]),
    "i": ("i_v", ["A", "B"]),
    "d": ("d", ["A", "C"]),  # anode, cathode - skin may use A/C or +/-
    "q_npn": ("q_npn", ["C", "B", "E"]),
    "q_pnp": ("q_pnp", ["C", "B", "E"]),
    "m_n": ("nmos", ["D", "G", "S"]),
    "m_p": ("pmos", ["D", "G", "S"]),
    "j_n": ("q_npn", ["C", "B", "E"]),  # fallback symbol
    "j_p": ("q_pnp", ["C", "B", "E"]),
    "gnd": ("gnd", ["A"]),
    "x": ("generic", ["A", "B"]),
}


def _net_ids(circuit: Circuit) -> dict[str, int]:
    # Stable numeric IDs required by netlistsvg connections
    names = sorted(circuit.nets, key=lambda n: (n != "0", n))
    return {name: i for i, name in enumerate(names)}


def component_to_cell(comp: Component, net_id: dict[str, int]) -> dict[str, Any] | None:
    if comp.kind not in _PIN_MAP:
        return None
    skin_type, pins = _PIN_MAP[comp.kind]
    nodes = list(comp.nodes)

    # MOSFET often has 4 nodes (D G S B) - drop bulk for 3-pin symbol
    if comp.kind in {"m_n", "m_p"} and len(nodes) >= 3:
        nodes = nodes[:3]

    if len(nodes) < len(pins):
        return None

    connections = {pin: [net_id[nodes[i]]] for i, pin in enumerate(pins)}
    cell: dict[str, Any] = {
        "type": skin_type,
        "connections": connections,
        "attributes": {},
    }
    if comp.value:
        cell["attributes"]["value"] = comp.value
    return cell


def circuit_to_netlistsvg_json(circuit: Circuit) -> dict[str, Any]:
    net_id = _net_ids(circuit)
    cells: dict[str, Any] = {}

    for comp in circuit.components:
        cell = component_to_cell(comp, net_id)
        if cell is None:
            continue
        cells[comp.name] = cell

    # Add VCC markers for nets named vcc/vdd/+v
    for name, nid in net_id.items():
        low = name.lower()
        if low in {"vcc", "vdd", "+v", "v+", "vp"}:
            cells[f"vcc_{name}"] = {
                "type": "vcc",
                "connections": {"A": [nid]},
                "attributes": {},
            }

    return {
        "modules": {
            circuit.title: {
                "ports": {},
                "cells": cells,
            }
        }
    }


def write_netlist_json(circuit: Circuit, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = circuit_to_netlistsvg_json(circuit)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
