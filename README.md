# CIR-Diagram

SPICE `.CIR` → one-page wired schematic (SVG). Flat client package.

## Layout

```
CIR.cir                 input netlist
cir_diagram/            Python package (only nested folder)
requirements.txt
run_deliverable.bat
CIR_full.svg            created in this folder (no output/, no zip)
```

## Run

```bat
run_deliverable.bat
```

Or:

```bat
py -m pip install -r requirements.txt
py -m cir_diagram.cli CIR.cir -o CIR_full.svg --mode single --max-per-row 80
```

## Verify

```bat
py test_verify.py
```
