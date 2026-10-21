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
   - In `feamster.github.io` (`~/Documents/research/feamster.github.io`): bump
     the `bib` submodule pointer (this alone fixes the live GitHub-Pages
     publications page, which renders the bib via JS) and copy the rebuilt
     `cv.pdf` into `cv/cv.pdf`; commit & push.
   - **Deploy to UChicago CS** (the `people.cs.uchicago.edu/~feamster/` mirror):
     from the `feamster.github.io` checkout run `make uchicago` — it rsyncs the
     site (incl. `bib/feamster.bib` and `cv/cv.pdf`) to
     `feamster@linux.cs.uchicago.edu:html/` over SSH (key-based; needs the
     UChicago SSH key/agent). The GitHub-Pages push does **not** update this
     mirror, so it's a required separate step. Verify with
     `ssh feamster@linux.cs.uchicago.edu 'grep <new-title> ~/html/bib/feamster.bib'`.
3. **Sync the archive** (this repo):
   ```
   python3 tools/fetch_pubs.py sync     # rebuild index.json from cv.tex + feamster.bib
   python3 tools/fetch_pubs.py login    # UChicago SSO+Duo -> EZproxy session (for ACM/Springer/etc.)
   python3 tools/fetch_pubs.py fetch --proxy          # OA + EZproxy sweep
   python3 tools/fetch_pubs.py fetch --socks          # IEEE via UChicago SOCKS (see below)
   python3 tools/fetch_pubs.py fetch --refresh-preprints --proxy --socks  # upgrade preprints (see below)
   python3 tools/fetch_pubs.py render                 # README.md + MISSING.md
   ```
   Then commit & push this repo. (`all` = sync + fetch + render.)

