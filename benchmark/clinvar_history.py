"""
benchmark/clinvar_history.py — dating ClinVar's calls from its own release archive
==================================================================================

Plumbing for the "when did ClinVar first call this pathogenic?" section of
``benchmark/00_clinvar.ipynb``. The notebook keeps the *argument* — why a date is needed,
what the archive floor does to it, which hold-outs it makes possible — and this module
holds the parts that would otherwise bury it: twelve releases whose column set grows from
25 to 43, a join key that does not exist before 2018, a classification vocabulary NCBI
renamed, and the guards that stop any of those from silently returning a plausible wrong
answer.

Nothing here runs on import. NCBI keeps every monthly ``variant_summary`` release in a
public FTP archive, so :func:`fetch_release` can pull any of them; each is filtered to
CFTR/GRCh38 on the way in and cached, because the raw files run from 8 MB (2015) to
421 MB (2026) and nobody should re-download 1.7 GB to re-run a notebook cell.

Why a date is measurable at all
-------------------------------
``variant_summary.txt.gz`` is a snapshot, not a history. Its ``LastEvaluated`` column is
the *most recent* review, which points the wrong way in time, and it carries no "first
asserted" field at all. But NCBI archives the snapshot every month back to 2015-02, so
the history can be reconstructed the same way CFTR2's is — read the archive, and the
first release at which a variant reads Pathogenic is a real, ClinVar-native date.

What it cannot do
-----------------
The series is **left-censored at its earliest release**: anything already Pathogenic then
has no date, only a bound. Sampling is **annual** (see :data:`RELEASES`), matching the
year granularity of ``toolkit.TOOL_YEAR`` — a variant dated to a release became
Pathogenic at some point in the preceding twelve months, not on that day.

The three traps this module exists to catch
-------------------------------------------
Each returns a plausible answer rather than an error, which is what makes them dangerous.

1. **``VariationID`` does not exist before 2018-12.** It is the key ``load_clinvar()``
   uses and the natural thing to join on, and for the oldest releases in the series it is
   simply absent — a walk keyed on it silently starts in 2018 and dates every variant
   known before then to 2018. ``#AlleleID`` is present in all twelve releases, so the walk
   keys on that.

2. **The column set grows from 25 to 43.** Positional indexing works on exactly one layout
   and reads the wrong column on the other eleven, so every column is located by header
   name and a missing one raises.

3. **Classification vocabulary drift.** NCBI renamed "Conflicting interpretations of
   pathogenicity" to "Conflicting classifications of pathogenicity". Treated as distinct
   strings it manufactures a reclassification for every affected variant, so
   :func:`normalise_significance` maps them together — while the raw string is kept.

Why this is a module and not more notebook cells
------------------------------------------------
Streaming twelve gzipped files, mapping four column layouts and normalising a drifting
vocabulary take about two hundred lines that are entirely about file archaeology and say
nothing about cystic fibrosis.

Why this is not in ``toolkit.py``
---------------------------------
``toolkit.py`` is the shared *reader* layer: one thin ``load_<tool>()`` per dataset, no
build logic, deliberately. ``load_clinvar_history()`` there reads what this module writes.

Why the ``_history`` suffix
---------------------------
Not decoration. A module named ``clinvar.py`` beside notebooks that run from
``benchmark/`` would sit on ``sys.path`` ahead of site-packages and shadow anything of
that name — the same failure ``tools/spliceai_build.py`` is named around.

Data use
--------
ClinVar is public domain (NCBI applies no restrictions), so unlike the CFTR2 series these
extracts *could* ship. They are kept in gitignored ``data/`` anyway because they are
bulky and trivially rebuilt; ``data/publishable/`` carries the current snapshot instead.
"""
from __future__ import annotations

import gzip
import json
import re
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# --------------------------------------------------------------------------------------
# The release series
# --------------------------------------------------------------------------------------
# NCBI archives variant_summary monthly from 2015-02. Monthly across eleven years is ~140
# releases and ~20 GB; this samples the LAST release of each year instead. That is the
# same granularity as toolkit.TOOL_YEAR, which records tool release *years*, so a finer
# sample would not sharpen any hold-out this feeds. 2026 is the current partial year.
RELEASES = (
    "2015-12", "2016-12", "2017-12", "2018-12", "2019-12", "2020-12",
    "2021-12", "2022-12", "2023-12", "2024-12", "2025-12", "2026-08",
)

