---
name: sync-publications
description: Keep Nick Feamster's publications PDF archive in sync with his CV. Use ~monthly, or whenever the CV's publication list changes (new papers added to cv.tex / feamster.bib), PDFs are missing, or the README/MISSING index needs regenerating. Rebuilds index.json from the CV, fetches PDFs (open-access + UChicago EZproxy + UChicago SOCKS for IEEE + manual Downloads), and regenerates the index.
---

# Sync Publications Archive

This repo (`publications`) holds an original PDF for every entry in the
**Publications** section of Nick Feamster's CV, organized by year, named by
bibkey, with a README index whose numbering/grouping matches the CV exactly.

## Recommended end-to-end workflow (do this ~monthly)

The CV/bib are the **source of truth**; the archive is derived from them. So
update them FIRST, then sync the archive:

1. **Add new papers to the bib + CV.** Add the BibTeX entry to
   `feamster.bib` (in the `feamster/bib` repo, used as a submodule by both the
   CV and the website) AND add a `\mkbib{key}` / `\mkbiba{key}{rate}` line to
   the right subsection of `~/Documents/CV/current/cv.tex`. The `\mkbib` list is
   what defines membership — a paper only enters the archive once it's cited in
   cv.tex.
2. **Rebuild the CV + website** (so everything based on the bib is current):
   - Commit & push `feamster.bib` in the bib submodule.
   - In `~/Documents/CV/current`: update the bib submodule, then rebuild
     (bibunits — see "Rebuilding the CV" below). `cv.pdf` is git-ignored in the
     CV repo (only source is tracked).
   - In `feamster.github.io`: bump the `bib` submodule pointer (this alone fixes
     the live publications page, which renders the bib via JS) and copy the
     rebuilt `cv.pdf` into `cv/cv.pdf`; commit & push.
3. **Sync the archive** (this repo):
   ```
   python3 tools/fetch_pubs.py sync     # rebuild index.json from cv.tex + feamster.bib
   python3 tools/fetch_pubs.py login    # UChicago SSO+Duo -> EZproxy session (for ACM/Springer/etc.)
   python3 tools/fetch_pubs.py fetch --proxy          # OA + EZproxy sweep
   python3 tools/fetch_pubs.py fetch --socks          # IEEE via UChicago SOCKS (see below)
   python3 tools/fetch_pubs.py render                 # README.md + MISSING.md
   ```
   Then commit & push this repo. (`all` = sync + fetch + render.)

> If you have a better idea than "bib/CV/website first, then sync," it's worth
> noting: the archive can technically `sync` off any cv.tex+bib state, but
> keeping the CV/website as the canonical update point avoids drift — the
> archive should always trail the CV, never lead it.

## Single source of truth: `index.json`

One entry per CV publication with BOTH CV/bib-derived fields and fetch state.
There is no separate catalog/manifest. `sync` rebuilds the CV-derived fields and
**merges in** existing fetch state by key, so re-syncing never drops downloads.
Bib `doi`/`url` are kept separate from `resolved_doi` (inferred by title search
— may be wrong; verify before trusting).

## Acquisition methods (what actually works, learned in practice)

The `fetch` waterfall, in order: `extra_urls.json` override → arXiv (bib
eprint) → OpenAlex (PDF + landing scrape) → **IEEE via SOCKS** → Crossref
title→DOI → Unpaywall → arXiv title → bib URL → ACM `/doi/pdf/` → DOI landing →
DSpace (MIT theses) → Semantic Scholar (`--use-s2`, slow). Plus:

- **ACM / Springer / Elsevier / SAGE (paywalled):** `--proxy` routes them
  through `proxy.uchicago.edu` using the saved EZproxy session (`login` first;
  it opens real Chrome for SSO+Duo). EZproxy sessions are **IP-bound** — re-run
  `login` after changing networks.
- **IEEE Xplore:** behind an AWS-WAF bot challenge that blocks EZproxy/headless.
  The fix is a **SOCKS proxy to UChicago** (`socks5h://localhost:8080`, default)
  giving an institutional IP, then `--socks`. The resolver finds the article
  number from the bib URL or a 10.1109 DOI redirect and pulls
  `stampPDF/getPDF.jsp`. **IEEE rate-limits (HTTP 418)** after ~15 rapid
  downloads — if some 418, wait ~60–90s and re-run `fetch --socks` (resumable).
  Requires the SOCKS tunnel to be up; if `localhost:8080` refuses connections,
  ask Nick to re-establish it.
- **HTML-only items** (e.g. a USENIX `;login:` article): rendered to PDF with
  headless Chromium `page.pdf()` (source = `html-render`).
- **DSpace (MIT theses):** resolved via the DSpace discover API.
- **Manual downloads (the long tail):** for anything bot-blocked or offline
  (bepress/Colorado Tech Law Journal, ResearchGate, dead author hosts, SSRN),
  Nick downloads it in his logged-in browser into `~/Downloads`; then match it
  by title/DOI and file it (copy to `pdf/<year>/<bibkey>.pdf`, set status ok,
  source `manual-download`, sha256, pages). **Auto-match guidance:** match on
  the DOI/SSRN-id embedded in the filename when present; for title matching use
  a high threshold (≥0.7) and skip files whose content is already archived under
  another key — a loose threshold (~0.6) has produced false positives (e.g. one
  SSRN paper wrongly matched a different IEEE entry).
- **`extra_urls.json`** (`{bibkey: url | [urls]}`, tried first): for found/known
  direct PDF URLs. Useful for USENIX/NSDI legacy paths
  (`usenix.org/legacy/event/<conf>/.../X.pdf`), AAAI OJS landing pages (scraped
  for `citation_pdf_url`), etc.
- **Bots/dead hosts that do NOT work programmatically** (don't burn time —
  go straight to manual Downloads): IEEE without SOCKS, bepress
  (`scholar.law.colorado.edu`), ResearchGate, `nms.lcs.mit.edu` (defunct),
  `researchictafrica.net` (broken cert).

## Same-work duplicates

When the CV lists the same work twice (e.g. a workshop + journal version, or a
conference + journal version) and only one form is obtainable, file the
in-hand published version under both keys with source `same-work-copy` and a
note of which sibling it is.

## Rebuilding the CV (bibunits)

From `~/Documents/CV/current` (LaTeX toolchain required):
```
rm -f bu*.aux bu*.bbl bu*.blg
pdflatex -interaction=nonstopmode cv.tex
for f in bu*.aux; do bibtex "${f%.aux}"; done   # ~256 bibunits
pdflatex -interaction=nonstopmode cv.tex
pdflatex -interaction=nonstopmode cv.tex
```

## Catching CV/bib errors

Verify the title of each fetched PDF matches its CV entry. This session caught a
real CV error: `Feamster2006:policy` used the tech-report title ("Stable Policy
Routing with Provider Independence") but cited the IEEE/ACM ToN 2007 journal
publication, whose actual title is "Implications of Autonomy for the
Expressiveness of Policy Routing" — fixed in `feamster.bib`. When a PDF's title
doesn't match the CV, check whether the CV/bib is wrong rather than assuming the
PDF is, and fix the bib (then rebuild CV/website).

## Conventions / notes

- Commit to all of Nick's repos **as him — no `Co-Authored-By: Claude` trailer**.
- `index.json` records bib `doi`/`url` plus any `resolved_doi` — useful for a
  future task of backfilling canonical/OA URLs into `feamster.bib`.
- `.auth/` (EZproxy session) and `.cache/` are git-ignored.
- Remaining gaps are normally not-yet-published / in-press papers and
  bot-protected items; `MISSING.md` always lists the current set with links.
