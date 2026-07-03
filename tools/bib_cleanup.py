#!/usr/bin/env python3
"""bib_cleanup.py — audit + auto-fix \mkbib-cited entries in feamster.bib.

For every CV-cited entry:
  - look up authoritative metadata from Crossref by DOI (bib doi > resolved_doi
    from publications/index.json) in parallel
  - compute proposed edits: fill empty fields, replace placeholder-shaped values
    (e.g. `pages = 1--14` placeholders that disagree with Crossref)
  - apply the safe subset in place, preserving formatting
  - report ambiguous mismatches for human eyeballing (no auto-edit)

Also: fill empty `url` from `https://doi.org/<doi>` (canonical), but ONLY when
the current `url` is empty — a working direct-PDF URL is better UX.

Usage:
  python3 tools/bib_cleanup.py audit         # dry run: writes proposed.json
  python3 tools/bib_cleanup.py apply         # applies audit.json to bib
"""
import json, re, sys, time, pathlib, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from fetch_pubs import parse_bib, parse_cv

REPO = pathlib.Path(__file__).resolve().parent.parent
CV = pathlib.Path.home() / "Documents/CV/current/cv.tex"
BIB = pathlib.Path.home() / "Documents/CV/current/bib/feamster.bib"
INDEX = REPO / "index.json"
PROPOSED = REPO / "bib_proposed.json"
REPORT = REPO / "bib_report.txt"

UA = "bib_cleanup/1.0 (mailto:feamster@uchicago.edu)"

MONTH_NAMES = ["", "jan", "feb", "mar", "apr", "may", "jun",
               "jul", "aug", "sep", "oct", "nov", "dec"]

# Any 1--N range where N looks like a typical placeholder page-count for a
# not-yet-paginated paper. If Crossref confirms different pages, we'll replace.
PLACEHOLDER_PAGES = re.compile(r"^\s*1\s*[-–]{1,2}\s*(\d{1,2}|N|X|TBD)\s*$", re.I)


def crossref_work(doi, timeout=15):
    if not doi:
        return None
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='/')}"
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r).get("message")
    except Exception as e:
        return {"__error__": str(e)}


def _decode_entities(s):
    """Crossref returns HTML-encoded entities in text fields (`&amp;`)."""
    if not s:
        return s
    return (s.replace("&amp;", "&").replace("&#x2013;", "–")
             .replace("&#x2014;", "—").replace("&lt;", "<").replace("&gt;", ">"))


def cr_field(cr, field):
    if not cr or "__error__" in cr:
        return None
    if field == "pages":
        p = cr.get("page")
        return p.replace("-", "--") if p and "--" not in p else p
    if field == "journal":
        ct = cr.get("container-title") or []
        return _decode_entities(ct[0]) if ct else None
    if field == "booktitle":
        ct = cr.get("container-title") or []
        return _decode_entities(ct[0]) if ct else None
    if field == "volume":
        return cr.get("volume")
    if field == "number":
        return cr.get("issue")
    if field == "address":
        ev = cr.get("event") or {}
        return _decode_entities(ev.get("location"))
    if field in ("year", "month"):
        for k in ("published-print", "published-online", "issued", "published"):
            d = cr.get(k)
            if d and d.get("date-parts"):
                dp = d["date-parts"][0]
                if field == "year" and dp and dp[0]:
                    return str(dp[0])
                if field == "month" and len(dp) >= 2 and dp[1]:
                    return MONTH_NAMES[int(dp[1])]
    return None


def norm(s):
    """Loose comparison: strip case/whitespace/punct for value equality."""
    if s is None:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def norm_venue(s):
    """Journal/booktitle normalization that also strips parenthetical acronyms
    like `(TIOT)`, `(CACM)`, `(CTLJ)` — Nick's curator style, not evidence of a
    wrong DOI."""
    if s is None:
        return ""
    return norm(re.sub(r"\s*\([^)]*\)\s*", " ", str(s)))


def norm_pages(s):
    if not s:
        return ""
    return re.sub(r"\s|-|–|—", "", s)