_FTP = "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited"

# Columns pulled out of each release, located by header NAME rather than position: the
# layout grows from 25 columns (2015) to 43 (2026) and shifts in between.
_WANTED = {
    "allele_id": "#AlleleID",
    "name": "Name",
    "significance": "ClinicalSignificance",
    "review_status": "ReviewStatus",
    "variant_type": "Type",
    "last_evaluated": "LastEvaluated",
}
# Present only from 2018-12 onward -- absent is expected, not an error.
_OPTIONAL = {"variation_id": "VariationID"}


class HistoryError(RuntimeError):
    """A guard fired. Every one of these means a date would otherwise be wrong."""


# --------------------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------------------
# ClinVar's aggregate ClinicalSignificance is free-ish text -- 48 distinct strings across
# these twelve releases. Normalising is a real collapse, so the raw string is kept beside
# it and the rules below are applied. Each is a rename, a separator change or an explicit
# conflict rule; none is a clinical judgement about a variant.
#
# The separator is the subtle part. In the 2015-2016 releases ";" joins the *disagreeing
# classifications of individual submitters* -- "Benign;Likely benign;Pathogenic;Uncertain
# significance" is one aggregate row reporting four different calls. From 2017 ClinVar
# replaced that with the single label "Conflicting interpretations of pathogenicity", and
# reused ";" for trailing modifiers instead ("Pathogenic; drug response"). Taking the
# leading token would therefore read a pre-2017 four-way disagreement as a clean
# "pathogenic" and date the variant years too early -- the exact mistake section 4 of the
# notebook warns against. So every part is parsed and the conflict is reconstructed.
_MODIFIERS = frozenset({
    "drug response", "risk factor", "other", "association", "protective", "affects",
    "confers sensitivity", "not provided", "uncertain risk allele",
})
_CALLS = {
    "pathogenic": "pathogenic",
    "likely pathogenic": "likely pathogenic",
    "benign": "benign",
    "likely benign": "likely benign",
    "uncertain significance": "uncertain significance",
    "pathogenic/likely pathogenic": "pathogenic/likely pathogenic",
    "benign/likely benign": "benign/likely benign",
}
# ClinVar itself treats these pairs as concordant -- that is why it coined the combined
# labels in 2017. Mapping the older ";"-joined form onto them is what makes a 2015 row
# comparable with a 2024 one.
_CONCORDANT = {
    frozenset({"pathogenic", "likely pathogenic"}): "pathogenic/likely pathogenic",
    frozenset({"benign", "likely benign"}): "benign/likely benign",
}
# "no interpretation for the single variant" (2018-2023) was renamed to "no classification
# for the single variant" (2024+): the variant is only described as part of a haplotype.
_NO_SINGLE = re.compile(r"^no (interpretation|classification) for the single variant$"
                        r"|^not reported for simple variant$")
_CONFLICTING = re.compile(r"^conflicting (interpretations|classifications) of pathogenicity")

NO_SINGLE_VARIANT = "no single-variant classification"
CONFLICTING = "conflicting"
NOT_PROVIDED = "not provided"

# What counts as "ClinVar called this pathogenic" -- the label a supervised model could
# have trained on. Deliberately EXCLUDES 'conflicting': a conflicted record is not a clean
# pathogenic label, and counting it as one would inflate every hold-out. Section 4 of the
# notebook makes the same argument about not folding conflicts into a call.
PATHOGENIC = frozenset({"pathogenic", "likely pathogenic", "pathogenic/likely pathogenic"})