4. **Reconcile bib ↔ archive** (see "Bib URL consistency" below):
   ```
   python3 tools/bib_cleanup.py audit   # cross-check bib against Crossref + index.json.source_url
   # inspect bib_proposed.json (auto-applies) and bib_report.txt (warnings)
   python3 tools/bib_cleanup.py apply   # write the safe fills back into feamster.bib
   ```
   Then rebuild the CV, bump the website's bib submodule, `make uchicago`, and
   push all three repos (bib → CV → website). See "Bib URL consistency" for the
   rules the audit enforces.

   **`--refresh-preprints`** re-resolves *only* the entries currently held as a
   preprint (arXiv/SSRN/OpenReview host, or an OA `submittedVersion`) **or an
   explicit stopgap** (`extra_urls.json` override marked `"stopgap": true` — see
   "Stopgap URLs" below), and upgrades any whose authoritative published
   version has since appeared — the common case where a paper was archived from
   arXiv before its ACM/IEEE/USENIX version was out. It writes to a temp file
   and swaps in the result **only on a real upgrade** (a non-preprint/published
   copy); otherwise it keeps the existing copy (`[keep]`), so there's no
   lateral churn and a good PDF is never dropped. Every other archived entry is
   left untouched. Run it with `--proxy --socks` so paywalled published
   versions are reachable; **verify the title of each `[UP! ]` upgrade** against
   the CV (a published title can differ from the preprint's).

> If you have a better idea than "bib/CV/website first, then sync," it's worth
> noting: the archive can technically `sync` off any cv.tex+bib state, but
> keeping the CV/website as the canonical update point avoids drift — the
> archive should always trail the CV, never lead it.

## Always end a run with this summary table

Every time this skill runs (full monthly sync, a test run, or a partial), close
with a **Markdown table** of the workflow steps and their results, then a short
prose outcome and any follow-ups. This is the required final format — keep the
columns `Step | Result`, one row per step actually run/skipped, and state the
result concretely (counts, what was skipped and why). Template:

```
| Step | Result |
|---|---|
| **1. New papers?** | <none — bib <sha> matches CV pointer> OR <list of new \mkbib keys added> |
| **2. CV/website/UChicago deploy** | <skipped (no bib change)> OR <CV rebuilt NNpp; GitHub-Pages pushed; `make uchicago` deployed + verified> |
| **3a. `sync`** | <N publications, M archived> |
| **3b. `fetch --proxy`** | <session valid?; which keys resolved/missed and why> |
| **3c. `fetch --socks`** | <ran: results> OR <skipped — SOCKS tunnel down> |
| **3d. `fetch --refresh-preprints`** | <K preprints + S stopgaps: J `[keep]`, any `[UP! ]` upgrades (verify titles)> |
| **3e. `render`** | <X ok / Y missing> |
| **4. `bib_cleanup.py audit`** | <N safe fills, M warnings; also note bib-url-consistency count from the one-liner above> |
| **4b. `bib_cleanup.py apply` + rebuild** | <N field edits across K entries; CV rebuilt; website deployed> |
```

Then below the table: **Outcome** (one line — were there substantive changes,
did anything deploy), and **Notes** (preconditions like SOCKS up/down or whether
`login` was needed, plus any `[UP! ]` titles to double-check). End the
commit/push step by reporting the pushed commit hashes.

## Single source of truth: `index.json`

One entry per CV publication with BOTH CV/bib-derived fields and fetch state.
There is no separate catalog/manifest. `sync` rebuilds the CV-derived fields and
**merges in** existing fetch state by key, so re-syncing never drops downloads.
Bib `doi`/`url` are kept separate from `resolved_doi` (inferred by title search
— may be wrong; verify before trusting).

## Acquisition methods (what actually works, learned in practice)

**Prefer the authoritative published version over a preprint.** The waterfall
is split into two tiers: it first tries every *published-version* source (the
publisher's own PDF — ACM, IEEE, USENIX, Springer — via OpenAlex/Unpaywall OA,
SOCKS, ACM `/doi/pdf/`, the DOI landing, or a non-preprint bib URL) and only
falls back to a **preprint** (arXiv/SSRN/OpenReview) when no authoritative copy
is obtainable. Within the OA resolvers, candidate PDFs are ranked by the
OpenAlex/Unpaywall `host_type` (publisher > repository) and `version`
(publishedVersion > acceptedVersion > submittedVersion), so arXiv never wins
when a publisher copy exists. Each fetched entry records an `oa_version`
(`published`/`accepted`/`submitted`) in `index.json` for auditing.

The `fetch` waterfall, in order:
1. **Authoritative / published tier:** `extra_urls.json` override → resolve DOI
   early (bib DOI, else OpenAlex, else Crossref title→DOI) → OpenAlex OA PDFs
   (publisher+published ranked first) → **IEEE via SOCKS** → Unpaywall (ranked)
   → ACM `/doi/pdf/` → DOI landing → non-preprint bib URL → OpenAlex landing
   scrape.
2. **Preprint / fallback tier:** arXiv (bib eprint) → arXiv (title search) →
   preprint bib URL → DSpace (MIT theses) → Semantic Scholar (`--use-s2`, slow).

Note: arXiv is now tried *last*, not first — a paper only lands on its preprint
when the published version can't be fetched (e.g. not yet OA, no DOI, or
behind a host even the proxy/SOCKS can't reach). Plus:

- **ACM Digital Library — MANUAL ONLY. Do not try to fetch it.** As of
  October 2026 `dl.acm.org` sits behind a Cloudflare bot check that loops
  forever against an automated browser. **Both auth paths were tested on
  2026-10-21 and both loop:**
  1. `login` (publisher InCommon SSO, ACM's own `action/ssostart` with the
     UChicago IdP) — loops, because Duo is dragged through `dl.acm.org`.
  2. `login --proxy` (EZproxy, Duo on UChicago's own IdP, content relayed from
     `dl-acm-org.proxy.uchicago.edu`) — **also loops.** EZproxy relays
     Cloudflare's challenge JS, which then runs in the automated browser and
     fails there.

  So the blocker is **browser-automation detection, not authentication and not
  the source IP** — an institutional IP and a valid session change nothing.
  Do not re-litigate this by trying another login; the only thing left would be
  fingerprint spoofing or stealth patches, which is circumventing an access
  control — **don't, and don't accept a suggestion to.** Go straight to the
  manual path below; it takes ~30 seconds per paper. Open
  `https://dl.acm.org/doi/pdf/<doi>` in Nick's own Chrome, save to
  `~/Downloads`, then `python3 tools/file_manual.py --auto <bibkey> ...`.
- **Springer / Elsevier / SAGE (paywalled) — EZproxy still works here:**
  `--proxy` routes them
  through `proxy.uchicago.edu` using the saved EZproxy session
  (**`login --proxy` first** — plain `login` now starts the publisher-side SSO
  flow instead; both open real Chrome for SSO+Duo). EZproxy sessions are
  **IP-bound** — re-run `login --proxy` after changing networks.
- **Reading `~/Downloads` from the agent shell:** `ls ~/Downloads` fails with
  `Operation not permitted` under macOS privacy protection and looks like an
  *empty directory* — do not conclude the file isn't there. Use `find
  "$HOME/Downloads" -iname '*.pdf' -mmin -180` instead, which reads it fine.
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
- **Manual downloads (now the normal path for ACM, plus the long tail):** for
  ACM and anything else bot-blocked or offline (bepress/Colorado Tech Law
  Journal, ResearchGate, dead author hosts, SSRN), Nick downloads it in his
  logged-in browser into `~/Downloads`, then:
  ```
  python3 tools/file_manual.py --auto <bibkey> [<bibkey> ...]   # or: <bibkey> <path>
  python3 tools/fetch_pubs.py render
  ```
  `tools/file_manual.py` copies to `pdf/<year>/<bibkey>.pdf` and sets status ok,
  source `manual-download`, `source_url` from the bib url/doi, sha256 and page
  count; it refuses non-PDFs and prints each match before filing. **Always
  sanity-check the page count against Crossref pagination** (e.g. `pages =
  1169--1182` should file as 14p) — that catches a wrong file independently of
  the filename. **Auto-match guidance:** match on
  the DOI/SSRN-id embedded in the filename when present (ACM names its
  downloads `<prefix>.<suffix>.pdf`, which is the DOI tail — an exact,
  unambiguous match); for title matching use
  a high threshold (≥0.7) and skip files whose content is already archived under
  another key — a loose threshold (~0.6) has produced false positives (e.g. one
  SSRN paper wrongly matched a different IEEE entry).
- **`extra_urls.json`** (`{bibkey: url | [urls]}`, tried first): for found/known
  direct PDF URLs. Useful for USENIX/NSDI legacy paths
  (`usenix.org/legacy/event/<conf>/.../X.pdf`), AAAI OJS landing pages (scraped
  for `citation_pdf_url`), etc.
- **Bots/dead hosts that do NOT work programmatically** (don't burn time —
  go straight to manual Downloads): **`dl.acm.org` (Cloudflare, since Oct 2026
  — see above; no auth fix exists)**, IEEE without SOCKS, bepress
  (`scholar.law.colorado.edu`), ResearchGate, `nms.lcs.mit.edu` (defunct),
  `researchictafrica.net` (broken cert).
- **Why `ctx.request.get()` can't clear a bot check (fixed Oct 2026):** the
  Playwright `request` API is a bare HTTP client — it carries the session
  cookies but renders no page, runs no JS and has no browser fingerprint, so it
  fails *any* interactive challenge regardless of authentication. `pw_get()`
  now falls back to a real page navigation and captures the download. Keep that
  shape for any future publisher work; a fetch that only uses `request.get`
  will silently fail on every gated host.

## Bib URL consistency (feamster.bib ↔ index.json / extra_urls.json)

The bib is the source of truth for what the public website publishes. Every
CV-cited entry with an archived PDF should carry a `url = {...}` (or a `doi =
{...}` — the JS renderer accepts either) so the publications page renders a
clickable link. Two invariants to enforce after every monthly sync:

1. **Every entry `\mkbib`'d in cv.tex, except stopgap-flagged and truly missing
   in-press papers, has a `url` or `doi` in feamster.bib.** No exceptions for
   entries where the archive already has a `source_url` — that URL should be
   in the bib too. Quick audit:
   ```
   python3 -c "
   import sys, pathlib, json; sys.path.insert(0,'tools')
   from fetch_pubs import parse_bib, parse_cv
   bib=parse_bib(pathlib.Path.home()/'Documents/CV/current/bib/feamster.bib')
   ci={k.lower():v for k,v in bib.items()}
   cited=[i['key'] for i in parse_cv(pathlib.Path.home()/'Documents/CV/current/cv.tex')]
   idx={e['key']:e for e in json.load(open('index.json'))}
   MISS={e['key'] for e in idx.values() if e.get('status')=='missing' or e.get('stopgap')}
   gap=[k for k in cited if not (bib.get(k) or ci.get(k.lower()) or {}).get('url')
        and not (bib.get(k) or ci.get(k.lower()) or {}).get('doi') and k not in MISS]
   print(f'{len(gap)} CV entries lack url/doi in bib:', gap)
   "
   ```
   A healthy state prints `0`.

2. **Prefer canonical URLs; never use stopgap/author-site URLs in the bib.**
   Order of preference for the `url` field when backfilling:
   1. Publisher/DOI (`dl.acm.org`, `ieeexplore.ieee.org`, `link.springer.com`,
      `usenix.org`, `doi.org/…` — including `10.48550/arxiv.*` arXiv DOIs).
   2. Institutional canonical (DSpace handles, `repository.gatech.edu`,
      `www.repository.cam.ac.uk`, workshop-hosted archives like `ceas.cc`,
      `edusigcomm.info.ucl.ac.be`, `netecon.seas.harvard.edu`).
   3. Preprint host (`arxiv.org`, `papers.ssrn.com`) — user's policy: **SSRN
      counts as canonical**.
   4. Anything already in `extra_urls.json` for the entry (carry over
      even if not "canonical" — see rule 3 below).
   5. Nothing. Leave `url` empty rather than back-referencing our own
      `feamster.github.io/publications/…` mirror.

3. **extra_urls.json entries carry over into the bib.** If we've told the
   fetcher where to grab the PDF from, that URL is by definition the
   best-available source — the bib should reflect the same URL. Two
   exceptions:
   - **Stopgap-flagged entries** (`{"url": "...", "stopgap": true}`) are
     deliberately kept out of the bib until the canonical replacement lands.
   - **Same-work-copy** entries (see below) inherit from the primary key.

The `bib_cleanup.py audit` step enforces all three above. Anything it can't
auto-safely apply lands in `bib_report.txt` for human review.

## `tools/bib_cleanup.py` — audit + safe apply

Audits every `\mkbib`-cited entry in `feamster.bib` against Crossref (by bib
`doi`, else `resolved_doi` from `index.json`), plus `index.json.source_url`
(de-proxied from EZproxy paths → doi.org) and `extra_urls.json`. Emits two
files:

- **`bib_proposed.json`** — safe auto-applies (missing `pages`/`volume`/
  `number`/`month`/`address`/`url`/`doi` filled from an authoritative source;
  placeholder `pages = 1--N` replaced with real Crossref pagination).
- **`bib_report.txt`** — ambiguous mismatches grouped by field for human
  eyeballing. Common categories:
  - venue-name-with-`(ACRONYM)` variants (curator style — cosmetic; leave)
  - `journal`/`booktitle` mismatch where the resolved DOI is *wrong*
    (fix by nulling `resolved_doi` in `index.json`)
  - preprint vs. published pagination (leave — the archive tracks that
    separately in `oa_version`)

**Safety guards baked in:**
- URL fills only from **bib DOI** (curator-approved). URL fills from
  `resolved_doi` (Crossref title-search) go to warnings — a wrong resolved DOI
  would poison the bib.
- If Crossref's `pages`/`year`/`venue` for an entry disagrees drastically with
  the bib, ALL fills for that entry get retracted to warnings (the DOI is
  probably wrong).
- Journal names are compared with `norm_venue()` which strips `(ACRONYM)`
  parentheticals and decodes `&amp;` — so `Research and Politics` vs
  `Research &amp; Politics` doesn't fire a spurious mismatch.
- Page fills from PDF page count skip entries where `pdf_pages > 30`
  (probably a tech-report copy filed under a workshop key — verify manually).

**Nulling a wrong `resolved_doi`:** if the `bib_report.txt` warnings for an
entry all show a totally different paper (title/venue/year all disagree), the
`resolved_doi` in `index.json` is wrong. Fix:
```
python3 -c "import json;d=json.load(open('index.json'));\
[e.__setitem__('resolved_doi',None) for e in d if e['key']=='<KEY>'];\
json.dump(d,open('index.json','w'),indent=2)"
```

## Rendering guardrails (feamster.github.io/js/bib-list.js)

The `bibtexify` renderer has a legacy line (~2541) that substitutes the
literal string `<span class="undefined">missing</span>` for any `undefined`
value in a citation string. To prevent that showing up on the live page:

- **Every optional field in a type template MUST be guarded** with a
  `((entryData.X)?...)` conditional (see `inproceedings`/`article`/
  `incollection`/`techreport`/`mastersthesis`/`phdthesis` for examples). A
  bare `entryData.pages` will render as `pp. missing` when the bib doesn't
  have pages.
- The `links()` function renders one of three link styles:
  - `fa-file-pdf-o` icon for URLs ending in `.pdf`
  - `fa-external-link` icon for `doi.org` URLs (canonical marker)
  - `fa-globe` icon for everything else
- Entries with only `doi` and no `url` fall back to `https://doi.org/<doi>`
  automatically (the 4th branch of `links()`).
- The `tweet:` bibtexify option is disabled in `publications.html` — do NOT
  re-enable it.

If you edit the templates, verify the render live with a headless load:
```
python3 -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b=p.chromium.launch(headless=True); page=b.new_context().new_page()
    page.goto('https://people.cs.uchicago.edu/~feamster/publications.html?v=chk', wait_until='networkidle')
    page.wait_for_timeout(4000)
    body=page.evaluate(\"document.querySelector('#pubTable').innerText\")
    print('missing occurrences:', body.lower().count('missing'))
    b.close()
"
```
A healthy state prints `missing occurrences: 0`.

## Stopgap URLs (non-canonical sources, auto-upgraded later)

A **stopgap** is a URL we deliberately archive from a truly non-canonical
source — an author's personal page, a co-author's site, a Dropbox link, etc. —
because the paper isn't reachable from its publisher/venue yet (e.g. a paper
accepted at a symposium whose proceedings haven't been posted). The word
"stopgap" is chosen because the copy is a **temporary placeholder** that the
next monthly `--refresh-preprints` pass should try to replace with the real
publisher/venue PDF once it exists. Preprints (arXiv/SSRN/OpenReview) are
NOT stopgaps — they have their own detection path.

Declare a stopgap by using the object form in `extra_urls.json`:
```
"chu2026privacy": {"url": "https://andrewchu.org/assets/pubs/mia_pets26.pdf", "stopgap": true}
```
(String and list-of-strings forms are still accepted for non-stopgap manual
overrides, e.g. USENIX legacy paths.) The `fetch` waterfall still tries the
override first in normal runs, and records `"stopgap": true` on the archived
`index.json` entry when it wins as `source: manual`.

**How the upgrade works:** `fetch --refresh-preprints` re-evaluates every entry
with `"stopgap": true` alongside every preprint entry. For stopgap entries it
**skips step -1 (the override) on purpose** so that the OpenAlex/Unpaywall/DOI/
publisher tiers get a chance at the canonical PDF. If any of them succeeds the
copy is swapped in, the entry is retagged `source: openalex|unpaywall|acm-doi|
...`, and the `stopgap` flag is **cleared** (`[UP! ] ... <-- upgraded from
stopgap`). If nothing better hits, the existing stopgap PDF is kept (`[keep]
... still stopgap (no published copy yet)`).

**Audit / cleanup:**
- List all archived stopgaps:
  `jq -r '.[] | select(.stopgap==true) | "\(.n)\t\(.key)\t\(.source_url)"' index.json`
- Once a stopgap is upgraded, remove its entry from `extra_urls.json` (the
  archive now points at the canonical URL and no override is needed).
- If a paper's stopgap URL turns out to BE its canonical location (rare — e.g.
  a workshop that only publishes via author homepages), drop the `stopgap: true`
  flag from `extra_urls.json` and the next fetch will clear the entry's flag.

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
- Each fetched entry has an `oa_version` (`published`/`accepted`/`submitted`)
  and a `source_url`. To audit how many archived PDFs are still preprints:
  `python3 -c "import json;d=json.load(open('index.json'));print(sum('arxiv.org'
  in (x.get('source_url') or '') for x in d if x.get('status')=='ok'))"`.
- **Re-resolving a preprint to its published version:** `git` is the backup, so
  force-refetching is safe — `git checkout -- <pdf>` restores any copy you don't
  want. Run `fetch --proxy --socks --force --only <key>` (after `login`) for an
  arXiv-sourced entry to pull the publisher version; if nothing authoritative is
  reachable it just re-lands on arXiv (no loss). Always verify the new PDF's
  title before keeping it.
- `.auth/` (EZproxy session) and `.cache/` are git-ignored.
- Remaining gaps are normally not-yet-published / in-press papers and
  bot-protected items; `MISSING.md` always lists the current set with links.
