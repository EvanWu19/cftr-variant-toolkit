"""Assert a bare clone behaves exactly as README.md documents.

Runs with no data/ present. Three contracts:

1. The three live-fetched sources (gnomAD, AlphaMissense, ClinVar) raise
   FileNotFoundError — they have no DEMO fallback, so a missing extract must
   never look like a successful load.
2. The manual-download sources fall back to a DEMO table AND warn. A silent
   fallback is the specific failure this repo is built to prevent.
3. strict=True raises instead of degrading, for every fallback loader.

Run locally the same way CI does:  python .github/scripts/smoke_fresh_clone.py
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import toolkit as tk  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        failures.append(label)


if (tk.DATA_DIR / "cftr2_cftr.csv").exists():
    sys.exit(
        f"refusing to run: {tk.DATA_DIR} holds built extracts, so this would not be "
        "testing fresh-clone behaviour. Point DATA_DIR at an empty tree instead."
    )

print("1. live-fetched loaders raise FileNotFoundError (no DEMO fallback)")
for name, loader in [
    ("gnomAD", tk.load_gnomad_all),
    ("AlphaMissense", tk.load_alphamissense),
    ("ClinVar", tk.load_clinvar),
]:
    try:
        loader()
    except FileNotFoundError:
        check(f"{name} raises FileNotFoundError", True)
    except Exception as exc:  # noqa: BLE001
        check(f"{name} raises FileNotFoundError", False, f"got {type(exc).__name__}: {exc}")
    else:
        check(f"{name} raises FileNotFoundError", False, "returned a frame instead")

print("2. manual-download loaders fall back to DEMO *and* warn")
fallback = {
    "CFTR2": tk.load_cftr2,
    "EVE": tk.load_eve,
    "ESM1b": tk.load_esm1b,
    "REVEL": tk.load_revel,
    "PrimateAI": tk.load_primateai,
    "SpliceAI": tk.load_spliceai,
    "Pangolin": tk.load_pangolin,
}
for name, loader in fallback.items():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        df = loader()
    check(f"{name} returns rows", len(df) > 0)
    check(f"{name} is labelled DEMO", set(df["source"].unique()) == {"DEMO"},
          f"got {sorted(set(df['source'].unique()))}")
    check(f"{name} warns about the fallback", len(caught) > 0, "fell back silently")

print("3. strict=True raises instead of degrading")
for name, loader in fallback.items():
    try:
        loader(strict=True)
    except FileNotFoundError:
        check(f"{name} strict=True raises", True)
    except Exception as exc:  # noqa: BLE001
        check(f"{name} strict=True raises", False, f"got {type(exc).__name__}: {exc}")
    else:
        check(f"{name} strict=True raises", False, "returned a frame instead")

print("4. shared helpers work with no data at all")
check("three_to_one('Tyr161Cys') == 'Y161C'", tk.three_to_one("Tyr161Cys") == "Y161C")
check("call_from_score(0.9, 'am') == 'pathogenic'",
      tk.call_from_score(0.9, "am") == "pathogenic")
check("call_from_score(-8.0, 'esm1b') == 'pathogenic'  (scale runs backwards)",
      tk.call_from_score(-8.0, "esm1b") == "pathogenic")
check("every registry tool has a circularity rating",
      all("circularity" in v for v in tk.TOOL_REGISTRY.values()))

if failures:
    print(f"\n{len(failures)} check(s) failed:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\nall fresh-clone checks passed")