def normalise_significance(raw) -> str:
    """Collapse one ClinicalSignificance string to a canonical label.

    Splits on both separators ClinVar has used, drops modifier tokens that are assertions
    about the allele rather than pathogenicity calls, and resolves what remains: one call
    stands, a concordant pair becomes the combined label, and two or more genuinely
    different calls become ``conflicting``. Anything unrecognised passes through
    lowercased rather than being bucketed into a default, so it shows up in the vocabulary
    report instead of vanishing.
    """
    if not isinstance(raw, str) or not raw.strip() or raw.strip() == "-":
        return NOT_PROVIDED
    s = raw.strip().lower()
    if _CONFLICTING.match(s):
        return CONFLICTING
    if _NO_SINGLE.match(s):
        return NO_SINGLE_VARIANT

    parts = [p.strip() for p in re.split(r"[;,]", s) if p.strip()]
    calls, modifiers, unknown = set(), [], []
    for p in parts:
        if p in _MODIFIERS:
            modifiers.append(p)
        elif p in _CALLS:
            calls.add(_CALLS[p])
        elif _NO_SINGLE.match(p) or p == "-":
            continue
        else:
            unknown.append(p)
    if unknown:                      # surfaced by check_vocabulary rather than swallowed
        return unknown[0]
    if not calls:
        # A record whose only assertion is e.g. "drug response" was classified -- just not
        # on the pathogenicity axis. Folding it into "not provided" would lose the
        # difference between "nobody submitted a call" and "submitted a different kind".
        real = [m for m in modifiers if m != NOT_PROVIDED]
        return real[0] if real else NOT_PROVIDED
    if len(calls) == 1:
        return next(iter(calls))
    # Expand the combined labels before testing concordance, so "Pathogenic/Likely
    # pathogenic;Pathogenic" is not mistaken for a disagreement.
    flat = set()
    for c in calls:
        flat.update(c.split("/") if "/" in c else [c])
    if _CONCORDANT.get(frozenset(flat)):
        return _CONCORDANT[frozenset(flat)]
    return CONFLICTING


def is_pathogenic(canon: str) -> bool:
    """True if this canonical label is a clean pathogenic call. 'conflicting' is not."""
    return canon in PATHOGENIC


# --------------------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------------------
def archive_url(release: str) -> str:
    """Resolve 'YYYY-MM' to its archive URL.

    The archive layout changed: 2025 onward sits directly under ``archive/``, 2015-2024 is
    nested one level deeper under ``archive/<year>/``.
    """
    year = int(release[:4])
    if year >= 2025:
        return f"{_FTP}/archive/variant_summary_{release}.txt.gz"
    return f"{_FTP}/archive/{year}/variant_summary_{release}.txt.gz"


def cache_path(release: str, cache_dir: Path, gene: str = "CFTR") -> Path:
    return cache_dir / f"clinvar_{gene.lower()}_{release}.tsv"


