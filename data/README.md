# `data/` — how to fetch and build every extract

> **Four of the ten extracts are committed**, in
> [`publishable/`](publishable/LICENSES.md) — gnomAD (ODbL+MIT), AlphaMissense
> (CC BY 4.0), EVE (MIT) and ClinVar (CC0). Everything else under `data/`, and all of
> `outputs/`, is gitignored: **ESM1b** (scores CC BY-NC), **REVEL** (non-commercial),
> **PrimateAI** (Illumina "research use only"), **SpliceAI** (CC BY-NC 4.0),
> **Pangolin** (non-commercial) and **CFTR2** (terms forbid
> republishing any portion, including derived extracts) are **not redistributed**
> here — build them locally with the recipes below.
>
> The loaders resolve `data/<file>` first and fall back to `data/publishable/<file>`,
> so the four shipped extracts work on a fresh clone with no setup, and a local build
> always takes precedence over the shipped snapshot.

**There is one data folder now, not two.** Every dataset — whether it's pulled
live from an API, filtered from a bulk download, or produced by running a model
locally — lands in `data/`. There is no separate cache directory.

**The fetch/build code lives in the notebook that owns the tool, not in a
separate script.** Open that notebook, find the "Fetching/Building the REAL
data" cell near the top, and run it — it either queries a live source directly,
or tells you exactly what to manually download and where to put it before
re-running. This table is the summary; the notebook cell is the actual recipe.

> **One row below is marked *(notebook pending audit)*** — CADD. Its loader ships in
> `toolkit.py` and the dataset is documented here in full, but the notebook that
> builds it has not yet been through the audit pass the published ones have, so it
> is not in this repo yet. The `raw_source` / `source` / `license` columns still
> tell you exactly what to query and under what terms; you just have to write the
> request step yourself until that notebook lands.

Once you have rebuilt an extract, `python verify_data.py` checks its `sha256`/row
count against [`../data_manifest.json`](../data_manifest.json) (the machine-readable
version of everything below).

---

## Live-fetched (the notebook queries a public API/FTP directly — no manual download)

| Tool | Save-as in `data/` | Rows | Notebook | Source | Notes |
|---|---|---|---|---|---|
| **gnomAD (all CFTR)** | `gnomad_cftr_all.tsv` | 7,577 | `tools/01_gnomad.ipynb` | gnomAD v4.1.1 GraphQL API (`ENSG00000001626`, `gnomad_r4`) | **No PASS/AC filter** → 7,577 incl. AC0-filtered (see the live-computed funnel in tools/01). One table, classified into `gnomad_class` = missense (2,466) / noncoding (4,717) / other_coding (394); `load_gnomad_missense()`/`load_gnomad_noncoding()` in `toolkit.py` are in-memory filters over it, not separate files. `gnomad_af` is gnomAD v4.1's joint (exome+genome combined) AC/AN, not `max(exome_af, genome_af)` |
| **AlphaMissense** | `alphamissense_cftr.tsv` + `alphamissense_cftr.release.json` | 28,120 (true saturation: 1,480 residues × 19) | `tools/02_alphamissense.ipynb` | `AlphaMissense_aa_substitutions.tsv.gz` (protein-keyed), streamed from Google's public `dm_alphamissense` GCS bucket and filtered to UniProt P13569 | **CC BY 4.0** (DeepMind relicensed from CC BY-NC-SA on 2024-03-13). No version string in the GCS URL, so the fetch cell records the file's HTTP `Last-Modified`/`ETag` in the `.release.json` sidecar (`am_release` column) |
| **ClinVar** | `clinvar_cftr.tsv` + `clinvar_cftr.release.json` | ~6,100+ (drifts by design) | `benchmark/00_clinvar.ipynb` | `variant_summary.txt.gz` (default) or a pinned `archive/variant_summary_YYYY-MM.txt.gz`, streamed from `ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/` and filtered to `GeneSymbol=='CFTR', Assembly=='GRCh38'` | **Pinnable** — set `CLINVAR_RELEASE` in the fetch cell to `'latest'` (default; dated via HTTP `Last-Modified`, recorded in the `.release.json` sidecar) or `'YYYY-MM'` to reproduce a specific past month |
| **CADD** | *(no file — live per-call)* | — | *(notebook pending audit)* | `https://cadd.gs.washington.edu/api/v1.0/GRCh38-v1.7/` | Not reproducible unless you cache responses; a CADD version bump changes scores |

