"""
Parse a subset of SPICE / Micro-Cap style .CIR netlists.

Supported: R C L V I Q M J D X, comments (* ;), + continuations.
Directives (.model, .end, ...) ignored for drawing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass
class Component:
    name: str
    kind: str
    nodes: list[str]
    value: str = ""
    params: dict[str, str] = field(default_factory=dict)


@dataclass
class Circuit:
    title: str
    components: list[Component]
    nets: set[str]


def _is_comment(line: str) -> bool:
    s = line.lstrip()
    return not s or s.startswith("*") or s.startswith(";")


def _is_directive(line: str) -> bool:
    return line.lstrip().startswith(".")


def _join_continuations(lines: Iterable[str]) -> list[str]:
    out: list[str] = []
    buf = ""
    for raw in lines:
        line = raw.rstrip("\n\r")
        if _is_comment(line) and not buf:
            continue
        stripped = line.lstrip()
        if stripped.startswith("+"):
            buf += " " + stripped[1:].strip()
            continue
        if buf:
            out.append(buf)
        buf = line.strip()
    if buf:
        out.append(buf)
    return out


def _normalize_node(n: str) -> str:
    n = n.strip()
    if n in {"0", "gnd", "GND", "ground", "GROUND"}:
        return "0"
    return n


def _parse_params(tokens: list[str]) -> tuple[str, dict[str, str]]:
    value = ""
    params: dict[str, str] = {}
    for t in tokens:
        if "=" in t:
            k, _, v = t.partition("=")
            params[k.lower()] = v
        elif not value:
            value = t
        else:
            params.setdefault("model", t)
    return value, params


def _classify(name: str, nodes: list[str], value: str, params: dict[str, str]) -> str:
    prefix = name[0].upper()
    model = (params.get("model") or value or "").lower()
    upper = name.upper()

    # Synthesizer quirk: "GND GND 0 0V" uses G-prefix but is a rail
    if upper in {"GND", "VSS", "VDD", "VCC"} and len(nodes) >= 2:
        return "v"

    if prefix == "R":
        return "r"
    if prefix == "C":
        return "c"
    if prefix == "L":
        return "l"
    if prefix == "V":
        return "v"
    if prefix == "I":
        return "i"
    if prefix == "D":
        return "d"
    if prefix == "Q":
        return "q_pnp" if "pnp" in model else "q_npn"
    if prefix == "M":
        if "pmos" in model or model.startswith("p"):
            return "m_p"
        return "m_n"
    if prefix == "J":
        return "j_p" if "pjf" in model else "j_n"
    if prefix == "X":
        return "x"
    if prefix in {"E", "H"}:
        return "v"
    if prefix in {"F", "G"}:
        return "i"
    return "r" if len(nodes) < 3 else "q_npn"


def parse_cir_text(text: str) -> Circuit:
    lines = _join_continuations(text.splitlines())
    title = "Circuit"
    components: list[Component] = []
    nets: set[str] = set()

    for line in lines:
        if not line or _is_comment(line):
            continue
        if _is_directive(line):
            low = line.lower()
            if low.startswith(".title") and " " in line:
                title = line.split(None, 1)[1]
            continue

        tokens = line.replace("(", " ").replace(")", " ").split()
        if not tokens or not tokens[0][0].isalpha():
            continue

        name = tokens[0]
        prefix = name[0].upper()
        rest = tokens[1:]
        name_u = name.upper()

        if name_u in {"GND", "VSS", "VDD", "VCC"} and len(rest) >= 2:
            n_pins = 2
        elif prefix in {"R", "C", "L", "V", "I", "D"}:
            n_pins = 2
        elif prefix in {"Q", "J"}:
            n_pins = 3
        elif prefix == "M":
            n_pins = 4 if len(rest) >= 4 else 3
        elif prefix == "X":
            n_pins = max(2, len(rest) - 1)
        elif prefix in {"E", "G"}:
            n_pins = 4 if len(rest) >= 4 else 2
        elif prefix in {"F", "H"}:
            n_pins = 2
        else:
            n_pins = 2

        if len(rest) < n_pins:
            continue

        # Keep literal VDD/GND net names for MOSFET bulk/rails; map only bare 0
        raw_nodes = rest[:n_pins]
        nodes = []
        for n in raw_nodes:
            n = n.strip()
            if n == "0":
                nodes.append("0")
            else:
                nodes.append(n)

        value, params = _parse_params(rest[n_pins:])
        # MOSFET model is usually first positional after pins
        if prefix == "M" and value and "model" not in params:
            params["model"] = value

        kind = _classify(name, nodes, value, params)
        components.append(
            Component(name=name, kind=kind, nodes=nodes, value=value, params=params)
        )
        nets.update(nodes)

    # Title from first comment if present
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("*") and len(s) > 2:
            title = s.lstrip("* ").strip()
            break

    return Circuit(title=title, components=components, nets=nets)


def parse_cir_file(path: str | Path) -> Circuit:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    circuit = parse_cir_text(text)
    if not circuit.title:
        circuit.title = path.stem
    return circuit
