# Licenses & attribution for `data/publishable/`

Everything in this folder is a small, CFTR-only extract derived from a larger public
source, verified against that source's own current terms before being committed here.
See [`../../data_manifest.json`](../../data_manifest.json) and
[`../../DATA_LICENSING_CONCERNS.md`](../../DATA_LICENSING_CONCERNS.md) for the full
verification trail. **Not legal advice** — these are engineering-level attributions,
re-verify against each source's current terms before relying on them.

---

## `gnomad_cftr_all.tsv`

**Source:** gnomAD v4.1.1 (`gnomad_r4`), Broad Institute — queried live via the public
GraphQL API (`https://gnomad.broadinstitute.org/api`), gene `ENSG00000001626` (CFTR).
Built by `tools/01_gnomad.ipynb`.

**License:** [Open Database License (ODbL) v1.0](https://opendatacommons.org/licenses/odbl/1-0/)
+ MIT terms, per gnomAD's own data-use policy.

**Attribution:** Data from the Genome Aggregation Database (gnomAD), Broad Institute
(gnomad.broadinstitute.org). See Karczewski et al. 2020, *Nature*, PMID 32461654.
ODbL requires: attribute gnomAD, keep any derived database open, share adaptations of
the *database itself* under ODbL (this CFTR-only extract), and do not attempt to
re-identify participants.

**Not included:** gnomAD's bundled SpliceAI annotation column is separately licensed
CC BY-NC 4.0 and is not part of this extract — only frequency columns are published here.

**Known gap:** this extract has no `gnomad_release` version-tracking column yet (added
for every other tool in this toolkit, not yet backfilled for gnomAD) — the dataset
version is `gnomAD v4.1.1 (gnomad_r4)` as recorded in `data_manifest.json`.

---

## `alphamissense_cftr.tsv` (+ `alphamissense_cftr.release.json`)

**Source:** AlphaMissense v3 (Zenodo [10.5281/zenodo.8208688](https://doi.org/10.5281/zenodo.8208688)),
Google DeepMind — `AlphaMissense_aa_substitutions.tsv.gz`, filtered to UniProt P13569
(CFTR). Built by `tools/02_alphamissense.ipynb`.

**License:** [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). DeepMind
relicensed AlphaMissense predictions from CC BY-NC-SA to CC BY 4.0 on 2024-03-13,
lifting the NonCommercial restriction (verified against the Zenodo record).

**Attribution:** Cheng, J. et al. "Accurate proteome-wide missense variant effect
prediction with AlphaMissense." *Science* 381, eadg7492 (2023). PMID 37733863.
Predictions © 2023 DeepMind Technologies Limited, licensed CC BY 4.0.

---

## `eve_cftr_2021-08.csv` (+ `eve_cftr_2021-08.release.json`)

**Source:** EVE (Evolutionary model of Variant Effect), Marks Lab (Harvard Medical
School) / OATML (Oxford) — [evemodel.org](https://evemodel.org), release 2021-08,
`variant_files/CFTR_HUMAN.csv` from the bulk release zip. Built by `tools/03_eve.ipynb`.

**License:** MIT. evemodel.org's own "Bulk Protein Data" and "Single Protein Data"
download pages state, verbatim: *"The downloading of this data, and of all other data
on this site, falls under the MIT License."* (verified directly on the site 2026-08-07;
matches the GitHub code license, MIT, copyright Pascal Notin 2021).

**Attribution:** Frazer, J. et al. "Disease variant prediction with deep generative
models of evolutionary data." *Nature* 599, 91–95 (2021). PMID 34707284.

---

## What this extract is, and isn't

Each file is a **CFTR-gene-only slice** of a much larger genome/proteome-wide public
release — the *value* is entirely each source's own predictions or population data;
the selection here (filtering to one gene) is mechanical, not original research. These
extracts exist so this toolkit's teaching notebooks are reproducible without requiring
every reader to re-download multi-hundred-megabyte-to-multi-gigabyte source files.
Regenerate any of them yourself by running the fetch cell in the notebook named above —
see [`../README.md`](../README.md) for the full fetch-and-build guide.
