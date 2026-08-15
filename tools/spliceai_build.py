"""
tools/spliceai_build.py — reading Illumina's precomputed SpliceAI VCFs, not 90 GB of them
==========================================================================================

Plumbing for ``tools/07_spliceai.ipynb``. The notebook keeps the *recipe* — which files
to read, what to keep, what to write — and this module holds the parts that would
otherwise bury it: the tabix binary format, the bgzf seek, the VCF header parse, and the
guards that stop a bad index from silently returning a short answer.

Nothing here runs on import, and nothing here downloads anything. The two source VCFs
are ~26.6 GB (SNVs) and ~64.1 GB (indels); you fetch them yourself from the Illumina
BaseSpace share and the notebook tells you how.

Why this is a module and not more notebook cells
------------------------------------------------
Parsing a ``.tbi`` by hand takes about forty lines that are entirely about a binary
layout and say nothing about splicing. Inline, they drown the four lines that actually
matter — pick the files, scan the region, write the extract, stamp the version.

Why this is not in ``toolkit.py``
--------------------------------
``toolkit.py`` is the shared *reader* layer: one thin ``load_<tool>()`` per dataset, no
build logic, deliberately. That rule exists because this repo used to reference seven
``build_*.py`` and two ``fetch_*.py`` scripts that were **never committed** — provenance
you could not check. This module is the opposite of that: it ships, the notebook imports
it in front of you, and every function is documented below and introduced in the
notebook next to the cell that calls it.

Why the tabix index is parsed by hand
-------------------------------------
``pysam`` would normally do this seek, but it does not build on Windows. The format is
small and stable (SAMtools' tabix spec), so ``tabix_linear_offset`` reads it directly
with ``struct``. That is portable, but it is also hand-rolled binary parsing against
files that are too large to eyeball — hence the guards in ``scan_region``.

Why the ``_build`` suffix
-------------------------
Not decoration. A module named ``spliceai.py`` in this directory would shadow the
PyPI package of the same name for anything run from ``tools/`` -- which is where the
notebooks run -- because the working directory precedes site-packages on ``sys.path``.
The failure is quiet and confusing: ``import spliceai`` succeeds, then an attribute
that belongs to the real package is simply missing.

Reference
---------
    SpliceAI : Jaganathan et al. 2019, Cell, PMID 30661751. Scores CC BY-NC 4.0.
"""
from __future__ import annotations

import gzip
import json
import re
import struct
from datetime import datetime, timezone
from pathlib import Path

from Bio import bgzf

# ─────────────────────────────────────────────────────────────────────────────
# Where the source VCFs live, and the window we care about
# ─────────────────────────────────────────────────────────────────────────────
# Resolved from this file, not the working directory, so importing from anywhere works.
TOOLS_DIR = Path(__file__).resolve().parent
DATA_DIR = TOOLS_DIR.parent / "data"

REGION_START, REGION_END = 117_470_000, 117_670_000   # GRCh38, a safe superset of CFTR
LIDX_SHIFT = 14                    # tabix linear index: one offset per 2**14 = 16 kb
EXPECT_ASSEMBLY = "GRCh38"         # these coordinates are GRCh38; an hg19 file must not slip in
GENE = "CFTR"                      # the SYMBOL field SpliceAI annotates each score against