def fetch_release(release: str, cache_dir: Path, gene: str = "CFTR",
                  assembly: str = "GRCh38") -> Path:
    """Stream one release, filter to one gene/assembly, cache the result as TSV.

    The raw files run 8 MB to 421 MB and are filtered on the way in, so the cache holds a
    few hundred KB per release instead. Caching per release also means an interrupted
    build resumes rather than re-downloading everything.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_path(release, cache_dir, gene)
    if dest.exists():
        return dest

    req = urllib.request.Request(archive_url(release),
                                 headers={"Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=900) as resp:
        gz = gzip.GzipFile(fileobj=resp)
        header = gz.readline().decode().rstrip("\n").split("\t")
        idx = {}
        for key, col in _WANTED.items():
            if col not in header:
                raise HistoryError(
                    f"{release}: no {col!r} column (header has {len(header)}: "
                    f"{header[:8]}...). The layout changed again -- fix the column map "
                    "rather than falling back to a position.")
            idx[key] = header.index(col)
        for key, col in _OPTIONAL.items():
            if col in header:
                idx[key] = header.index(col)
        gi, ai = header.index("GeneSymbol"), header.index("Assembly")

        rows = []
        for line in gz:
            f = line.decode(errors="replace").rstrip("\n").split("\t")
            if len(f) <= max(gi, ai):
                continue
            # GeneSymbol can be a multi-gene list ("CFTR;CTTNBP2") on larger events.
            if gene not in f[gi].split(";") or f[ai] != assembly:
                continue
            rows.append({k: (f[i] if i < len(f) else "") for k, i in idx.items()})

    if not rows:
        raise HistoryError(
            f"{release}: no {gene}/{assembly} rows. Either the filter is wrong or the "
            "assembly label changed -- an empty release would silently shorten the series.")
    df = pd.DataFrame(rows)
    df.insert(0, "release", release)
    df.to_csv(dest, sep="\t", index=False)
    return dest


def fetch_all(cache_dir: Path, releases=RELEASES, log=print) -> list[Path]:
    """Fetch every release in the series, skipping any already cached."""
    out = []
    for r in releases:
        dest = cache_path(r, cache_dir)
        if dest.exists():
            out.append(dest)
            continue
        log(f"  fetching {r} ...")
        out.append(fetch_release(r, cache_dir))
    return out


# --------------------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------------------
def check_key(frames: dict, key: str = "allele_id") -> None:
    """The join key must exist, be populated, and be unique in every release.

    A missing key silently truncates the series -- the walk simply starts at the first
    release that has the column, and every variant known before then is dated to it. A
    duplicated key silently fans the walk out. Both produce a table that looks fine.
    """
    absent = [r for r, df in frames.items()
              if key not in df.columns or (df[key].astype(str).str.strip() == "").all()]
    if absent:
        raise HistoryError(
            f"{key!r} is missing or empty in {len(absent)} of {len(frames)} releases "
            f"({', '.join(absent)}). Keying the walk on it would silently start the "
            f"series at {sorted(set(frames) - set(absent))[0]} and date every variant "
            "known before then to that release.")
    for release, df in frames.items():
        dup = int(df[key].duplicated().sum())
        if dup:
            raise HistoryError(
                f"{release}: {key!r} is the join key but {dup} rows duplicate it. "
                "Deduplicate before walking, or the trajectory fans out.")


def check_dropouts(frames: dict) -> list[str]:
    """No release step may lose a large share of the previous release's *classified* records.

    A key break reads as one cohort dropped and another added, which a "did it vanish and
    come back?" check misses entirely -- the affected variants reappear under a different
    key, so nothing looks resurrected.

    The population matters. ClinVar genuinely churns records carrying **no** submitted
    classification: its 2018 reorganisation dropped 234 such CFTR rows in one step, 96% of
    everything lost there, and most returned in later releases. Those rows can never carry
    a first-pathogenic date anyway, so counting them makes the guard fire on ordinary
    curation and forces the threshold so wide it stops catching real breaks. Measured on
    **classified** records instead, the worst observed step loses 1.78%; the 5% limit below
    sits above that with room, and well under what a broken key would produce.

    The unclassified churn is not swallowed -- it is returned in the notes and printed.
    """
    notes, releases = [], list(frames)
    for prev, cur in zip(releases, releases[1:]):
        a, b = frames[prev], frames[cur]
        ids_b = set(b["allele_id"])
        classified = set(a.loc[a["significance_canon"] != "not provided", "allele_id"])
        lost_classified = classified - ids_b
        lost_all = set(a["allele_id"]) - ids_b
        limit = max(10, len(classified) // 20)
        if len(lost_classified) > limit:
            raise HistoryError(
                f"{len(lost_classified)} of {len(classified)} CLASSIFIED variants present "
                f"at {prev} are absent at {cur} (limit {limit}). ClinVar does not retire "
                f"classified records in bulk, so the join key has probably broken -- those "
                f"variants would be re-dated to {cur}.")
        notes.append(
            f"{prev} -> {cur}: {len(a):,} -> {len(b):,} rows "
            f"(+{len(ids_b - set(a['allele_id'])):,} new, -{len(lost_all)} gone, "
            f"of which {len(lost_classified)} were classified)")
    return notes


def check_vocabulary(frames: dict, known: set[str] | None = None) -> Counter:
    """Report canonical labels seen, so an unrecognised one is visible rather than silent.

    This one deliberately does NOT raise. ClinVar's aggregate field is open text and new
    combinations appear routinely; the normaliser passes anything unrecognised through
    lowercased, and the notebook prints the tail so it can be inspected.
    """
    seen = Counter()
    for df in frames.values():
        seen.update(df["significance_canon"])
    return seen


# --------------------------------------------------------------------------------------
# The walk
# --------------------------------------------------------------------------------------
def build_history(paths: list[Path], key_by: str = "allele_id") -> dict:
    """Chain the cached releases into a per-variant classification trajectory.

    Returns ``{"long": DataFrame, "summary": DataFrame, "meta": dict}``. ``long`` is the
    raw uncollapsed signal, one row per variant per release; ``summary`` is the derived
    per-variant view ``load_clinvar_history()`` reads.

    ``key_by`` selects the join key and exists so the failure can be demonstrated rather
    than asserted. ``"allele_id"`` is correct. ``"variation_id"`` is the key
    ``load_clinvar()`` uses and the obvious thing to reach for -- it is expected to trip
    :func:`check_key`, because ClinVar did not add that column until 2018-12.
    """
    if key_by not in {"allele_id", "variation_id"}:
        raise HistoryError(f"key_by must be 'allele_id' or 'variation_id', got {key_by!r}")
    if len(paths) < 2:
        raise HistoryError("the release series needs at least two releases")

    frames = {}
    for p in sorted(paths):
        df = pd.read_csv(p, sep="\t", dtype=str).fillna("")
        release = df["release"].iloc[0]
        df["significance_canon"] = df["significance"].map(normalise_significance)
        # One row per allele per release. ClinVar can list an allele twice when it sits in
        # more than one aggregate record; keep the first and count what was collapsed.
        before = len(df)
        if key_by in df.columns:
            df = df.drop_duplicates(key_by, keep="first").reset_index(drop=True)
        frames[release] = df
        frames[release].attrs["collapsed"] = before - len(df)
    frames = dict(sorted(frames.items()))

    check_key(frames, key_by)
    steps = check_dropouts(frames)
    vocab = check_vocabulary(frames)

    long = pd.concat(
        [df[["release", "allele_id", "name", "significance",
             "significance_canon", "review_status"]] for df in frames.values()],
        ignore_index=True)
    long["is_pathogenic"] = long["significance_canon"].map(is_pathogenic)
    long = long.sort_values(["allele_id", "release"]).reset_index(drop=True)

    releases = list(frames)
    floor, latest = releases[0], releases[-1]
    current_ids = set(frames[latest]["allele_id"])

    # The walk keys on #AlleleID because that is the only column present in all twelve
    # releases, but load_clinvar() keys on VariationID. Carry the mapping from the newest
    # release (where both exist) so the two tables can be joined without re-deriving it.
    if "variation_id" not in frames[latest]:
        raise HistoryError(f"{latest}: no VariationID -- cannot bridge to load_clinvar()")
    to_variation = dict(zip(frames[latest]["allele_id"], frames[latest]["variation_id"]))

    summary = []
    for allele, g in long.groupby("allele_id", sort=True):
        path = g["is_pathogenic"].tolist()
        canon = g["significance_canon"].tolist()
        seen = g["release"].tolist()
        first_p = next((r for r, p in zip(seen, path) if p), None)
        i = path.index(True) if any(path) else None
        summary.append({
            "allele_id": allele,
            "variation_id": to_variation.get(allele, ""),
            "clinvar_first_pathogenic": first_p,
            "clinvar_first_seen": seen[0],
            "clinvar_class_changes": sum(1 for a, b in zip(canon, canon[1:]) if a != b),
            # was pathogenic at one release and not at a LATER one (a round trip counts)
            "clinvar_ever_withdrawn": bool(i is not None and not all(path[i:])),
            "clinvar_current_significance": canon[-1],
            "clinvar_in_current_release": allele in current_ids,
        })
    summary = pd.DataFrame(summary)

    if summary["clinvar_first_seen"].isna().any():
        raise HistoryError("some variants have no first-seen release")

    return {
        "long": long,
        "summary": summary,
        "meta": {
            "releases": releases,
            "release_count": len(releases),
            "steps": steps,
            "floor_release": floor,
            "latest_release": latest,
            "join_key": key_by,
            "vocabulary": dict(vocab.most_common()),
            "rows_collapsed_per_release": {r: int(df.attrs["collapsed"])
                                           for r, df in frames.items()},
        },
    }


def write_extracts(hist: dict, data_dir: Path) -> dict:
    """Write the two extracts and the release stamp."""
    long_fp = data_dir / "clinvar_history_long.csv"
    summary_fp = data_dir / "clinvar_history.csv"
    release_fp = data_dir / "clinvar_history.release.json"

    hist["long"].to_csv(long_fp, index=False)
    summary = hist["summary"].copy()
    summary["clinvar_history_release"] = hist["meta"]["latest_release"]
    summary.to_csv(summary_fp, index=False)

    stamp = dict(hist["meta"])
    stamp["built_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stamp["row_count"] = len(summary)
    stamp["long_row_count"] = len(hist["long"])
    release_fp.write_text(json.dumps(stamp, indent=2))
    return {"long": long_fp, "summary": summary_fp, "release": release_fp}
