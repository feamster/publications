---
name: sync-publications
description: Keep the publications PDF archive in sync with Nick Feamster's CV. Use when the CV's publication list changes (papers added to cv.tex / feamster.bib), when PDFs are missing and need re-fetching, or when the README/MISSING index needs regenerating. Rebuilds index.json from the CV, fetches PDFs (open-access + authenticated UChicago proxy), and regenerates the index.
---

# Sync Publications Archive

This repo (`publications`) holds an original PDF for every entry in the
**Publications** section of Nick Feamster's CV, organized by year, with a
README index whose numbering/grouping matches the CV exactly.

## Single source of truth: `index.json`

One entry per CV publication, holding BOTH the CV/bib-derived fields and the
fetch state. There is no separate catalog/manifest. Fields:

- CV/bib-derived (rebuilt by `sync`): `n`, `category`, `key`, `title`,
  `authors`, `venue`, `year`, `bibtype`, `url` (bib), `doi` (bib), `eprint`.
- Fetch state (set by `fetch`, preserved across re-syncs): `status`,
  `source`, `source_url`, `resolved_doi` (DOI inferred by title search — may be
  wrong), `landing`, `pdf_path`, `sha256`, `pages`.

`sync` rebuilds the CV-derived fields and **merges in** existing fetch state by
key, so re-syncing against the CV never drops downloaded PDFs.

Inputs:
- List/order/categories: `\mkbib{key}` / `\mkbiba{key}{rate}` in
  `~/Documents/CV/current/cv.tex` (between `\section*{Publications}` and the
  next `\section*`; commented `%` lines ignored).
- Per-paper metadata: `~/Documents/research/feamster.github.io/bib/feamster.bib`
  (BibTeX keys are case-insensitive).

## Workflow (run from the repo root)

1. **Sync** after any CV/bib change (prints counts; flags CV keys missing from
   the bib):
   ```
   python3 tools/fetch_pubs.py sync
   ```
2. **Authenticate** to the UChicago library proxy for paywalled publishers
   (ACM/IEEE/Springer/Elsevier/SAGE). Opens a real Chrome window for SSO + Duo;
   the session is saved to `.auth/` (git-ignored). EZproxy sessions are
   IP-bound — re-run after changing networks:
   ```
   python3 tools/fetch_pubs.py login
   ```
3. **Fetch** PDFs (resumable — skips PDFs already on disk). OA/web waterfall:
   extra_urls override → arXiv (bib eprint) → OpenAlex (PDF + landing scrape) →
   Crossref title→DOI → Unpaywall → arXiv title → bib URL → ACM `/doi/pdf/` →
   DOI landing → DSpace (MIT theses) → Semantic Scholar. `--proxy` routes
   paywalled hosts through `proxy.uchicago.edu` using the saved session.
   ```
   python3 tools/fetch_pubs.py fetch --proxy            # full sweep
   python3 tools/fetch_pubs.py fetch --proxy --use-s2   # also Semantic Scholar (slow)
   python3 tools/fetch_pubs.py fetch --only KEY         # one paper
   python3 tools/fetch_pubs.py fetch --proxy --force    # re-fetch even if present
   ```
4. **Render** the index:
   ```
   python3 tools/fetch_pubs.py render        # README.md + MISSING.md
   ```
   `python3 tools/fetch_pubs.py all` does sync + fetch + render.

## Stragglers (MISSING.md) and `extra_urls.json`

For papers with no open-access copy (recent/in-press, paywalled non-ACM,
IEEE — which is behind an AWS WAF that blocks automation), either:
- drop the PDF at the path MISSING.md lists (`pdf/<year>/<key>.pdf`), or
- add a direct URL to `extra_urls.json` — `{ "<bibkey>": "https://…pdf" }`
  (value may be a list) — which the fetcher tries **first**.

Then rerun `fetch` (or just `render`). The fetcher never overwrites a
manually-placed PDF unless `--force` is given.

## Notes

- `index.json` records each paper's bib `doi`/`url` plus any `resolved_doi`
  found by title search — useful for the separate task of backfilling
  canonical/OA URLs into `feamster.bib` (resolved DOIs are NOT trusted; verify).
- `.auth/` and `.cache/` are git-ignored.
- IEEE Xplore is behind AWS WAF; those PDFs are retrieved manually (MISSING.md
  lists their direct links).
