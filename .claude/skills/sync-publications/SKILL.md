---
name: sync-publications
description: Keep the publications PDF archive in sync with Nick Feamster's CV. Use when the CV's publication list changes (new papers added to cv.tex / feamster.bib), when PDFs are missing and need re-fetching, or when the README/MISSING index needs regenerating. Rebuilds the catalog from cv.tex, fetches open-access PDFs, and regenerates the index.
---

# Sync Publications Archive

This repo (`publications`) holds an original PDF for every entry in the
**Publications** section of Nick Feamster's CV, organized by year, with a
README index whose numbering/grouping matches the CV exactly.

## Source of truth

- **List + order + categories:** the `\mkbib{key}` / `\mkbiba{key}{rate}`
  citations in `~/Documents/CV/current/cv.tex` (between `\section*{Publications}`
  and the next `\section*`). Commented-out (`%`) lines are ignored.
- **Per-paper metadata:** `~/Documents/research/feamster.github.io/bib/feamster.bib`
  (the same submodule the CV and website share). BibTeX keys are
  case-insensitive.

The tool joins these into `catalog.json`, fetches PDFs into `pdf/<year>/<key>.pdf`,
records provenance in `manifest.json`, and renders `README.md` + `MISSING.md`.

## Workflow

Run everything from the repo root.

1. **Rebuild the catalog** after any CV/bib change and confirm the count/categories
   look right (it prints a summary and flags any CV key missing from the bib):
   ```
   python3 tools/fetch_pubs.py catalog
   ```
2. **Fetch PDFs** (resumable — skips papers already in `pdf/`; OA/web waterfall:
   OpenAlex → Unpaywall → arXiv → bib `url` → Semantic Scholar):
   ```
   python3 tools/fetch_pubs.py fetch            # all
   python3 tools/fetch_pubs.py fetch --only KEY # single paper
   python3 tools/fetch_pubs.py fetch --force    # re-fetch even if present
   ```
3. **Regenerate the index:**
   ```
   python3 tools/fetch_pubs.py render
   ```
   `python3 tools/fetch_pubs.py all` does catalog + fetch + render in one shot.

## Handling the stragglers (MISSING.md)

Recent (current-year), paywalled (e.g. SAGE/Elsevier), and thesis entries often
have no open-access PDF and land in `MISSING.md`. For each, drop the correct PDF
at the path it lists (`pdf/<year>/<key>.pdf`, named after the bibkey) — from the
author's own copy or a repo you have access to — then rerun `render`. The fetcher
will not overwrite a manually-placed file unless `--force` is given.

## Keeping it current

When a paper is added to the CV: add its bib entry to `feamster.bib`, add the
`\mkbib` line to `cv.tex`, then run `python3 tools/fetch_pubs.py all`. Commit the
new PDF + updated `catalog.json` / `manifest.json` / `README.md`.

Note: `manifest.json` also records each paper's resolved DOI and landing-page
URL — useful for the separate task of backfilling canonical/OA URLs into
`feamster.bib`.
