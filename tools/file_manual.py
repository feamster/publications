#!/usr/bin/env python3
"""File a manually downloaded PDF into the archive.

For hosts that cannot be fetched programmatically (ACM behind an interactive
bot check, IEEE without SOCKS, bepress, ResearchGate, dead author hosts), the
documented path is: download the PDF yourself in your own signed-in browser,
then file it with this script.

  python3 tools/file_manual.py <bibkey> [path/to/file.pdf]

With no path, the newest PDF in ~/Downloads is used. The file is copied to
pdf/<year>/<bibkey>.pdf and index.json is updated (status ok, source
manual-download, sha256, page count, source_url from the bib's url/doi).

  python3 tools/file_manual.py --auto <bibkey> [<bibkey> ...]

matches each key against ~/Downloads by DOI-in-filename, then by title
similarity at a high threshold (>=0.7; a loose threshold has produced false
positives before), and reports what it would file before doing it.
"""
import difflib
import hashlib
import json
import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = ROOT / "index.json"
DOWNLOADS = pathlib.Path.home() / "Downloads"
MATCH_THRESHOLD = 0.7


def pdf_pages(path):
    try:
        data = path.read_bytes()
    except Exception:
        return 0
    n = len(re.findall(rb"/Type\s*/Page\b", data))
    return n or len(re.findall(rb"/Count\s+(\d+)", data) or [])


def norm(s):
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).split()


def load():
    return json.loads(INDEX.read_text())


def save(idx):
    INDEX.write_text(json.dumps(idx, indent=2) + "\n")


def entry_for(idx, key):
    for e in idx:
        if e["key"] == key:
            return e
    sys.exit(f"no index entry for {key!r} — run `fetch_pubs.py sync` first")


def file_one(idx, key, src):
    e = entry_for(idx, key)
    src = pathlib.Path(src).expanduser()
    if not src.is_file():
        sys.exit(f"not a file: {src}")
    if src.read_bytes()[:5] != b"%PDF-":
        sys.exit(f"not a PDF (no %PDF- header): {src}")
    pages = pdf_pages(src)
    if pages <= 0:
        sys.exit(f"unreadable PDF (0 pages): {src}")
    dest = ROOT / "pdf" / str(e["year"]) / f"{key}.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    e.update(
        status="ok",
        source="manual-download",
        source_url=e.get("url") or (
            f"https://doi.org/{e['doi']}" if e.get("doi") else None),
        pdf_path=str(dest.relative_to(ROOT)),
        sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),
        pages=pages,
    )
    e.pop("stopgap", None)
    print(f"[FILED] {key:26} {pages:>3}p  <- {src.name}")
    return e


def candidates():
    return sorted(DOWNLOADS.glob("*.pdf"), key=lambda p: p.stat().st_mtime,
                  reverse=True)


def auto_match(idx, key, pool):
    e = entry_for(idx, key)
    doi = (e.get("doi") or "").lower()
    if doi:
        tail = doi.split("/")[-1]
        for p in pool:
            if tail and tail.lower() in p.name.lower():
                return p, f"DOI {tail} in filename"
    want = " ".join(norm(e.get("title")))
    best, score = None, 0.0
    for p in pool:
        got = " ".join(norm(p.stem))
        r = difflib.SequenceMatcher(None, want, got).ratio()
        if r > score:
            best, score = p, r
    if best and score >= MATCH_THRESHOLD:
        return best, f"title match {score:.2f}"
    return None, f"no match (best {score:.2f} < {MATCH_THRESHOLD})"


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    idx = load()
    if args[0] == "--auto":
        keys = args[1:]
        if not keys:
            sys.exit("--auto needs at least one bibkey")
        pool = candidates()
        if not pool:
            sys.exit(f"no PDFs in {DOWNLOADS}")
        picks = {}
        for k in keys:
            p, why = auto_match(idx, k, pool)
            print(f"{k:26} {'-> ' + p.name if p else 'SKIP'}  ({why})")
            if p:
                picks[k] = p
        if not picks:
            sys.exit("nothing matched; pass the path explicitly")
        for k, p in picks.items():
            file_one(idx, k, p)
    else:
        key = args[0]
        src = args[1] if len(args) > 1 else None
        if src is None:
            pool = candidates()
            if not pool:
                sys.exit(f"no PDFs in {DOWNLOADS}")
            src = pool[0]
            print(f"using newest download: {src.name}")
        file_one(idx, key, src)
    save(idx)
    print("index.json updated — now run: python3 tools/fetch_pubs.py render")


if __name__ == "__main__":
    main()
