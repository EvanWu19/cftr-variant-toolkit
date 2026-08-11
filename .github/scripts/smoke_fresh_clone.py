"""Assert a bare clone behaves exactly as README.md documents.

Runs with no locally built data/ — only what git actually ships. Contracts:

1. The four license-verified extracts in data/publishable/ (gnomAD, AlphaMissense,
   EVE, ClinVar) load as source='REAL' with NO setup step. This is the promise the
   README makes to anyone who clones the repo.
2. The six that may not be redistributed (ESM1b, REVEL, PrimateAI, SpliceAI,
   Pangolin, CFTR2) fall back to a DEMO table AND warn. Their notebooks ship; the
   data does not. A silent fallback is the specific failure this repo prevents.
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


# Guard: if someone runs this in a working checkout with built extracts, the DEMO
# assertions below would pass for the wrong reason.
locally_built = [p.name for p in (
    tk.DATA_DIR / "esm1b_cftr.csv",
    tk.DATA_DIR / "revel_cftr_v1.3.csv",
    tk.DATA_DIR / "primateai_cftr.csv",
    tk.DATA_DIR / "cftr2_cftr.csv",
) if p.exists()]
if locally_built:
    sys.exit(
        f"refusing to run: {tk.DATA_DIR} holds locally built extracts {locally_built}, "
        "so this would not be testing fresh-clone behaviour. Run against a checkout of "
        "tracked files only."
    )

print("1. the four shipped extracts load as REAL with no setup")
shipped = {
    "gnomAD": tk.load_gnomad_all,
    "AlphaMissense": tk.load_alphamissense,
    "EVE": tk.load_eve,
    "ClinVar": tk.load_clinvar,
}
for name, loader in shipped.items():
    try:
        df = loader()
    except Exception as exc:  # noqa: BLE001
        check(f"{name} loads on a bare clone", False, f"raised {type(exc).__name__}: {exc}")
        continue
    check(f"{name} loads on a bare clone", len(df) > 0)
    check(f"{name} is labelled REAL", set(df["source"].unique()) == {"REAL"},
          f"got {sorted(set(df['source'].unique()))}")

print("2. the non-redistributable extracts fall back to DEMO *and* warn")
fallback = {
    "ESM1b": tk.load_esm1b,        # scores are CC BY-NC (HF Space) -- cannot ship in an MIT repo
    "REVEL": tk.load_revel,        # non-commercial
    "PrimateAI": tk.load_primateai,  # Illumina "research use only"
    "CFTR2": tk.load_cftr2,        # cftr2.org terms forbid redistribution + derivative works
    "SpliceAI": tk.load_spliceai,  # CC BY-NC 4.0
    "Pangolin": tk.load_pangolin,  # non-commercial
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

print("4. a locally built extract always wins over the shipped snapshot")
probe = tk.DATA_DIR / "gnomad_cftr_all.tsv"
check("shipped copy is used when data/ has no local build",
      not probe.exists() and tk._extract(probe).parent.name == "publishable")

print("5. shared helpers work with no data at all")
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
