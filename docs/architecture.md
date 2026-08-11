# Architecture — how a public source becomes a worklist

This is the annotated version of the diagram in the [README](../README.md#-quick-tour).
It describes what actually happens when you run a notebook in this repo, and where the
two guardrails sit: **only license-verified data crosses into git**, and **every table
is labelled REAL or DEMO**.

```mermaid
flowchart TB
    subgraph SRC["1 · Public sources — external, licensed, never vendored"]
        direction LR
        S1["<b>Live endpoints</b><br/>gnomAD v4.1.1 GraphQL<br/>AlphaMissense (GCS)<br/>ClinVar (NCBI FTP)"]
        S2["<b>Manual downloads</b><br/>EVE · ESM1b · REVEL<br/>PrimateAI · CFTR2 workbook"]
    end

    S1 -->|"fetch cell<br/>queries directly"| D
    S2 -->|"you download by hand,<br/>build cell parses"| D

    D["<b>2 · data/</b><br/>derived per-CFTR extracts<br/>+ .release.json version sidecars<br/><i>gitignored, except data/publishable/</i>"]

    D -.->|"license verified permissive:<br/>gnomAD · AlphaMissense · EVE"| PUB
    PUB["<b>data/publishable/</b><br/>the only data in git<br/>attributed in LICENSES.md"]

    D --> TK["<b>3 · toolkit.py</b><br/>thin load_&lt;tool&gt;() readers<br/>tool registry · thresholds<br/>call_from_score() · key normalisers"]

    TK --> REAL["<b>source = REAL</b><br/>extract present on disk"]
    TK --> DEMO["<b>source = DEMO</b><br/><i>small curated fallback,<br/>emits a warning;<br/>strict=True raises instead</i>"]
    TK --> ERR["<b>FileNotFoundError</b><br/>live-fetched tools have<br/>no demo fallback"]

    REAL --> X
    DEMO --> X

    X["<b>4 · Cross-check against the truth sets</b><br/>benchmark/00 — ClinVar (clinical assertions)<br/>benchmark/01 — CFTR2 (patient data + functional assays)"]

    X --> W["<b>5 · Discordance worklists</b><br/>A1 — missense: predictor vs curated class<br/>A2 — splice: delta scores vs curated class"]

    W --> H["<b>6 · Human curation</b><br/>the worklist is the deliverable;<br/>a score is never a diagnosis"]

    style D stroke-dasharray: 5 5
    style DEMO stroke-dasharray: 5 5
    style ERR stroke-dasharray: 5 5
```

## Stage notes

**1 · Sources.** Every dataset is license-restricted to some degree — REVEL is
non-commercial, PrimateAI is "research use only", CFTR2 has its own data-use terms.
None of them are redistributed here. `data_manifest.json` records the exact URL,
version, checksum, and license for each.

**2 · `data/` is a boundary, not a cache.** It is gitignored by default so the licensing
question is answered before anything is published, not after. The one exception is
`data/publishable/`: three extracts whose terms were checked and found permissive
(gnomAD ODbL+MIT, AlphaMissense CC BY 4.0, EVE MIT), each attributed in
[`LICENSES.md`](../data/publishable/LICENSES.md) and enforced by CI. Note the loaders
read `data/<file>` directly, so the published copies are an offline convenience you copy
across — not a wired-up default. The `.release.json` sidecars exist because most sources have no version
string in their URL — the fetch cell records an HTTP `Last-Modified`/`ETag`, or reads a
zip member's embedded timestamp, so a silent upstream score change becomes visible as a
changed release column rather than a mystery diff.

**3 · `toolkit.py` stays thin on purpose.** It holds only what is shared across
notebooks: the loaders, the tool registry (learning type + circularity rating per
predictor), the thresholds, and the key normalisers (`hgvsp_to_short()`,
`three_to_one()`) that let protein-keyed and coordinate-keyed tools join. Each dataset's
fetch/build code lives in the notebook that owns the tool, so the recipe sits next to the
explanation of why it works that way.

**4 · The truth sets are not interchangeable.** ClinVar gives breadth; CFTR2 gives a
functional axis that no sequence model trained on. But they cross-cite each other, so
CFTR2 is only *partially* orthogonal — see
[Circularity](../README.md#circularity).

**5 · Worklists, not calls.** The output is a ranked list of variants worth a human's
attention, using deliberately simple single cut-points. Real clinical classification
applies graded thresholds and multiple evidence lines (Pejaver 2022).

## The REAL/DEMO fork

The fork at stage 3 is the design decision the rest of the repo hangs off. A loader can
return one of three things, and which one it returns depends entirely on what is on disk:

| Situation | Live-fetched tools | Manual-download tools |
|---|---|---|
| Extract present | `source='REAL'` | `source='REAL'` |
| Extract absent | raises `FileNotFoundError` | returns `source='DEMO'` + warning |
| Absent, `strict=True` | raises | raises |

The asymmetry is deliberate. A missing live-fetch is always user error — the fetch cell
takes seconds and needs no manual step — so failing loudly is right. A missing
manual-download can be legitimate (the REVEL zip is 6.5 GB), so the loader degrades to a
teaching table instead of blocking the notebook, but it never does so silently and the
`source` column carries the truth into every downstream join.