def audit():
    bib = parse_bib(BIB)
    cited = [item["key"] for item in parse_cv(CV)]
    by_key_ci = {k.lower(): v for k, v in bib.items()}
    idx = {e["key"]: e for e in json.loads(INDEX.read_text())}

    # keys -> DOI to resolve
    tasks = []
    for k in cited:
        e = bib.get(k) or by_key_ci.get(k.lower())
        if not e:
            continue
        doi = e.get("doi") or (idx.get(k) or {}).get("resolved_doi")
        tasks.append((k, doi))

    # parallel Crossref lookups
    print(f"Fetching Crossref for {sum(1 for _, d in tasks if d)} DOIs...",
          file=sys.stderr)
    cr_by_key = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        futs = {pool.submit(crossref_work, d): k for k, d in tasks if d}
        done = 0
        for fut in as_completed(futs):
            k = futs[fut]
            cr_by_key[k] = fut.result()
            done += 1
            if done % 25 == 0:
                print(f"  {done}/{len(futs)}", file=sys.stderr)

    proposed = []   # per (key, field): safe auto-apply
    warnings = []   # per (key, field): ambiguous mismatch, report only

    def rec(key, field, current, new, kind, source="crossref"):
        (proposed if kind == "apply" else warnings).append(
            {"key": key, "field": field, "current": current,
             "proposed": new, "kind": kind, "source": source})

    for k in cited:
        e = bib.get(k) or by_key_ci.get(k.lower())
        if not e:
            continue
        t = (e.get("__type__") or "").lower()
        cr = cr_by_key.get(k) or {}
        cr_ok = cr and "__error__" not in cr
        doi = e.get("doi") or (idx.get(k) or {}).get("resolved_doi")

        # ---- pages ----
        cur = e.get("pages")
        new_pages = cr_field(cr, "pages") if cr_ok else None
        if not cur and new_pages:
            rec(k, "pages", None, new_pages, "apply")
        elif cur and new_pages:
            if PLACEHOLDER_PAGES.match(cur) and norm_pages(cur) != norm_pages(new_pages):
                rec(k, "pages", cur, new_pages, "apply")
            elif norm_pages(cur) != norm_pages(new_pages):
                rec(k, "pages", cur, new_pages, "warn")

        # ---- type-specific ----
        checks = []
        if t == "article":
            checks = [("journal", cr_field(cr, "journal")),
                      ("volume", cr_field(cr, "volume")),
                      ("number", cr_field(cr, "number"))]
        elif t in ("inproceedings", "incollection"):
            checks = [("booktitle", cr_field(cr, "booktitle")),
                      ("address", cr_field(cr, "address"))]
        # ---- common ----
        checks += [("year", cr_field(cr, "year")),
                   ("month", cr_field(cr, "month"))]

        for f, new in checks:
            if not new:
                continue
            cur = e.get(f)
            if not cur:
                rec(k, f, None, new, "apply")
            else:
                # Journals/booktitles are compared with acronym-stripped norm
                # so `(TIOT)`/`(CACM)` don't fire spurious mismatches.
                nc = norm_venue(cur) if f in ("journal", "booktitle") else norm(cur)
                nn = norm_venue(new) if f in ("journal", "booktitle") else norm(new)
                if nc != nn:
                    rec(k, f, cur, new, "warn")

        # ---- url: fill only if empty AND we have a DOI.
        # Only trust the BIB DOI for URL fills (curator-approved). A resolved
        # DOI from title search can be wrong (mismatched paper), so those go
        # to warnings for eyeballing.
        bib_doi = e.get("doi")
        if not e.get("url") and doi:
            kind = "apply" if bib_doi else "warn"
            rec(k, "url", None, f"https://doi.org/{doi}", kind,
                source="bib-doi" if bib_doi else "resolved-doi")

        # If Crossref returned a DIFFERENT paper (existing pages, year, or
        # venue name badly disagrees), the DOI is likely wrong — retract every
        # `apply` we recorded for this key that came from that DOI.
        # (Whitespace-only journal-name differences are OK.)
        entry_warns = [w for w in warnings if w["key"] == k]
        bad_doi = any(
            (w["field"] == "pages") or
            (w["field"] == "year") or
            (w["field"] in ("journal", "booktitle")
             and norm_venue(w["current"]) != norm_venue(w["proposed"]))
            for w in entry_warns)
        if bad_doi:
            # move all of this key's applies to warnings (safer)
            keep = [c for c in proposed if c["key"] != k]
            move = [c for c in proposed if c["key"] == k]
            proposed[:] = keep
            for c in move:
                c["kind"] = "warn"
                c["source"] += "-doi-suspect"
                warnings.append(c)

    PROPOSED.write_text(json.dumps({"apply": proposed, "warn": warnings},
                                   indent=2))
    print(f"\nproposed.json: {len(proposed)} safe fills, {len(warnings)} warnings",
          file=sys.stderr)


def _entry_range(text, key):
    """Locate `@type{<key>,` and return the byte range [start, end) of the full
    entry (including outer braces). Case-insensitive on key."""
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        if m.group(2).lower() == key.lower():
            start = m.start()
            j, depth = m.end(), 1
            while j < len(text) and depth > 0:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                j += 1
            return start, j
    return None


def _detect_style(entry_text):
    """Sample the first field line to reproduce indent + `field = {value},`."""
    for ln in entry_text.splitlines():
        m = re.match(r"^(\s+)([A-Za-z][A-Za-z0-9_-]*)(\s*=\s*)", ln)
        if m:
            return m.group(1), m.group(3)
    return "    ", " = "