## Manual download + build (the notebook has a cell that reads the file once you provide it)

| Tool | Save-as in `data/` | Rows | Notebook | Manual download | Source | License |
|---|---|---|---|---|---|---|
| **CFTR2** | `cftr2_cftr.csv` + `cftr2_cftr.release.json` | 2,097 | `benchmark/01_cftr2.ipynb` | `CFTR2_30January2026.xlsx` (filename configurable in the build cell) | cftr2.org variant-list history tab | CFTR2 public data-use terms — **cite CFTR2** |
| **EVE** | `eve_cftr_2021-08.csv` + `eve_cftr_2021-08.release.json` | 26,809 | `tools/03_eve.ipynb` | `EVE_all_data.zip` → reads only `variant_files/CFTR_HUMAN.csv` from inside it | https://evemodel.org (release 2021-08, UniProt P13569) | **MIT** — confirmed on evemodel.org's download pages (2026-08-07); covers the scores, not just the GitHub code |
| **ESM1b** | `esm1b_cftr.csv` + `esm1b_cftr.release.json` | 28,120 | `tools/04_esm1b.ipynb` | `ALL_hum_isoforms_ESM1b_LLR.zip` → reads only `…/P13569_LLR.csv` from inside it | HuggingFace Space `ntranoslab/esm_variants` | MIT (code); scores per publication |
| **REVEL** | `revel_cftr_v1.3.csv` + `revel_cftr_v1.3.release.json` | 10,826 raw (all CFTR transcripts) → **9,730** canonical-transcript-only | `tools/05_revel.ipynb` | `revel-v1.3_all_chromosomes.zip` → streams `revel_with_transcript_ids` (6.5 GB member, stops after chr7) | https://sites.google.com/site/revelgenomics | **Non-commercial** (contact authors otherwise) |
| **PrimateAI** | `primateai_cftr.csv` + `primateai_cftr.release.json` | 9,722 | `tools/06_primateai.ipynb` | `primateAI/PrimateAI_scores_v0.2_hg38.tsv.gz` (~910 MB, streams full genome, not chromosome-sorted) | Illumina BaseSpace https://basespace.illumina.com/s/cPgCSmecvhb4 (native v0.2 release) | **"For research use only"** (Illumina, 2018, stated verbatim in the file header) |
| **SpliceAI** | `spliceai_cftr_2021_v1.3.csv` + `spliceai_cftr_2021_v1.3.release.json` | 2,075,730 (566,106 SNVs + 1,509,624 indels) | `tools/07_spliceai.ipynb` | `spliceai_scores.masked.snv.hg38.vcf.gz` + `.tbi` (~28.6 GB) and `spliceai_scores.raw.indel.hg38.vcf.gz` + `.tbi` (~69.3 GB) — the notebook seeks directly to the CFTR region via the `.tbi` index, never reading the full files | Illumina BaseSpace share https://basespace.illumina.com/s/otSPW8hnhaZR (`genome_scores_v1.3`) | **CC BY-NC 4.0** — attribute SpliceAI + Illumina |
| **Pangolin** | `pangolin_cftr.csv` + `pangolin_cftr.release.json` | 1,892 scored / 2,097 targets | `tools/08_pangolin.ipynb` | No data file — `pip install` the model package; the notebook auto-fetches+caches the ~215 kb CFTR reference region from Ensembl on first run (no whole-genome download) and needs `data/cftr2_cftr.csv` built first | github.com/tkzeng/Pangolin (Zeng & Li 2022, PMID 35449021) | non-commercial |

