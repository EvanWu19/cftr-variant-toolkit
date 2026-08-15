"""
tools/pangolin_build.py — running the Pangolin splice model over one gene
=========================================================================

Plumbing for ``tools/08_pangolin.ipynb``. The notebook keeps the *recipe* — which
variants to score and what to write — and this module holds the machinery: loading the
twelve bundled models, one-hot encoding, the delta-score computation, the cached
reference slice, and the release stamp.

``one_hot_encode`` and ``compute_score`` are inlined **verbatim** from
``pangolin/pangolin.py`` (Zeng & Li 2022) rather than imported. That module's own
top-level ``import pyfastx, vcf`` pulls in dependencies nothing here otherwise needs,
and vendoring the two functions keeps the scoring identical to upstream while leaving
the import surface small. They are the only borrowed code in this file.

``torch`` is imported lazily, inside the functions that need it, so this module can be
imported and read on a machine that has never installed it — only the scoring path
actually requires a working torch.

What the model gives you, and what this keeps
---------------------------------------------
Pangolin emits, per position, a **largest increase** and a **largest decrease** in
predicted splice-site usage. ``score_variant`` returns ``max(gain, |loss|)``, which is
what the published 0.5 / 0.2 cut-points apply to — but that collapse discards the
*direction*, so a 0.86 does not say whether a site is being created or destroyed. It
cannot be recovered from the saved extract; only a rerun can. SpliceAI (tools/07)
keeps its four deltas and can answer that question.

Why the ``_build`` suffix
-------------------------
Not decoration, and not optional. A module named ``pangolin.py`` in this directory
shadows the installed ``pangolin`` package for anything run from ``tools/`` -- which
is where the notebooks run, since the working directory precedes site-packages on
``sys.path``. ``load_models()`` below imports ``pangolin.model``, so under the
shadowed name it would import *this file*, find no ``model`` submodule, and fail with
"'pangolin' is not a package". Do not rename this back.

Reference
---------
    Pangolin : Zeng & Li 2022, Genome Biol 23:103, PMID 35449021,
               https://github.com/tkzeng/Pangolin  — non-commercial.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

TOOLS_DIR = Path(__file__).resolve().parent
DATA_DIR = TOOLS_DIR.parent / "data"

REF_FA = DATA_DIR / "cftr_region_grch38.fa"
ENSEMBL_REGION_URL = "https://rest.ensembl.org/sequence/region/human/7:117465000..117680000"

DIST = 50            # Pangolin's aggregation window, +/- d around the variant
MAX_EVENT = 100      # skip ref/alt events larger than this; the score would be meaningless

# The model's own release, and the anchor for temporal-leakage reasoning: a variant first
# reported AFTER this cannot have informed the model. See toolkit.TOOL_YEAR / tools/05.
MODEL_RELEASE = "Pangolin, Zeng & Li 2022 (Genome Biol 23:103, PMID 35449021)"
MODEL_YEAR = 2022

_ACGT = re.compile(r"^[ACGT]+$")
IN_MAP = np.asarray([[0, 0, 0, 0], [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])


# ─────────────────────────────────────────────────────────────────────────────
# Vendored verbatim from pangolin/pangolin.py (Zeng & Li 2022)
# ─────────────────────────────────────────────────────────────────────────────
def one_hot_encode(seq, strand):
    seq = seq.upper().replace('A', '1').replace('C', '2').replace('G', '3').replace('T', '4').replace('N', '0')
    if strand == '+':
        seq = np.asarray(list(map(int, list(seq))))
    else:
        seq = np.asarray(list(map(int, list(seq[::-1]))))
        seq = (5 - seq) % 5
    return IN_MAP[seq.astype('int8')]


def compute_score(ref_seq, alt_seq, strand, d, models):
    import torch
    ref_seq = torch.from_numpy(np.expand_dims(one_hot_encode(ref_seq, strand).T, axis=0)).float()
    alt_seq = torch.from_numpy(np.expand_dims(one_hot_encode(alt_seq, strand).T, axis=0)).float()
    if torch.cuda.is_available():
        ref_seq, alt_seq = ref_seq.to("cuda"), alt_seq.to("cuda")
    pang = []
    for j in range(4):
        score = []
        for model in models[3 * j:3 * j + 3]:
            with torch.no_grad():
                ref = model(ref_seq)[0][[1, 4, 7, 10][j], :].cpu().numpy()
                alt = model(alt_seq)[0][[1, 4, 7, 10][j], :].cpu().numpy()
                if strand == '-':
                    ref, alt = ref[::-1], alt[::-1]
                l = 2 * d + 1
                ndiff = np.abs(len(ref) - len(alt))
                if len(ref) > len(alt):
                    alt = np.concatenate([alt[0:l // 2 + 1], np.zeros(ndiff), alt[l // 2 + 1:]])
                elif len(ref) < len(alt):
                    alt = np.concatenate([alt[0:l // 2], np.max(alt[l // 2:l // 2 + ndiff + 1], keepdims=True), alt[l // 2 + ndiff + 1:]])
                score.append(alt - ref)
        pang.append(np.mean(score, axis=0))
    pang = np.array(pang)
    loss = pang[np.argmin(pang, axis=0), np.arange(pang.shape[1])]
    gain = pang[np.argmax(pang, axis=0), np.arange(pang.shape[1])]
    return loss, gain


# ─────────────────────────────────────────────────────────────────────────────
# Model loading — and hashing what was loaded
# ─────────────────────────────────────────────────────────────────────────────
def _weight_path(j: int, i: int) -> Path:
    """Locate a bundled weight file inside the installed pangolin package."""
    try:
        from importlib.resources import files
        return Path(str(files("pangolin") / "models" / f"final.{j}.{i}.3.v2"))
    except Exception:
        from pkg_resources import resource_filename
        return Path(resource_filename("pangolin", f"models/final.{j}.{i}.3.v2"))


def load_models() -> tuple[list, dict[str, str]]:
    """Load the 12 bundled Pangolin models; return ``(models, {filename: sha256})``.

    The twelve are 4 tissues (heart, liver, brain, testis) x 3 model replicates, whose
    predictions ``compute_score`` averages. The hashes go into the release stamp: the
    package version alone would not catch a swapped, truncated or locally patched weight
    file, and those would change every score without changing any version string.
    """
    import torch
    from pangolin.model import Pangolin, L, W, AR

    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    models, weights = [], {}
    for i in [0, 2, 4, 6]:
        for j in range(1, 4):
            wpath = _weight_path(j, i)
            weights[wpath.name] = hashlib.sha256(wpath.read_bytes()).hexdigest()
            m = Pangolin(L, W, AR)
            m.load_state_dict(torch.load(wpath, map_location=dev))
            models.append(m.to(dev).eval())
    return models, weights


# ─────────────────────────────────────────────────────────────────────────────
# Reference sequence
# ─────────────────────────────────────────────────────────────────────────────
def region_start(header: str, seq: str) -> int:
    """First base's 1-based position, taken from the FASTA header and CHECKED.

    Ensembl REST writes ``chromosome:GRCh38:7:117465000:117680000:1``, but a cache
    written by anything else (samtools faidx, UCSC, a manual save) uses
    ``7:117465000-117680000`` — so picking a fixed colon-separated field crashes on one
    of them or, worse, reads a plausible wrong number. Instead take every integer in the
    header and keep the adjacent pair whose span equals the sequence length. That is
    self-validating: it identifies the coordinates AND catches a truncated or mismatched
    cache, which would otherwise offset every variant silently.
    """
    nums = [int(x) for x in re.findall(r"\d+", header)]
    spans = [(a, b) for a, b in zip(nums, nums[1:]) if b - a + 1 == len(seq)]
    if len(spans) != 1:
        raise ValueError(
            f"cannot locate {REF_FA.name} from its header {header!r}: found {len(spans)} "
            f"integer pairs spanning its {len(seq):,} bases, need exactly 1. If the file "
            "was truncated or hand-edited, delete it and re-run to re-fetch.")
    return spans[0][0]


def load_region(verbose: bool = True) -> tuple[int, str, str]:
    """Return ``(region_start_1based, sequence, fasta_header)``.

    Fetches and caches the ~215 kb CFTR slice from Ensembl's REST API on first use — no
    whole-genome FASTA is needed to score one gene. The header names the assembly and
    span, so it goes into the release stamp: scores are only meaningful against a stated
    reference.
    """
    if not REF_FA.exists():
        import requests
        if verbose:
            print("fetching CFTR reference region from Ensembl (one-time, ~215 kb)...")
        r = requests.get(ENSEMBL_REGION_URL,
                         headers={"Content-Type": "text/x-fasta"}, timeout=30)
        r.raise_for_status()
        REF_FA.write_text(r.text)
    lines = REF_FA.read_text().splitlines()
    header = lines[0].lstrip(">")
    seq = "".join(lines[1:]).upper()
    return region_start(header, seq), seq, header


# ─────────────────────────────────────────────────────────────────────────────
# Scoring one variant
# ─────────────────────────────────────────────────────────────────────────────
def score_variant(pos: int, ref: str, alt: str, r0: int, seq: str, models,
                  d: int = DIST) -> float:
    """Pangolin score for one variant: ``max(largest gain, |largest loss|)``, 4 dp.

    Cuts the +/-5 kb of context the model needs out of the cached region, checks that
    the reference base there really is ``ref`` (a mismatch means the coordinate or the
    assembly is wrong, and scoring it anyway would produce a confident wrong number),
    builds the alt sequence, and scores both.

    Scored in reference/plus-strand orientation, which matches how SpliceAI's precomputed
    scores are reported. CFTR is plus-strand; passing strand='-' would mis-score it.
    """
    start = (pos - r0) - (5000 + d)
    end = start + 10000 + 2 * d + len(ref)
    if start < 0 or end > len(seq):
        raise ValueError(f"outside the cached reference window (needs 7:{pos-5050}-{pos+5050})")
    window = seq[start:end]
    got = window[5000 + d: 5000 + d + len(ref)]
    if got != ref:
        raise ValueError(f"ref mismatch at 7:{pos} -- window has {got!r}, expected {ref!r}")
    alt_seq = window[:5000 + d] + alt + window[5000 + d + len(ref):]
    loss, gain = compute_score(window, alt_seq, "+", d, models)
    return round(float(max(gain.max(), -loss.min())), 4)


def skip_reason(pos, ref: str, alt: str, max_event: int = MAX_EVENT) -> str | None:
    """Why this variant cannot be scored, or None if it can.

    Kept as an explicit reason rather than a silent drop: coverage that is short for a
    stated reason is auditable, coverage that is just short is not.
    """
    import pandas as pd
    if pos is None or (isinstance(pos, float) and pd.isna(pos)):
        return "no GRCh38 coordinates in CFTR2"
    if not (_ACGT.match(str(ref)) and _ACGT.match(str(alt))):
        return "allele is not plain ACGT"
    if len(str(ref)) > max_event or len(str(alt)) > max_event:
        return f"event larger than {max_event} bp"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Release stamp
# ─────────────────────────────────────────────────────────────────────────────
def write_release_stamp(release_json: Path, weights: dict[str, str], region_header: str,
                        scope: str, scored: int, targets: int) -> str:
    """Stamp the run, and return the resolved version string.

    Two different questions get answered here, and conflating them is the mistake this
    avoids:

    * **When was the model released?** ``MODEL_RELEASE`` — the temporal anchor. A variant
      first reported after 2022 cannot have informed Pangolin, which is what makes a
      hold-out by report date meaningful. This is the date that belongs in a benchmark.
    * **Exactly what produced these numbers?** The package version, the SHA-256 of the
      twelve weight files actually loaded, the torch build and device, and the reference
      region. This is what a rerun has to match, and none of it can be reconstructed
      from the extract afterwards — so it is written while the model runs.
    """
    import torch
    try:
        from importlib.metadata import version as _pkgver
        pkg = _pkgver("pangolin")
    except Exception:
        pkg = "unknown"
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    resolved = (f"{MODEL_RELEASE}; run with pangolin pkg {pkg}, "
                f"12 bundled weight files, torch {torch.__version__} on {dev}")
    release_json.write_text(json.dumps({
        "resolved_version": resolved,
        "model_release": MODEL_RELEASE,
        "model_year": MODEL_YEAR,
        "pangolin_package_version": pkg,
        "model_weight_sha256": weights,
        "torch_version": torch.__version__,
        "device": dev,
        "reference_region": region_header,
        "reference_region_file": REF_FA.name,
        "aggregation_window_bp": DIST,
        "max_event_bp": MAX_EVENT,
        "scope": scope,
        "scored": scored,
        "targets": targets,
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, indent=2))
    return resolved
