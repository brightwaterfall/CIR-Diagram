"""Locate netlistsvg and run it to produce an SVG."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def find_netlistsvg() -> str | None:
    exe = shutil.which("netlistsvg")
    if exe:
        return exe
    # npx fallback
    if shutil.which("npx"):
        return "npx"
    return None


def find_analog_skin(search_roots: list[Path] | None = None) -> Path | None:
    """Find analog.svg shipped with netlistsvg package."""
    candidates: list[Path] = []
    roots = search_roots or []

    # Local project copy
    here = Path(__file__).resolve().parent.parent
    candidates.append(here / "skins" / "analog.svg")

    # npm global / local node_modules
    for base in [
        here / "node_modules" / "netlistsvg",
        here / "node_modules" / "@nturley" / "netlistsvg",
        Path.home() / "AppData" / "Roaming" / "npm" / "node_modules" / "netlistsvg",
    ]:
        candidates.append(base / "lib" / "analog.svg")
        candidates.append(base / "src" / "analog.svg")
        candidates.append(base / "analog.svg")

    for r in roots:
        candidates.extend(
            [
                r / "analog.svg",
                r / "lib" / "analog.svg",
                r / "src" / "analog.svg",
            ]
        )

    for c in candidates:
        if c.is_file():
            return c
    return None


def run_netlistsvg(
    json_path: Path,
    svg_path: Path,
    skin: Path | None = None,
) -> Path:
    tool = find_netlistsvg()
    if not tool:
        raise RuntimeError(
            "netlistsvg not found. Install with: npm install -g netlistsvg"
        )

    skin = skin or find_analog_skin()
    svg_path.parent.mkdir(parents=True, exist_ok=True)

    if tool == "npx":
        cmd = ["npx", "--yes", "netlistsvg", str(json_path), "-o", str(svg_path)]
    else:
        cmd = [tool, str(json_path), "-o", str(svg_path)]

    if skin:
        cmd.extend(["--skin", str(skin)])

    proc = subprocess.run(cmd, capture_output=True, text=True, shell=False)
    if proc.returncode != 0:
        raise RuntimeError(
            "netlistsvg failed:\n"
            + (proc.stderr or proc.stdout or f"exit {proc.returncode}")
        )
    if not svg_path.is_file():
        raise RuntimeError(f"netlistsvg did not create {svg_path}")
    return svg_path


def write_debug_json(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