Notes:
- **EVE** is MIT-licensed (confirmed 2026-08-07) — safe to publish alongside
  AlphaMissense/gnomAD/ClinVar. It has no live download endpoint to date-check
  (manual zip), so version tracking instead reads the zip's own embedded
  per-file timestamp for `CFTR_HUMAN.csv`, exposed as the `eve_release` column.
- **ESM1b** is the same manual-zip situation as EVE — no live endpoint to date-check,
  so version tracking reads the zip's own embedded per-file timestamp for
  `P13569_LLR.csv`, exposed as the `esm1b_release` column.
- **REVEL** is the same manual-zip situation too — version tracking reads the zip's
  embedded timestamp for its one member, `revel_with_transcript_ids`, exposed as the
  `revel_release` column. REVEL's raw file also carries scores from a second CFTR
  transcript besides the canonical one; `load_revel()` drops it (verified against
  live Ensembl VEP as stale/unreliable, not real alternative-isoform biology — see
  `tools/05_revel.ipynb`), so the loaded table (9,730) is smaller than the raw file.
- **PrimateAI** (as of 2026-08-08) uses its own native genome-wide release (Illumina
  BaseSpace, v0.2, hg38), not dbNSFP — 9,722 of CFTR's 9,730 true possible missense
  SNVs, effectively saturating, scored under one confirmed-canonical UCSC transcript
  (`uc003vjd` = `ENST00000003084`). Coordinate-keyed (`chr`→bare-number normalized in
  the build cell — the source uses UCSC-style `chr7`, everything else here uses `7`).
  Version tracking reads the release gzip's own `MTIME` header field directly
  (2018-09-04), exposed as the `primateai_release` column. License is "for research
  use only" (Illumina), stricter than the old dbNSFP-inherited "non-commercial" note.
- **AlphaMissense** is true saturation (28,120 = 1,480 residues × 19), sourced from
  DeepMind's protein-keyed release file (`AlphaMissense_aa_substitutions.tsv.gz`) —
  see `tools/02_alphamissense.ipynb` for why the genome-coordinate file undercounts.
- **SpliceAI** is usually built **mixed masked/raw**: Illumina's `masked.indel`
  release is commonly a 0-byte failed download, so indels fall back to `raw.indel`
  while SNVs come from `masked.snv`. Every row carries `score_type`. Version
  tracking here needs no download date — the VCFs state their own version in their
  headers (`##fileDate`, `##reference`, and the SpliceAI annotation version inside
  `##INFO`), and the build cell copies that into the `.release.json` sidecar,
  exposed as the `spliceai_release` column. All four deltas are kept alongside
  `spliceai_ds_max`, so the headline collapse is always reversible.
- **Pangolin**'s default scope (`SCOPE = "cftr2"` in the build cell) scores every
  CFTR2 variant with GRCh38 coordinates and labels the result `source='REAL'`;
  `SCOPE = "curated"` scores just 5 classic alleles and stays `source='DEMO'` —
  the label follows coverage, never the model. It is the only entry here that is a
  **model run** rather than a download, so its `.release.json` records what a rerun
  must match — package version, SHA-256 of the twelve weight files, reference
  region, torch build and device — written *while the model runs*, because it
  cannot be reconstructed from an extract afterwards. Unlike SpliceAI, the stored
  `pangolin_score` is a collapse (`max(gain, |loss|)`) and the direction of the
  change is not retained; use SpliceAI's deltas when you need the mechanism.
- **CFTR2 has no historical archive** (checked directly against cftr2.org —
  there is no dated-release listing, unlike ClinVar). The build cell reads
  whatever release date is in the workbook's own header and records it in
  `cftr2_cftr.release.json`; reproducing a past run means manually sourcing
  that older workbook from cftr2.org yourself.

---

For the exact query strings, checksums, and build provenance of every item above,
see [`../data_manifest.json`](../data_manifest.json).