def pick_source(*names: str) -> Path | None:
    """First of ``names`` that exists in ``data/`` **and is non-empty**, else None.

    The emptiness test is the point. A failed or cancelled download of one of these
    files often leaves a 0-byte stub behind, and reading that would look exactly like
    "this gene has no indels" rather than "the file never arrived". Callers pass the
    masked file first and the raw one second, so a missing masked release degrades to
    raw rather than to silence — and ``scan_region`` records which one it read.
    """
    for n in names:
        p = DATA_DIR / n
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def vcf_self_reported_version(vcf_path: Path) -> dict:
    """Read the version the VCF states about **itself**, from its own header.

    Returns whichever of ``file_date``, ``reference`` and ``annotation_version`` the
    header declares (``##fileDate``, ``##reference``, and the SpliceAI ``##INFO``
    description). These are claims the release makes about itself, so unlike a download
    timestamp they survive the file being copied, renamed or re-downloaded — which is
    what makes them worth persisting as the release stamp.
    """
    meta = {"file": vcf_path.name}
    reader = bgzf.BgzfReader(str(vcf_path), "rb")
    for bline in reader:
        line = bline.decode() if isinstance(bline, bytes) else bline
        if not line.startswith("##"):
            break
        if line.startswith("##fileDate="):
            d = line.strip().split("=", 1)[1]
            meta["file_date"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else d
        elif line.startswith("##reference="):
            meta["reference"] = line.strip().split("=", 1)[1]
        elif line.startswith("##INFO=<ID=SpliceAI"):
            m = re.search(r"SpliceAIv[\d.]+", line)
            if m:
                meta["annotation_version"] = m.group(0)
    reader.close()
    return meta


def tabix_linear_offset(tbi_path: Path, region_start: int = REGION_START) -> tuple[str, int]:
    """Parse a ``.tbi`` index and return ``(contig_name, bgzf_virtual_offset)``.

    A tabix index holds, per contig, a *bin* index and a *linear* index. Only the linear
    index is needed here: it stores one virtual file offset per 16 kb of the chromosome,
    so the entry covering ``region_start`` is a place to seek to that is guaranteed to be
    at or before the first record of interest. Everything before the target contig has to
    be walked through anyway, because the per-contig blocks are variable length.

    The returned offset is a bgzf *virtual* offset (block start << 16 | offset in block),
    which is what ``BgzfReader.seek`` expects — not a plain byte offset.
    """
    raw = gzip.open(tbi_path, "rb").read()
    off = [0]

    def take(fmt: str):
        sz = struct.calcsize(fmt)
        v = struct.unpack_from(fmt, raw, off[0])
        off[0] += sz
        return v

    (magic,) = take("<4s")
    if magic != b"TBI\x01":
        raise ValueError(f"{tbi_path.name} is not a tabix index (magic {magic!r})")
    n_ref, _fmt, _cs, _cb, _ce, _meta, _skip, l_nm = take("<8i")
    names = [n.decode() for n in raw[off[0]:off[0] + l_nm].split(b"\x00")[:-1]]
    off[0] += l_nm
    target = next((names.index(c) for c in ("7", "chr7") if c in names), None)
    if target is None:
        raise ValueError(f"no chr7 contig in {tbi_path.name}; first names: {names[:6]}")
    for r in range(n_ref):
        (n_bin,) = take("<i")
        for _ in range(n_bin):
            _bin, n_chunk = take("<Ii")
            off[0] += n_chunk * 16          # each chunk is two uint64s
        (n_intv,) = take("<i")
        intv = struct.unpack_from("<%dQ" % n_intv, raw, off[0])
        off[0] += n_intv * 8
        if r == target:
            li = min(region_start >> LIDX_SHIFT, n_intv - 1)
            # bins before the first record are 0; walk forward to the first real offset
            voff = next((intv[i] for i in range(li, n_intv) if intv[i]), 0)
            return names[target], voff
    raise ValueError(f"{tbi_path.name}: target contig never reached")


def scan_region(vcf_path: Path, verbose: bool = True) -> list[dict]:
    """Seek to the CFTR window in one precomputed VCF and return parsed score rows.

    Keeps all four delta scores (the model's actual output) plus a derived ``DS_max``,
    the ``SYMBOL`` the release annotated the score against, and how the row was produced
    (``score_type`` masked/raw, ``variant_class`` snv/indel) so two scores are never
    compared across a difference in reporting convention.

    Guarded twice, because both failure modes here are **silent** rather than loud:

    * the VCF must declare the assembly we expect. An hg19 file has perfectly valid
      chr7 positions; they simply mean somewhere else, and nothing downstream notices.
    * the first record after the seek must be at or before ``REGION_START``. Landing
      early is harmless (the position filter drops the extra rows), but landing late
      truncates the front of the window and every count downstream comes out quietly
      short. That happens when the ``.tbi`` does not belong to the VCF beside it.
    """
    score_type = "masked" if ".masked." in vcf_path.name else "raw"
    variant_class = "indel" if ".indel." in vcf_path.name else "snv"

    meta = vcf_self_reported_version(vcf_path)
    if EXPECT_ASSEMBLY not in meta.get("reference", ""):
        raise ValueError(
            f"{vcf_path.name} declares ##reference={meta.get('reference')!r}, which is not "
            f"{EXPECT_ASSEMBLY}. The CFTR window here is GRCh38; scoring against another "
            "assembly would silently return the wrong locus.")

    contig, voff = tabix_linear_offset(Path(str(vcf_path) + ".tbi"))
    if verbose:
        print(f"  {vcf_path.name}  (contig={contig}, {score_type}/{variant_class}, "
              f"{meta.get('reference')})")

    rows: list[dict] = []
    first_pos = None
    reader = bgzf.BgzfReader(str(vcf_path), "rb")
    reader.seek(voff)
    for bline in reader:
        line = bline.decode() if isinstance(bline, bytes) else bline
        if line.startswith("#"):
            continue
        f = line.rstrip("\n").split("\t")
        if f[0] != contig:
            break
        pos = int(f[1])
        if first_pos is None:
            first_pos = pos
            if pos > REGION_START:
                raise ValueError(
                    f"{vcf_path.name}: the .tbi seek landed at {contig}:{pos:,}, past the start "
                    f"of the target window ({REGION_START:,}), so the scan would silently miss "
                    "the front of it. Usually this means the .tbi does not belong to this VCF "
                    "(e.g. an hg19 index beside an hg38 file) -- re-download the matching index.")
        if pos < REGION_START:
            continue
        if pos > REGION_END:
            break
        info = f[7]
        if "SpliceAI=" not in info:
            continue
        field = next(x for x in info.split(";") if x.startswith("SpliceAI="))[len("SpliceAI="):]
        parts = field.split("|")
        # ALLELE|SYMBOL|DS_AG|DS_AL|DS_DG|DS_DL|DP_AG|DP_AL|DP_DG|DP_DL
        if len(parts) < 6 or parts[1] != GENE:
            continue
        try:
            ds = [float(parts[i]) for i in (2, 3, 4, 5)]
        except ValueError:
            continue
        rows.append({"chrom": f[0], "pos": pos, "ref": f[3], "alt": f[4],
                     "symbol": parts[1],
                     "DS_AG": ds[0], "DS_AL": ds[1], "DS_DG": ds[2], "DS_DL": ds[3],
                     "spliceai_ds_max": round(max(ds), 4),
                     "variant_class": variant_class, "score_type": score_type,
                     "source": "REAL"})
    reader.close()
    if verbose:
        print(f"    -> {len(rows):,} {GENE} {variant_class} records "
              f"(seek landed at {first_pos:,}, {REGION_START - first_pos:,} bp before the window)")
    return rows


def write_release_stamp(release_json: Path, scanned: list[Path]) -> str:
    """Persist the sources' self-reported versions beside the extract; return the string.

    Takes the paths **actually scanned**, and the notebook calls it only on the build
    path. Stamping an extract that already exists would describe whatever happens to be
    in ``data/`` at that moment, which is not the same thing: delete a source file after
    a build and the next run would relabel the extract as having come from the fallback
    file instead. A stamp that can drift away from the artefact it describes is worse
    than no stamp, so an unstamped extract is reported as ``unknown``.
    """
    per_file = [vcf_self_reported_version(p) for p in scanned]
    ver = next((m.get("annotation_version") for m in per_file if m.get("annotation_version")),
               "unknown")
    dates = sorted({m.get("file_date") for m in per_file if m.get("file_date")})
    ref = next((m.get("reference") for m in per_file if m.get("reference")), "unknown")
    resolved = f"{ver} precomputed, VCF fileDate {'/'.join(dates) or 'unknown'}, {ref}"
    release_json.write_text(json.dumps({
        "resolved_version": resolved,
        "source_files": per_file,
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, indent=2))
    return resolved