def _quote_value(field, value):
    """Wrap a value the way BibTeX expects. `month` uses macros (jan/feb/…)."""
    if field == "month" and re.match(r"^[a-z]{3}$", str(value)):
        return str(value)                # bare macro
    return "{" + str(value) + "}"


def _add_field(entry_text, field, value):
    """Insert `<indent>field = {value},` just before the closing brace of the entry."""
    indent, sep = _detect_style(entry_text)
    line = f"{indent}{field}{sep}{_quote_value(field, value)}"
    # Find the last non-brace non-blank line and add trailing comma if missing
    body = entry_text.rstrip()
    if not body.endswith("}"):
        raise ValueError("entry doesn't end with }")
    inner = body[:-1].rstrip()          # drop the final }
    # ensure the last field line ends with a comma
    lines = inner.splitlines()
    # append the new field. Add comma to last existing field if it lacks one.
    if lines and re.search(r"[A-Za-z0-9\"}]\s*$", lines[-1]):
        lines[-1] = lines[-1].rstrip() + ","
    lines.append(line)
    return "\n".join(lines) + "\n}"


def _replace_field(entry_text, field, new_value):
    """Replace the value of an existing field. Preserves surrounding whitespace."""
    def repl(m):
        return f"{m.group(1)}{_quote_value(field, new_value)}{m.group(3)}"
    # match: name = {…} or name = "…" or name = macro , (trailing delim optional)
    pat = re.compile(
        rf"(^\s*{re.escape(field)}\s*=\s*)(\{{[^}}]*\}}|\"[^\"]*\"|[A-Za-z]+)(\s*(?:,|(?=\s*\}}$)|$))",
        re.MULTILINE)
    new_text, n = pat.subn(repl, entry_text, count=1)
    if n == 0:
        # field not present — add it
        return _add_field(entry_text, field, new_value)
    return new_text


def apply():
    data = json.loads(PROPOSED.read_text())
    changes = data["apply"]
    text = BIB.read_text()

    # Group changes by key so we edit each entry once (safer than N passes).
    by_key = {}
    for c in changes:
        by_key.setdefault(c["key"], []).append(c)

    applied = 0
    skipped = []
    # Process in reverse offset order so earlier byte ranges are unaffected.
    ranges = []
    for k in by_key:
        r = _entry_range(text, k)
        if r:
            ranges.append((r, k))
        else:
            skipped.append(f"{k}: entry not found")
    ranges.sort(key=lambda x: x[0][0], reverse=True)

    for (start, end), k in ranges:
        entry = text[start:end]
        new_entry = entry
        for c in by_key[k]:
            f, v = c["field"], c["proposed"]
            try:
                # _replace_field falls through to _add_field when the field
                # isn't present, so it correctly handles both cases —
                # including empty-brace fields like `volume={}` that a
                # falsy-check would misclassify as missing.
                new_entry = _replace_field(new_entry, f, v)
                applied += 1
            except Exception as e:
                skipped.append(f"{k}.{f}: {e}")
        text = text[:start] + new_entry + text[end:]

    BIB.write_text(text)
    print(f"applied {applied} field edits to {len(ranges)} entries",
          file=sys.stderr)
    if skipped:
        print(f"skipped {len(skipped)}:", file=sys.stderr)
        for s in skipped[:20]:
            print(f"  {s}", file=sys.stderr)

    # Write a report of ambiguous mismatches (warnings)
    warn = data.get("warn", [])
    lines = [f"# Ambiguous mismatches — NOT auto-applied ({len(warn)})", ""]
    lines.append("These are cases where the existing bib value disagrees with")
    lines.append("Crossref. Common legit reasons: publisher renamed the venue,")
    lines.append("different pagination between preprint and published version,")
    lines.append("month is fuzzy (issue vs. presentation date), etc.")
    lines.append("")
    from collections import defaultdict
    by_field = defaultdict(list)
    for w in warn:
        by_field[w["field"]].append(w)
    for f, items in sorted(by_field.items()):
        lines.append(f"## {f} ({len(items)})")
        for w in items[:60]:
            lines.append(f"  {w['key']}")
            lines.append(f"     current : {w['current']!r}")
            lines.append(f"     crossref: {w['proposed']!r}")
        if len(items) > 60:
            lines.append(f"  ...and {len(items)-60} more")
        lines.append("")
    REPORT.write_text("\n".join(lines))
    print(f"warnings report -> {REPORT}", file=sys.stderr)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "audit"
    if cmd == "audit":
        audit()
    elif cmd == "apply":
        apply()
    else:
        print(__doc__)
        sys.exit(2)
