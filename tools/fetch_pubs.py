#!/usr/bin/env python3
"""Fetch original PDFs for every publication in Nick Feamster's CV.

The master list is the ordered set of \\mkbib / \\mkbiba citations in the CV
LaTeX source (cv.tex), grouped by subsection (Theses, Journal, Books &
Chapters, Conference, Workshop).  Each key resolves against feamster.bib.

Pipeline:
  1. build_catalog() -> catalog.json   (ordered keys + category + [N] + metadata)
  2. fetch()         -> pdf/<year>/<key>.pdf + manifest.json   (OA/web waterfall)
  3. render()        -> README.md (CV-matching index) + MISSING.md

Usage:
  python3 fetch_pubs.py catalog            # (re)build catalog.json only
  python3 fetch_pubs.py fetch [--limit N] [--only KEY] [--force]
  python3 fetch_pubs.py render
  python3 fetch_pubs.py all                # catalog + fetch + render
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO = Path(__file__).resolve().parent.parent          # .../publications
CV_TEX = Path.home() / "Documents/CV/current/cv.tex"
BIB = Path.home() / "Documents/research/feamster.github.io/bib/feamster.bib"
PDF_DIR = REPO / "pdf"
CATALOG = REPO / "catalog.json"
MANIFEST = REPO / "manifest.json"
CACHE_DIR = REPO / ".cache"                             # cached API json responses
AUTH_DIR = REPO / ".auth"                               # cookies for proxied access

# UChicago EZproxy: paywalled hosts are reachable (when authenticated) at
# https://<host-with-dots-as-dashes>.proxy.uchicago.edu/<path>
PROXY_BASE = "proxy.uchicago.edu"
PAYWALL_HOSTS = {
    "dl.acm.org", "ieeexplore.ieee.org", "link.springer.com",
    "www.sciencedirect.com", "journals.sagepub.com", "onlinelibrary.wiley.com",
    "dial.acm.org", "doi.org", "www.tandfonline.com", "academic.oup.com",
}
PROXY_ENABLED = False  # set by --proxy
USE_S2 = False         # set by --use-s2 (Semantic Scholar; slow/rate-limited)

EMAIL = "feamster@gmail.com"
UA = f"feamster-pub-archive/1.0 (mailto:{EMAIL})"
HEADERS = {"User-Agent": UA}
# Browser-like headers for fetching PDFs from publisher/repository hosts that
# block non-browser agents.
DL_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
}
TIMEOUT = 25

# ---------------------------------------------------------------------------
# Minimal BibTeX parser (tolerant; bib uses mixed {} / "" / bare values)
# ---------------------------------------------------------------------------
def parse_bib(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    entries = {}
    i = 0
    n = len(text)
    while True:
        at = text.find("@", i)
        if at == -1:
            break
        # entry header: @type{key,
        m = re.match(r"@(\w+)\s*\{", text[at:])
        if not m:
            i = at + 1
            continue
        etype = m.group(1).lower()
        body_start = at + m.end()
        # find matching closing brace for the entry
        depth = 1
        j = body_start
        while j < n and depth > 0:
            c = text[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            j += 1
        body = text[body_start:j - 1]
        i = j
        # key is up to first comma
        comma = body.find(",")
        if comma == -1:
            continue
        key = body[:comma].strip()
        fields = parse_fields(body[comma + 1:])
        fields["__type__"] = etype
        entries[key] = fields
    return entries


def parse_fields(s):
    """Parse 'name = value, name = value, ...' with brace/quote aware values."""
    fields = {}
    i = 0
    n = len(s)
    while i < n:
        # skip whitespace/commas
        while i < n and s[i] in " \t\r\n,":
            i += 1
        # field name
        m = re.match(r"([A-Za-z][A-Za-z0-9_-]*)\s*=\s*", s[i:])
        if not m:
            break
        name = m.group(1).lower()
        i += m.end()
        if i >= n:
            break
        if s[i] == "{":
            depth = 1
            i += 1
            start = i
            while i < n and depth > 0:
                if s[i] == "{":
                    depth += 1
                elif s[i] == "}":
                    depth -= 1
                i += 1
            value = s[start:i - 1]
        elif s[i] == '"':
            i += 1
            start = i
            while i < n and s[i] != '"':
                i += 1
            value = s[start:i]
            i += 1
        else:
            start = i
            while i < n and s[i] not in ",\n":
                i += 1
            value = s[start:i]
        fields[name] = clean(value)
    return fields


def clean(v):
    v = re.sub(r"\s+", " ", v).strip()
    v = v.replace("{", "").replace("}", "")
    v = v.replace("\\&", "&").replace("\\_", "_")
    return v.strip()


# ---------------------------------------------------------------------------
# Parse the CV publications section: ordered (category, key, rate)
# ---------------------------------------------------------------------------
def strip_tex_comment(ln):
    """Remove a LaTeX line comment (unescaped %)."""
    out = []
    prev = ""
    for ch in ln:
        if ch == "%" and prev != "\\":
            break
        out.append(ch)
        prev = ch
    return "".join(out)


def parse_cv(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    # locate \section*{Publications}
    start = None
    for idx, ln in enumerate(lines):
        if re.search(r"\\section\*\{Publications\}", ln):
            start = idx
            break
    if start is None:
        raise SystemExit("Could not find \\section*{Publications} in cv.tex")
    cat = None
    out = []
    n = 0
    for ln in lines[start + 1:]:
        ln = strip_tex_comment(ln)
        if re.search(r"\\section\*\{", ln):  # next top-level section ends pubs
            break
        ms = re.search(r"\\subsection\*\{([^}]*)\}", ln)
        if ms:
            cat = ms.group(1).strip()
            continue
        for m in re.finditer(r"\\mkbiba?\{([^}]+)\}(?:\{([^}]*)\})?", ln):
            key = m.group(1).strip()
            rate = m.group(2)
            n += 1
            out.append({"n": n, "category": cat, "key": key,
                        "acceptance_rate": rate})
    return out


# ---------------------------------------------------------------------------
# Catalog: join CV order with bib metadata
# ---------------------------------------------------------------------------
def venue_of(f):
    return (f.get("booktitle") or f.get("journal") or f.get("school")
            or f.get("institution") or f.get("publisher") or "")


def build_catalog():
    bib = parse_bib(BIB)
    bib_ci = {k.lower(): v for k, v in bib.items()}  # bibtex keys are case-insensitive
    cv = parse_cv(CV_TEX)
    catalog = []
    missing_keys = []
    for item in cv:
        key = item["key"]
        f = bib.get(key) or bib_ci.get(key.lower())
        if f is None:
            missing_keys.append(key)
            entry = {**item, "title": None, "authors": None, "venue": None,
                     "year": None, "url": None, "doi": None, "bibtype": None,
                     "in_bib": False}
        else:
            entry = {
                **item,
                "title": f.get("title"),
                "authors": f.get("author"),
                "venue": venue_of(f),
                "year": f.get("year"),
                "url": f.get("url"),
                "doi": f.get("doi"),
                "eprint": f.get("eprint") or f.get("arxiv"),
                "bibtype": f.get("__type__"),
                "in_bib": True,
            }
        catalog.append(entry)
    CATALOG.write_text(json.dumps(catalog, indent=2, ensure_ascii=False))
    # summary
    from collections import Counter
    cats = Counter(e["category"] for e in catalog)
    print(f"CV publications: {len(catalog)}")
    for c, k in cats.items():
        print(f"  {k:3d}  {c}")
    print(f"with url: {sum(1 for e in catalog if e['url'])}  "
          f"with doi: {sum(1 for e in catalog if e['doi'])}")
    if missing_keys:
        print(f"\n!! {len(missing_keys)} CV keys NOT found in bib:")
        for k in missing_keys:
            print("   ", k)
    yrs = Counter(e["year"] for e in catalog if e["year"])
    print("\nyears:", dict(sorted(yrs.items(), reverse=True)))
    return catalog


# ---------------------------------------------------------------------------
# HTTP helpers + title matching
# ---------------------------------------------------------------------------
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def proxify(url):
    """Rewrite a paywalled-host URL through the UChicago EZproxy."""
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:
        return None
    host = p.netloc.lower()
    if host not in PAYWALL_HOSTS:
        return None
    newhost = host.replace(".", "-") + "." + PROXY_BASE
    return urllib.parse.urlunsplit((p.scheme or "https", newhost, p.path,
                                    p.query, p.fragment))


def load_cookies():
    """Load EZproxy/publisher cookies (Netscape cookies.txt) into the session."""
    cj = AUTH_DIR / "cookies.txt"
    if not cj.exists():
        return 0
    import http.cookiejar
    jar = http.cookiejar.MozillaCookieJar(str(cj))
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as ex:
        print(f"!! could not load cookies.txt: {ex}")
        return 0
    n = 0
    for c in jar:
        SESSION.cookies.set_cookie(c)
        n += 1
    return n


def http_get(url, **kw):
    """GET with simple retry/backoff on 429/5xx."""
    kw.setdefault("timeout", (8, TIMEOUT))  # (connect, read)
    kw.setdefault("allow_redirects", True)
    for attempt in range(4):
        try:
            r = SESSION.get(url, **kw)
        except requests.RequestException:
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503):
            time.sleep(2.0 * (attempt + 1))
            continue
        return r
    return None


def norm_title(s):
    if not s:
        return ""
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def title_tokens(s):
    return set(norm_title(s).split())


def title_sim(a, b):
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return inter / min(len(ta), len(tb))  # containment-style: robust to subtitles


def looks_like_pdf(content):
    return content[:5].startswith(b"%PDF")


def cache_get(name):
    p = CACHE_DIR / name
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None


def cache_put(name, obj):
    CACHE_DIR.mkdir(exist_ok=True)
    (CACHE_DIR / name).write_text(json.dumps(obj))


def qhash(*parts):
    return hashlib.sha1("||".join(parts).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Resolvers: each returns list of candidate dicts {pdf_url, landing, doi, src}
# ---------------------------------------------------------------------------
def openalex_lookup(title, year):
    cn = f"openalex_{qhash(title, str(year))}.json"
    data = cache_get(cn)
    if data is None:
        url = ("https://api.openalex.org/works?search="
               + urllib.parse.quote(title)
               + "&per-page=5&mailto=" + EMAIL)
        r = http_get(url)
        data = r.json() if r is not None and r.status_code == 200 else {"results": []}
        cache_put(cn, data)
        time.sleep(0.2)
    best = None
    best_sim = 0.0
    for w in data.get("results", []):
        sim = title_sim(title, w.get("title") or w.get("display_name") or "")
        # require Feamster authorship OR a very strong title match to accept
        authors = " ".join(
            (a.get("author") or {}).get("display_name", "")
            for a in (w.get("authorships") or [])
        ).lower()
        feam = "feamster" in authors
        score = sim + (0.25 if feam else 0.0)
        if year and str(w.get("publication_year")) == str(year):
            score += 0.1
        if score > best_sim:
            best_sim, best = score, w
    if not best:
        return None
    authors = " ".join(
        (a.get("author") or {}).get("display_name", "")
        for a in (best.get("authorships") or [])
    ).lower()
    sim = title_sim(title, best.get("title") or best.get("display_name") or "")
    # accept only if title matches well, or matches okay AND Feamster is an author
    if not (sim >= 0.8 or (sim >= 0.55 and "feamster" in authors)):
        return None
    doi = (best.get("doi") or "").replace("https://doi.org/", "") or None
    oa = best.get("open_access", {}) or {}
    bol = best.get("best_oa_location") or {}
    landing = (best.get("primary_location") or {}).get("landing_page_url")
    # gather every candidate URL: pdf_urls, oa_url, and landing pages (which we
    # will scrape for a citation_pdf_url meta tag).
    pdfs, pages = [], []
    if bol.get("pdf_url"):
        pdfs.append(bol["pdf_url"])
    if oa.get("oa_url"):
        pdfs.append(oa["oa_url"])
    for loc in best.get("locations", []) or []:
        if loc.get("pdf_url"):
            pdfs.append(loc["pdf_url"])
        if loc.get("landing_page_url"):
            pages.append(loc["landing_page_url"])
    if landing:
        pages.append(landing)
    return {"doi": doi, "landing": landing,
            "pdf_urls": dedup(pdfs), "pages": dedup(pages),
            "sim": round(sim, 2), "src": "openalex"}


def dedup(seq):
    seen, out = set(), []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def unpaywall_pdf(doi):
    if not doi:
        return None
    cn = f"unpaywall_{qhash(doi)}.json"
    data = cache_get(cn)
    if data is None:
        r = http_get(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={EMAIL}")
        data = r.json() if r is not None and r.status_code == 200 else {}
        cache_put(cn, data)
        time.sleep(0.2)
    bol = data.get("best_oa_location") or {}
    out = []
    if bol.get("url_for_pdf"):
        out.append(bol["url_for_pdf"])
    for loc in data.get("oa_locations", []) or []:
        if loc.get("url_for_pdf"):
            out.append(loc["url_for_pdf"])
    return out or None


def arxiv_pdf(title, year):
    cn = f"arxiv_{qhash(title)}.json"
    cached = cache_get(cn)
    if cached is not None:
        entries = cached
    else:
        url = ("http://export.arxiv.org/api/query?search_query=ti:"
               + urllib.parse.quote('"' + " ".join(norm_title(title).split()[:8]) + '"')
               + "&max_results=5")
        r = http_get(url)
        entries = []
        if r is not None and r.status_code == 200:
            for m in re.finditer(r"<entry>(.*?)</entry>", r.text, re.S):
                blk = m.group(1)
                t = re.search(r"<title>(.*?)</title>", blk, re.S)
                idm = re.search(r"<id>(.*?)</id>", blk, re.S)
                entries.append({"title": (t.group(1) if t else "").strip(),
                                "id": (idm.group(1) if idm else "").strip()})
        cache_put(cn, entries)
        time.sleep(0.3)
    for e in entries:
        if title_sim(title, e["title"]) >= 0.6 and "arxiv.org/abs/" in e["id"]:
            aid = e["id"].split("/abs/")[-1]
            return [f"https://arxiv.org/pdf/{aid}"]
    return None


def s2_pdf(title):
    cn = f"s2_{qhash(title)}.json"
    data = cache_get(cn)
    if data is None:
        url = ("https://api.semanticscholar.org/graph/v1/paper/search?query="
               + urllib.parse.quote(title)
               + "&limit=3&fields=title,openAccessPdf")
        r = http_get(url)
        data = r.json() if r is not None and r.status_code == 200 else {"data": []}
        cache_put(cn, data)
        time.sleep(1.2)
    for p in data.get("data", []) or []:
        if title_sim(title, p.get("title", "")) >= 0.6 and p.get("openAccessPdf"):
            u = p["openAccessPdf"].get("url")
            if u:
                return [u]
    return None


PDF_META_RE = re.compile(
    r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
    re.I)
PDF_META_RE2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']',
    re.I)


def extract_pdf_links(html, base_url):
    """Find PDF URLs inside an HTML landing page."""
    links = []
    for rx in (PDF_META_RE, PDF_META_RE2):
        links += rx.findall(html)
    # arxiv abs pages -> pdf
    for m in re.finditer(r'arxiv\.org/abs/([0-9]+\.[0-9]+)', html):
        links.append(f"https://arxiv.org/pdf/{m.group(1)}")
    # absolutize
    out = []
    for l in links:
        if l.startswith("//"):
            l = "https:" + l
        elif l.startswith("/"):
            l = urllib.parse.urljoin(base_url, l)
        out.append(l)
    return dedup(out)


# ---------------------------------------------------------------------------
# Playwright (authenticated UChicago EZproxy access)
# ---------------------------------------------------------------------------
AUTH_STATE = AUTH_DIR / "state.json"
_PW = {}
# Use the real installed Google Chrome (Duo's Universal Prompt behaves better in
# a recognized browser). Set to None to fall back to bundled Chromium.
PW_CHANNEL = "chrome"


def pw_start():
    """Start a headless browser context using the saved authenticated session.
    Prefers the persistent profile (.auth/userdata) written during login."""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    # Prefer state.json: it captures the session-scoped EZproxy cookies that a
    # persistent profile drops on disk.
    browser = pw.chromium.launch(headless=True, channel=PW_CHANNEL)
    state = str(AUTH_STATE) if AUTH_STATE.exists() else None
    ctx = browser.new_context(storage_state=state, accept_downloads=True)
    _PW.update(pw=pw, browser=browser, ctx=ctx)


def pw_stop():
    if _PW:
        try:
            _PW["ctx"].close()
            if _PW.get("browser"):
                _PW["browser"].close()
            _PW["pw"].stop()
        except Exception:
            pass
        _PW.clear()


def pw_get(url):
    try:
        r = _PW["ctx"].request.get(url, timeout=40000)
    except Exception:
        return None, None
    if not r.ok:
        return None, None
    try:
        return r.body(), r.headers.get("content-type", "")
    except Exception:
        return None, None


def do_login():
    """Open a real browser to the UChicago proxy; user completes SSO+Duo once;
    save the authenticated session to .auth/state.json. Uses a persistent
    profile so the login survives across runs, and auto-detects success by the
    presence of an EZproxy session cookie."""
    from playwright.sync_api import sync_playwright
    AUTH_DIR.mkdir(exist_ok=True)
    userdata = AUTH_DIR / "userdata"
    # Start the EZproxy -> Okta -> Duo flow directly for a real resource.
    start_url = ("https://login.proxy.uchicago.edu/login?qurl="
                 + urllib.parse.quote("https://dl.acm.org/", safe=""))
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(userdata), headless=False, accept_downloads=True,
            channel=PW_CHANNEL)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        print(">> LOG IN IN THE CHROME WINDOW THAT JUST OPENED (a separate automation profile).")
        print(">> Complete UChicago SSO + Duo, then just WAIT — do not close the window.")
        print(">> When you reach the ACM Digital Library, this auto-saves and exits.")
        try:
            page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        deadline = time.time() + 900
        ok = False
        while time.time() < deadline:
            try:
                cookies = ctx.cookies()
                url = page.url
            except Exception:
                break  # window closed; persistent profile already holds cookies
            # success = an EZproxy session cookie exists AND we're on the proxied
            # ACM host (not bounced to login/okta/duo)
            have_ezp = any("proxy.uchicago.edu" in c.get("domain", "")
                           for c in cookies)
            if have_ezp and "dl-acm-org.proxy.uchicago.edu" in url:
                ok = True
                break
            time.sleep(3)
        try:
            ctx.storage_state(path=str(AUTH_STATE))
            n_proxy = sum(1 for c in ctx.cookies()
                          if "proxy.uchicago.edu" in c.get("domain", ""))
            ctx.close()
        except Exception:
            n_proxy = -1
    print(f"session saved  (EZproxy cookies: {n_proxy}, "
          f"{'AUTHENTICATED ✓' if ok else 'NOT confirmed — rerun and wait until ACM loads'})")


def pdf_variants(url):
    """Derive likely direct-PDF URLs from a publisher landing URL."""
    out = []
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:
        return out
    host = p.netloc.lower().replace("-", ".")  # tolerate already-proxified hosts
    # ACM: /doi/10.x/y  -> /doi/pdf/10.x/y
    if "dl.acm.org" in host and "/doi/" in p.path and "/doi/pdf/" not in p.path:
        out.append(url.replace("/doi/", "/doi/pdf/", 1))
    # IEEE: /document/NNN -> /stamp/stamp.jsp?tp=&arnumber=NNN (renders the PDF)
    m = re.search(r"ieeexplore\.ieee\.org/(?:abstract/)?document/(\d+)", url)
    if m:
        base = url.split("ieeexplore")[0] + p.netloc + "/stamp/stamp.jsp?tp=&arnumber=" + m.group(1)
        out.append(base)
    # Springer: /chapter|/article/10.x/y -> /content/pdf/10.x%2Fy.pdf
    m = re.search(r"link\.springer\.com/(?:chapter|article|book)/(10\.\d+/[^?#]+)", url)
    if m:
        enc = m.group(1).replace("/", "%2F")
        out.append(f"https://{p.netloc}/content/pdf/{enc}.pdf")
    return out


def acm_pdf_from_doi(doi):
    if doi and doi.lower().startswith("10.1145/"):
        return f"https://dl.acm.org/doi/pdf/{doi}"
    return None


def fetch_bytes(url):
    """GET a URL; return (content, content_type) or (None, None).
    In proxy mode, route through the authenticated Playwright session."""
    if PROXY_ENABLED and _PW:
        return pw_get(url)
    r = http_get(url, headers=DL_HEADERS)
    if r is None or r.status_code != 200:
        return None, None
    return r.content, r.headers.get("content-type", "")


def acquire_pdf(urls, dest, depth=0):
    """Try each url. If a url is a PDF, save it. If it is HTML, scrape it for a
    citation_pdf_url and follow that (one level). Return the winning URL."""
    # When proxy auth is on, also try the EZproxy-rewritten form of each url,
    # plus publisher-specific direct-PDF variants (ACM /doi/pdf/ etc.).
    expanded = []
    for u in urls or []:
        if not u:
            continue
        for v in [u] + pdf_variants(u):
            expanded.append(v)
            if PROXY_ENABLED:
                pu = proxify(v)
                if pu:
                    expanded.append(pu)
    for u in expanded:
        # Without proxy auth, downloading from paywalled hosts just 403s and
        # wastes time — skip and let the authenticated --proxy sweep handle it.
        if not PROXY_ENABLED:
            try:
                if urllib.parse.urlsplit(u).netloc.lower() in PAYWALL_HOSTS:
                    continue
            except Exception:
                pass
        content, ct = fetch_bytes(u)
        if content is None:
            continue
        head = content[:2048].lstrip()
        if head.startswith(b"%PDF") or (("pdf" in ct) and head.startswith(b"%PDF")):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            if (pdf_pages(dest) or 0) > 0:
                return u
            dest.unlink(missing_ok=True)
            continue
        # HTML landing page: look for an embedded PDF link, follow once
        if depth == 0 and (b"<html" in content[:4096].lower() or "html" in ct):
            try:
                html = content.decode("utf-8", "replace")
            except Exception:
                html = ""
            extracted = [e for e in extract_pdf_links(html, u) if e != u]
            if extracted:
                won = acquire_pdf(extracted, dest, depth=1)
                if won:
                    return won
    return None


def pdf_pages(path):
    try:
        out = subprocess.run(["pdfinfo", str(path)], capture_output=True,
                             text=True, timeout=30)
        m = re.search(r"Pages:\s+(\d+)", out.stdout)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def sha256(path):
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Fetch loop
# ---------------------------------------------------------------------------
def fetch(limit=None, only=None, force=False):
    catalog = json.loads(CATALOG.read_text())
    manifest = {}
    if MANIFEST.exists():
        manifest = {m["key"]: m for m in json.loads(MANIFEST.read_text())}
    if PROXY_ENABLED:
        if not (AUTH_DIR / "userdata").exists() and not AUTH_STATE.exists():
            raise SystemExit("No saved session. Run:  python3 tools/fetch_pubs.py login")
        pw_start()
        print("proxy: authenticated Playwright session active")
    results = []
    done = 0
    for e in catalog:
        key = e["key"]
        if only and key != only:
            if key in manifest:
                results.append(manifest[key])
            continue
        year = e.get("year") or "unknown"
        dest = PDF_DIR / str(year) / f"{key}.pdf"
        rec = {**{k: e[k] for k in ("n", "category", "key", "title", "year", "doi", "url")}}

        if dest.exists() and not force:
            rec.update(status="ok", source=manifest.get(key, {}).get("source", "existing"),
                       pdf_path=str(dest.relative_to(REPO)),
                       sha256=sha256(dest), pages=pdf_pages(dest),
                       source_url=manifest.get(key, {}).get("source_url"))
            results.append(rec)
            continue

        title = e.get("title") or ""
        doi = e.get("doi")
        landing = None
        won_url = None
        source = None

        def attempt(urls, src):
            nonlocal won_url, source
            if won_url or not urls:
                return
            w = acquire_pdf(urls, dest)
            if w:
                won_url, source = w, src

        # 0. arXiv id straight from the bib (cheapest, cleanest)
        if e.get("eprint"):
            attempt([f"https://arxiv.org/pdf/{e['eprint']}"], "arxiv")

        # 1. OpenAlex: gives doi + oa pdf_urls + landing pages (scraped for pdf)
        oa = openalex_lookup(title, year) if title else None
        if oa:
            doi = doi or oa["doi"]
            landing = oa["landing"]
            attempt(oa["pdf_urls"], "openalex")
            attempt(oa.get("pages"), "openalex-landing")

        # 2. Unpaywall by DOI
        if doi:
            attempt(unpaywall_pdf(doi), "unpaywall")

        # 3. arXiv by title search
        if title:
            attempt(arxiv_pdf(title, year), "arxiv")

        # 4. bib url directly (usenix/ndss/arxiv direct, or landing to scrape)
        if e.get("url"):
            attempt([e["url"]], "biburl")

        # 5. DOI: ACM direct-PDF first, then the DOI landing page (proxy)
        if doi:
            acm = acm_pdf_from_doi(doi)
            if acm:
                attempt([acm], "acm-doi")
            attempt([f"https://doi.org/{doi}"], "doi")

        # 6. Semantic Scholar (last; rate-limited, slow) — opt-in via --use-s2
        if title and USE_S2 and not PROXY_ENABLED:
            attempt(s2_pdf(title), "s2")

        if won_url:
            rec.update(status="ok", source=source, source_url=won_url,
                       landing=landing, doi=doi,
                       pdf_path=str(dest.relative_to(REPO)),
                       sha256=sha256(dest), pages=pdf_pages(dest))
            print(f"[OK  ] [{e['n']:>3}] {key:35} <- {source} ({rec['pages']}p)")
        else:
            rec.update(status="missing", source=None, landing=landing, doi=doi)
            print(f"[MISS] [{e['n']:>3}] {key:35} doi={doi or '-'} url={e.get('url') or '-'}")
        results.append(rec)

        done += 1
        if limit and done >= limit:
            # keep prior manifest entries for untouched keys
            touched = {r["key"] for r in results}
            for k, m in manifest.items():
                if k not in touched:
                    results.append(m)
            break

    pw_stop()
    results.sort(key=lambda r: r["n"])
    MANIFEST.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    ok = sum(1 for r in results if r.get("status") == "ok")
    print(f"\n=== {ok}/{len(results)} resolved; "
          f"{len(results)-ok} missing ===")


# ---------------------------------------------------------------------------
# Render README + MISSING
# ---------------------------------------------------------------------------
CV_ORDER = ["Theses", "Journal Publications", "Books and Book Chapters",
            "Conference Publications", "Workshop Publications"]


def fmt_authors(a):
    if not a:
        return ""
    parts = [p.strip() for p in re.split(r"\s+and\s+", a)]
    if len(parts) > 6:
        parts = parts[:6] + ["et al."]
    return ", ".join(parts)


def render():
    catalog = json.loads(CATALOG.read_text())
    man = {m["key"]: m for m in json.loads(MANIFEST.read_text())} if MANIFEST.exists() else {}
    by_cat = {}
    for e in catalog:
        by_cat.setdefault(e["category"], []).append(e)

    ok = sum(1 for m in man.values() if m.get("status") == "ok")
    lines = [
        "# Nick Feamster — Publications Archive",
        "",
        "Original copies (PDFs) of every publication listed in the **Publications** "
        "section of the [CV](https://github.com/feamster/cv). Numbering and grouping "
        "match the CV exactly.",
        "",
        f"**Status:** {ok} / {len(catalog)} PDFs archived.  "
        f"Source of truth: `cv.tex` `\\mkbib` list + `feamster.bib`.  "
        "Regenerate with `tools/fetch_pubs.py`.",
        "",
    ]
    for cat in CV_ORDER + [c for c in by_cat if c not in CV_ORDER]:
        items = by_cat.get(cat)
        if not items:
            continue
        lines.append(f"## {cat}")
        lines.append("")
        for e in items:
            m = man.get(e["key"], {})
            n = e["n"]
            authors = fmt_authors(e.get("authors"))
            title = (e.get("title") or e["key"]).strip().rstrip(".")
            venue = e.get("venue") or ""
            yr = e.get("year") or ""
            if m.get("status") == "ok":
                link = f"[📄 PDF]({m['pdf_path']})"
            else:
                link = "⚠️ _missing_"
            meta = " · ".join(x for x in [venue, str(yr)] if x)
            src = ""
            if m.get("doi"):
                src = f" · [doi](https://doi.org/{m['doi']})"
            elif m.get("source_url"):
                src = f" · [source]({m['source_url']})"
            lines.append(f"- **[{n}]** {authors}. *{title}*. {meta}. {link}{src}")
        lines.append("")
    (REPO / "README.md").write_text("\n".join(lines))

    # MISSING.md
    miss = [m for m in man.values() if m.get("status") != "ok"]
    miss.sort(key=lambda r: r["n"])
    ml = [f"# Missing PDFs ({len(miss)})", "",
          "Papers not auto-resolved via OA/web. Drop the PDF into the listed path "
          "(named `<bibkey>.pdf`) and rerun `fetch_pubs.py render`.", ""]
    for m in miss:
        yr = m.get("year") or "unknown"
        ml.append(f"- **[{m['n']}]** `{m['key']}` — {m.get('title') or ''}")
        ml.append(f"  - expected: `pdf/{yr}/{m['key']}.pdf`")
        if m.get("doi"):
            ml.append(f"  - doi: https://doi.org/{m['doi']}")
        if m.get("url"):
            ml.append(f"  - bib url: {m['url']}")
        if m.get("landing"):
            ml.append(f"  - landing: {m['landing']}")
    (REPO / "MISSING.md").write_text("\n".join(ml))
    print(f"README.md + MISSING.md written ({ok} ok, {len(miss)} missing)")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["catalog", "fetch", "render", "all", "login"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--proxy", action="store_true",
                    help="route paywalled hosts through proxy.uchicago.edu "
                         "(requires an authenticated session via `login`)")
    ap.add_argument("--use-s2", action="store_true",
                    help="also query Semantic Scholar (slow, rate-limited)")
    args = ap.parse_args()

    global PROXY_ENABLED, USE_S2
    USE_S2 = args.use_s2
    if args.proxy:
        PROXY_ENABLED = True
        n = load_cookies()
        print(f"proxy mode ON; loaded {n} cookies from .auth/cookies.txt")

    if args.cmd == "login":
        do_login()
        return

    if args.cmd in ("catalog", "all"):
        build_catalog()
    if args.cmd in ("fetch", "all"):
        fetch(limit=args.limit, only=args.only, force=args.force)
    if args.cmd in ("render", "all"):
        render()


if __name__ == "__main__":
    main()
