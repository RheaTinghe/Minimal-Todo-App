#!/usr/bin/env python3
"""Year-end abstract digest for statistics / management science journals.

Pulls one full year of articles from a set of journals and renders a single
print-ready HTML page (title, authors, citation, abstract, DOI link, library
proxy link) so a whole year can be skimmed in one sitting and saved as a PDF.

Metadata and abstracts come from open scholarly APIs, in this order:

  1. Crossref      https://api.crossref.org   (article list + abstract when deposited)
  2. OpenAlex      https://api.openalex.org   (fills abstracts Crossref is missing)
  3. Semantic Scholar (optional, --use-s2)    (last-resort abstract fill)

No publisher login is used or needed: abstracts are open metadata. The library
proxy links in the output are there so *you* can click through to the full text
with your own credentials when something looks interesting.

Standard library only - no pip install required. Python 3.8+.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

VERSION = "1.0"
HERE = Path(__file__).resolve().parent

CROSSREF_API = "https://api.crossref.org"
OPENALEX_API = "https://api.openalex.org"
S2_API = "https://api.semanticscholar.org/graph/v1"

# Front matter that is not a research article. Matched against the title start.
NON_ARTICLE_TITLE = re.compile(
    r"^\s*(correction|corrigend|erratum|errata|retraction|withdraw|"
    r"editorial|editor'?s? note|from the editor|comment on|rejoinder|discussion of|"
    r"book review|acknowledg|list of referees|reviewer|index to volume|"
    r"front matter|back matter|title page|masthead|table of contents|issue information)",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class Http:
    """Tiny polite HTTP/JSON client with retries and rate limiting."""

    def __init__(self, mailto: str, delay: float = 0.25, retries: int = 4, timeout: int = 60):
        self.mailto = mailto
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self._last_call = 0.0
        self.user_agent = f"paper-digest/{VERSION} (https://github.com/; mailto:{mailto})"

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_call
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last_call = time.monotonic()

    def get_json(self, url: str, quiet_404: bool = False):
        return self._request(url, None, quiet_404)

    def post_json(self, url: str, payload: dict):
        return self._request(url, json.dumps(payload).encode("utf-8"), False)

    def _request(self, url: str, body, quiet_404: bool):
        last_error = None
        for attempt in range(self.retries):
            self._throttle()
            req = urllib.request.Request(url, data=body)
            req.add_header("User-Agent", self.user_agent)
            req.add_header("Accept", "application/json")
            if body is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 404 and quiet_404:
                    return None
                last_error = f"HTTP {exc.code} {exc.reason}"
                # 429/5xx are worth retrying; other 4xx are not.
                if exc.code not in (429, 500, 502, 503, 504):
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = str(exc)
            backoff = 2 ** attempt
            if attempt < self.retries - 1:
                warn(f"  request failed ({last_error}); retrying in {backoff}s")
                time.sleep(backoff)
        warn(f"  giving up on {url[:110]}... -> {last_error}")
        return None


def warn(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def info(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------
# Text cleanup
# --------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_ABSTRACT_LABEL_RE = re.compile(r"^\s*abstract[:.\s-]*", re.IGNORECASE)


def clean_text(raw) -> str:
    """Strip JATS/HTML markup and collapse whitespace."""
    if not raw:
        return ""
    if isinstance(raw, list):
        raw = " ".join(str(x) for x in raw)
    text = str(raw)
    # Keep paragraph boundaries readable before dropping tags.
    text = re.sub(r"</(jats:)?(p|sec|title)>", " \n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = text.replace(" ", " ").replace(" ", " ")
    text = _WS_RE.sub(" ", text).strip()
    text = _ABSTRACT_LABEL_RE.sub("", text, count=1)
    return text.strip()


def abstract_from_inverted_index(inverted) -> str:
    """Rebuild plain text from OpenAlex's abstract_inverted_index."""
    if not inverted:
        return ""
    slots = []
    for word, positions in inverted.items():
        for pos in positions:
            slots.append((pos, word))
    slots.sort(key=lambda pair: pair[0])
    return clean_text(" ".join(word for _, word in slots))


def format_authors(authors) -> str:
    names = []
    for a in authors or []:
        given = (a.get("given") or "").strip()
        family = (a.get("family") or "").strip()
        if family and given:
            names.append(f"{given} {family}")
        elif family or given:
            names.append(family or given)
        elif a.get("name"):
            names.append(a["name"].strip())
    return ", ".join(names)


# --------------------------------------------------------------------------
# Crossref
# --------------------------------------------------------------------------

CROSSREF_SELECT = ",".join(
    [
        "DOI", "title", "author", "abstract", "issued", "published-print",
        "published-online", "container-title", "short-container-title",
        "volume", "issue", "page", "type", "URL", "subject", "reference-count",
        "is-referenced-by-count",
    ]
)


def fetch_crossref_journal(http: Http, issn: str, year: int, rows: int = 100) -> list:
    """Every Crossref work for one ISSN whose earliest publication date is in `year`."""
    items = []
    cursor = "*"
    page = 0
    while True:
        params = {
            "filter": f"from-pub-date:{year}-01-01,until-pub-date:{year}-12-31,type:journal-article",
            "rows": str(rows),
            "cursor": cursor,
            "select": CROSSREF_SELECT,
            "mailto": http.mailto,
        }
        url = f"{CROSSREF_API}/journals/{issn}/works?" + urllib.parse.urlencode(params, safe=":/|")
        data = http.get_json(url)
        if not data or "message" not in data:
            break
        message = data["message"]
        batch = message.get("items") or []
        items.extend(batch)
        page += 1
        info(f"    ISSN {issn}: page {page}, +{len(batch)} (total {len(items)})")
        next_cursor = message.get("next-cursor")
        if not batch or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        if len(items) > 5000:  # safety valve against a runaway loop
            warn(f"    ISSN {issn}: stopping at 5000 records")
            break
    return items


def pick_year(item: dict) -> int:
    """Publication year, preferring the issue (print) year over the online-first year."""
    for key in ("published-print", "issued", "published-online", "published"):
        parts = (item.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            return int(parts[0][0])
    return 0


def pick_date(item: dict) -> str:
    for key in ("published-print", "published-online", "issued", "published"):
        parts = (item.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            nums = [int(x) for x in parts[0] if x is not None]
            nums += [1] * (3 - len(nums))
            return f"{nums[0]:04d}-{nums[1]:02d}-{nums[2]:02d}"
    return ""


def normalize(item: dict, journal_name: str) -> dict:
    title = clean_text(item.get("title"))
    return {
        "journal": journal_name,
        "title": title,
        "authors": format_authors(item.get("author")),
        "doi": (item.get("DOI") or "").lower(),
        "url": item.get("URL") or (f"https://doi.org/{item['DOI']}" if item.get("DOI") else ""),
        "volume": item.get("volume") or "",
        "issue": item.get("issue") or "",
        "pages": item.get("page") or "",
        "date": pick_date(item),
        "year": pick_year(item),
        "abstract": clean_text(item.get("abstract")),
        "abstract_source": "crossref" if clean_text(item.get("abstract")) else "",
        "cited_by": item.get("is-referenced-by-count") or 0,
        "subjects": "; ".join(item.get("subject") or []),
    }


def looks_like_front_matter(rec: dict) -> bool:
    title = rec["title"]
    if not title or len(title) < 8:
        return True
    if NON_ARTICLE_TITLE.match(title):
        return True
    # A "paper" with no authors and no pages is almost always front matter.
    if not rec["authors"] and not rec["pages"]:
        return True
    return False


# --------------------------------------------------------------------------
# Abstract back-fill
# --------------------------------------------------------------------------


def fill_from_openalex(http: Http, records: list, batch_size: int = 45) -> int:
    missing = [r for r in records if not r["abstract"] and r["doi"]]
    if not missing:
        return 0
    filled = 0
    for start in range(0, len(missing), batch_size):
        chunk = missing[start : start + batch_size]
        by_doi = {r["doi"]: r for r in chunk}
        doi_filter = "|".join(f"https://doi.org/{d}" for d in by_doi)
        params = {
            "filter": f"doi:{doi_filter}",
            "per-page": str(batch_size + 5),
            "mailto": http.mailto,
        }
        url = f"{OPENALEX_API}/works?" + urllib.parse.urlencode(params, safe=":/|")
        data = http.get_json(url)
        for work in (data or {}).get("results", []) or []:
            doi = (work.get("doi") or "").lower().replace("https://doi.org/", "")
            rec = by_doi.get(doi)
            if not rec:
                continue
            text = abstract_from_inverted_index(work.get("abstract_inverted_index"))
            if text:
                rec["abstract"] = text
                rec["abstract_source"] = "openalex"
                filled += 1
        info(f"    OpenAlex: {min(start + batch_size, len(missing))}/{len(missing)} checked, {filled} filled")
    return filled


def fill_from_semantic_scholar(http: Http, records: list, batch_size: int = 100) -> int:
    missing = [r for r in records if not r["abstract"] and r["doi"]]
    if not missing:
        return 0
    filled = 0
    for start in range(0, len(missing), batch_size):
        chunk = missing[start : start + batch_size]
        by_doi = {r["doi"]: r for r in chunk}
        payload = {"ids": [f"DOI:{d}" for d in by_doi]}
        data = http.post_json(f"{S2_API}/paper/batch?fields=externalIds,abstract", payload)
        for paper in data or []:
            if not paper:
                continue
            doi = ((paper.get("externalIds") or {}).get("DOI") or "").lower()
            rec = by_doi.get(doi)
            text = clean_text(paper.get("abstract"))
            if rec and text:
                rec["abstract"] = text
                rec["abstract_source"] = "semantic-scholar"
                filled += 1
        info(f"    Semantic Scholar: {min(start + batch_size, len(missing))}/{len(missing)} checked, {filled} filled")
    return filled


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def proxy_link(doi: str, prefix: str) -> str:
    if not doi or not prefix:
        return ""
    return prefix + urllib.parse.quote(f"https://doi.org/{doi}", safe=":/")


def keyword_pattern(keywords: list):
    terms = [re.escape(k.strip()) for k in keywords if k.strip()]
    if not terms:
        return None
    return re.compile(r"(" + "|".join(terms) + r")", re.IGNORECASE)


def highlight(escaped_text: str, pattern) -> str:
    if not pattern:
        return escaped_text
    return pattern.sub(r"<mark>\1</mark>", escaped_text)


def plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def citation_line(rec: dict) -> str:
    bits = []
    if rec["volume"]:
        vol = rec["volume"]
        if rec["issue"]:
            vol += f"({rec['issue']})"
        bits.append(vol)
    if rec["pages"]:
        bits.append(rec["pages"])
    tail = ", ".join(bits)
    head = str(rec["year"]) if rec["year"] else ""
    if head and tail:
        return f"{head}, {tail}"
    return head or tail or rec["journal"]


CSS = """
:root {
  --ink: #16181d;
  --muted: #6b7280;
  --line: #e2e5ea;
  --accent: #9b1c1c;
  --bg: #ffffff;
}
* { box-sizing: border-box; }
body {
  margin: 0 auto;
  padding: 32px 28px 80px;
  max-width: 860px;
  background: var(--bg);
  color: var(--ink);
  font: 16px/1.6 "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, "Songti SC", "Noto Serif CJK SC", serif;
}
header.masthead { border-bottom: 3px double var(--ink); padding-bottom: 14px; margin-bottom: 8px; }
h1 { font-size: 27px; margin: 0 0 6px; letter-spacing: -0.01em; }
.sub { color: var(--muted); font-size: 13.5px; }
.toolbar {
  position: sticky; top: 0; z-index: 5;
  background: var(--bg); border-bottom: 1px solid var(--line);
  padding: 10px 0; margin-bottom: 20px;
  display: flex; gap: 10px; flex-wrap: wrap; align-items: center;
  font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; font-size: 13px;
}
.toolbar input[type=search] {
  flex: 1 1 240px; min-width: 180px; padding: 7px 10px;
  border: 1px solid var(--line); border-radius: 6px; font-size: 13px;
}
.toolbar label { color: var(--muted); display: inline-flex; gap: 5px; align-items: center; cursor: pointer; }
.toolbar button {
  padding: 7px 12px; border: 1px solid var(--line); border-radius: 6px;
  background: #f7f8fa; cursor: pointer; font-size: 13px;
}
.toolbar button:hover { background: #eef0f4; }
nav.toc { margin: 0 0 34px; font-size: 14px; }
nav.toc h2 { font-size: 15px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); margin: 0 0 8px; }
nav.toc ol { margin: 0; padding-left: 22px; }
nav.toc li { margin: 3px 0; }
nav.toc a { color: var(--ink); text-decoration: none; }
nav.toc a:hover { text-decoration: underline; }
section.journal { margin-bottom: 40px; }
h2.journal-name {
  font-size: 20px; margin: 34px 0 4px; padding-bottom: 6px;
  border-bottom: 2px solid var(--ink);
}
.journal-meta { color: var(--muted); font-size: 13px; margin-bottom: 18px; }
article.entry {
  border-bottom: 1px solid var(--line);
  padding: 16px 0 18px;
  break-inside: avoid; page-break-inside: avoid;
}
article.entry.hit { border-left: 3px solid var(--accent); padding-left: 14px; margin-left: -17px; }
.entry-head { display: flex; gap: 10px; align-items: baseline; }
.tick {
  flex: 0 0 auto; width: 13px; height: 13px; margin-top: 6px;
  border: 1.5px solid #9aa1ab; border-radius: 3px;
}
h3.title { font-size: 17px; margin: 0; font-weight: 600; line-height: 1.35; }
h3.title a { color: var(--ink); text-decoration: none; }
h3.title a:hover { color: var(--accent); }
.authors { font-size: 14px; color: #3b4048; margin: 5px 0 2px; }
.meta { font-size: 12.5px; color: var(--muted); font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
.meta a { color: var(--muted); }
.meta > span { white-space: nowrap; }
.meta .sep { margin: 0 6px; opacity: 0.5; display: inline; }
p.abstract { margin: 9px 0 0; font-size: 14.6px; text-align: justify; hyphens: auto; }
p.abstract.none { color: var(--muted); font-style: italic; }
mark { background: #fff2a8; padding: 0 1px; }
.badge {
  display: inline-block; font-size: 11px; padding: 1px 6px; border-radius: 999px;
  background: #f1f3f7; color: var(--muted); font-family: system-ui, sans-serif;
}
.badge.hit { background: #fdeaea; color: var(--accent); }
footer.colophon { margin-top: 50px; padding-top: 14px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12.5px; }
@media print {
  body { max-width: none; padding: 0; font-size: 10.5pt; }
  .toolbar, .no-print { display: none !important; }
  h2.journal-name { break-before: page; page-break-before: always; }
  section.journal:first-of-type h2.journal-name { break-before: auto; page-break-before: auto; }
  a { color: inherit; text-decoration: none; }
  p.abstract { font-size: 9.6pt; }
  article.entry.hit { border-left: 2px solid #000; }
}
@page { margin: 16mm 14mm; }
"""

JS = """
(function () {
  var box = document.getElementById('q');
  var onlyHits = document.getElementById('onlyhits');
  var count = document.getElementById('count');
  var entries = Array.prototype.slice.call(document.querySelectorAll('article.entry'));

  function apply() {
    var q = (box.value || '').trim().toLowerCase();
    var hitsOnly = onlyHits.checked;
    var shown = 0;
    entries.forEach(function (el) {
      var textOk = !q || el.getAttribute('data-text').indexOf(q) !== -1;
      var hitOk = !hitsOnly || el.classList.contains('hit');
      var show = textOk && hitOk;
      el.style.display = show ? '' : 'none';
      if (show) shown++;
    });
    document.querySelectorAll('section.journal').forEach(function (sec) {
      var any = Array.prototype.some.call(sec.querySelectorAll('article.entry'), function (e) {
        return e.style.display !== 'none';
      });
      sec.style.display = any ? '' : 'none';
    });
    count.textContent = shown + ' / ' + entries.length;
  }

  box.addEventListener('input', apply);
  onlyHits.addEventListener('change', apply);
  document.getElementById('reset').addEventListener('click', function () {
    box.value = ''; onlyHits.checked = false; apply();
  });
  apply();
})();
"""


def render_html(records: list, year: int, keywords: list, proxy_prefix: str, proxy_label: str) -> str:
    pattern = keyword_pattern(keywords)
    by_journal = {}
    for rec in records:
        by_journal.setdefault(rec["journal"], []).append(rec)

    for items in by_journal.values():
        items.sort(key=lambda r: (r["date"], r["volume"], r["pages"], r["title"]))

    total = len(records)
    hits = sum(1 for r in records if r.get("hit"))
    with_abs = sum(1 for r in records if r["abstract"])
    generated = date.today().isoformat()
    esc = html.escape

    out = []
    out.append("<!doctype html>")
    out.append('<html lang="en"><head><meta charset="utf-8">')
    out.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    out.append(f"<title>{year} Journal Abstracts Digest</title>")
    out.append(f"<style>{CSS}</style></head><body>")

    out.append('<header class="masthead">')
    out.append(f"<h1>{year} 年度期刊摘要汇编</h1>")
    out.append(
        f'<div class="sub">{plural(total, "article")} &middot; {plural(len(by_journal), "journal")} &middot; '
        f"{with_abs} with abstracts &middot; generated {generated}</div>"
    )
    if keywords:
        out.append(
            f'<div class="sub">Keywords highlighted: <strong>{esc(", ".join(keywords))}</strong> '
            f'&mdash; {plural(hits, "matching article")}</div>'
        )
    out.append("</header>")

    out.append('<div class="toolbar no-print">')
    out.append('<input type="search" id="q" placeholder="Filter by title / author / abstract...">')
    out.append('<label><input type="checkbox" id="onlyhits"> only keyword matches</label>')
    out.append('<button id="reset" type="button">Reset</button>')
    out.append('<button onclick="window.print()" type="button">Print / Save as PDF</button>')
    out.append('<span class="badge" id="count"></span>')
    out.append("</div>")

    out.append('<nav class="toc"><h2>Contents</h2><ol>')
    for journal in by_journal:
        anchor = re.sub(r"[^a-z0-9]+", "-", journal.lower()).strip("-")
        n = len(by_journal[journal])
        jhits = sum(1 for r in by_journal[journal] if r.get("hit"))
        extra = f' <span class="badge hit">{jhits} match</span>' if jhits else ""
        out.append(f'<li><a href="#{anchor}">{esc(journal)}</a> &mdash; {plural(n, "article")}{extra}</li>')
    out.append("</ol></nav>")

    for journal in by_journal:
        items = by_journal[journal]
        anchor = re.sub(r"[^a-z0-9]+", "-", journal.lower()).strip("-")
        out.append('<section class="journal">')
        out.append(f'<h2 class="journal-name" id="{anchor}">{esc(journal)}</h2>')
        volumes = sorted({r["volume"] for r in items if r["volume"]}, key=lambda v: (len(v), v))
        vol_text = f"volume(s) {', '.join(volumes)} &middot; " if volumes else ""
        out.append(f'<div class="journal-meta">{vol_text}{plural(len(items), "article")} in {year}</div>')

        for rec in items:
            searchable = " ".join(
                [rec["title"], rec["authors"], rec["abstract"]]
            ).lower().replace('"', " ")
            classes = "entry hit" if rec.get("hit") else "entry"
            out.append(f'<article class="{classes}" data-text="{esc(searchable, quote=True)}">')
            out.append('<div class="entry-head"><div class="tick"></div><div>')
            link = rec["url"] or (f"https://doi.org/{rec['doi']}" if rec["doi"] else "")
            title_html = highlight(esc(rec["title"]), pattern)
            if link:
                out.append(f'<h3 class="title"><a href="{esc(link, quote=True)}">{title_html}</a></h3>')
            else:
                out.append(f'<h3 class="title">{title_html}</h3>')
            if rec["authors"]:
                out.append(f'<div class="authors">{esc(rec["authors"])}</div>')

            meta = [esc(citation_line(rec))]
            if rec["date"]:
                meta.append(esc(rec["date"]))
            if rec["doi"]:
                meta.append(f'<a href="https://doi.org/{esc(rec["doi"], quote=True)}">doi:{esc(rec["doi"])}</a>')
            plink = proxy_link(rec["doi"], proxy_prefix)
            if plink:
                meta.append(f'<a href="{esc(plink, quote=True)}">{esc(proxy_label)}</a>')
            if rec["cited_by"]:
                meta.append(f'cited {rec["cited_by"]}x')
            sep = '<span class="sep">|</span> '
            meta_html = sep.join(f"<span>{m}</span>" for m in meta)
            out.append(f'<div class="meta">{meta_html}</div>')
            out.append("</div></div>")

            if rec["abstract"]:
                out.append(f'<p class="abstract">{highlight(esc(rec["abstract"]), pattern)}</p>')
            else:
                out.append('<p class="abstract none">No abstract in the open metadata &mdash; open the article link to read it.</p>')
            out.append("</article>")
        out.append("</section>")

    out.append('<footer class="colophon">')
    out.append(
        "Metadata from Crossref, OpenAlex and (optionally) Semantic Scholar. "
        "Abstracts are open metadata; full texts are reached through your own library credentials "
        f"via the &ldquo;{esc(proxy_label)}&rdquo; links. Generated by paper-digest v{VERSION}."
    )
    out.append("</footer>")
    out.append(f"<script>{JS}</script>")
    out.append("</body></html>")
    return "\n".join(out)


CSV_FIELDS = [
    "journal", "year", "date", "volume", "issue", "pages", "title", "authors",
    "doi", "url", "cited_by", "hit", "abstract_source", "abstract",
]


def write_csv(records: list, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for rec in sorted(records, key=lambda r: (r["journal"], r["date"], r["title"])):
            row = dict(rec)
            row["hit"] = "yes" if rec.get("hit") else ""
            writer.writerow(row)


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


def load_journals(config_path: Path, selection: str) -> list:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    journals = config["journals"]
    by_key = {j["key"]: j for j in journals}
    if selection in ("", "default"):
        chosen = [j for j in journals if j.get("default")]
    elif selection == "all":
        chosen = journals
    else:
        chosen = []
        for key in [k.strip() for k in selection.split(",") if k.strip()]:
            if key not in by_key:
                raise SystemExit(
                    f"Unknown journal key '{key}'. Available: {', '.join(sorted(by_key))}"
                )
            chosen.append(by_key[key])
    if not chosen:
        raise SystemExit("No journals selected.")
    return chosen


def collect(args) -> list:
    http = Http(args.mailto, delay=args.delay)
    journals = load_journals(Path(args.config), args.journals)
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    records = []
    seen_dois = set()
    for journal in journals:
        info(f"\n[{journal['name']}]")
        raw_items = []
        for issn in journal["issn"]:
            cache_file = cache_dir / f"crossref_{issn}_{args.year}.json"
            if cache_file.exists() and not args.refresh:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                info(f"    ISSN {issn}: {len(cached)} records from cache")
                raw_items.extend(cached)
                continue
            fetched = fetch_crossref_journal(http, issn, args.year)
            cache_file.write_text(json.dumps(fetched, ensure_ascii=False), encoding="utf-8")
            raw_items.extend(fetched)

        kept, dropped_dupe, dropped_front, dropped_year = 0, 0, 0, 0
        for item in raw_items:
            rec = normalize(item, journal["name"])
            if not rec["doi"] or rec["doi"] in seen_dois:
                dropped_dupe += 1
                continue
            if args.strict_year and rec["year"] and rec["year"] != args.year:
                dropped_year += 1
                continue
            if not args.include_front_matter and looks_like_front_matter(rec):
                dropped_front += 1
                continue
            seen_dois.add(rec["doi"])
            records.append(rec)
            kept += 1
        info(
            f"    kept {kept}"
            f" (skipped {dropped_dupe} duplicate, {dropped_front} front-matter"
            f"{f', {dropped_year} other-year' if args.strict_year else ''})"
        )

    if not records:
        return records

    missing = sum(1 for r in records if not r["abstract"])
    info(f"\nAbstracts from Crossref: {len(records) - missing}/{len(records)}")
    if missing and not args.no_openalex:
        info("  Filling gaps from OpenAlex...")
        fill_from_openalex(http, records)
    if args.use_s2 and any(not r["abstract"] for r in records):
        info("  Filling remaining gaps from Semantic Scholar...")
        fill_from_semantic_scholar(http, records)
    return records


def main(argv=None) -> int:
    default_year = date.today().year - 1
    parser = argparse.ArgumentParser(
        description="Build a printable one-year abstract digest for a set of journals.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python3 fetch_abstracts.py --mailto you@wisc.edu\n"
            "  python3 fetch_abstracts.py --mailto you@wisc.edu --year 2025 --journals all \\\n"
            "      --keywords 'causal inference,bandit,insurance,high-dimensional'\n"
        ),
    )
    parser.add_argument("--year", type=int, default=default_year,
                        help=f"publication year to collect (default: {default_year}, i.e. last year)")
    parser.add_argument("--journals", default="default",
                        help="'default', 'all', or a comma-separated list of keys from journals.json")
    parser.add_argument("--mailto", default=os.environ.get("PAPER_DIGEST_EMAIL", ""),
                        help="your email; required by the Crossref/OpenAlex polite pools (much faster)")
    parser.add_argument("--keywords", default="",
                        help="comma-separated terms to highlight and flag in the output")
    parser.add_argument("--out-dir", default=str(HERE / "output"), help="where to write the digest")
    parser.add_argument("--cache-dir", default=str(HERE / ".cache"), help="raw API response cache")
    parser.add_argument("--config", default=str(HERE / "journals.json"), help="journal list")
    parser.add_argument("--proxy-prefix", default="https://ezproxy.library.wisc.edu/login?url=",
                        help="library proxy prefix for full-text links (default: UW-Madison EZproxy)")
    parser.add_argument("--proxy-label", default="UW full text", help="link text for the proxy link")
    parser.add_argument("--refresh", action="store_true", help="ignore the cache and re-download")
    parser.add_argument("--no-openalex", action="store_true", help="skip the OpenAlex abstract back-fill")
    parser.add_argument("--use-s2", action="store_true", help="also try Semantic Scholar for missing abstracts")
    parser.add_argument("--include-front-matter", action="store_true",
                        help="keep corrections, editorials, book reviews, etc.")
    parser.add_argument("--strict-year", action="store_true",
                        help="keep only articles whose issue year equals --year (drops online-first spillover)")
    parser.add_argument("--delay", type=float, default=0.25, help="seconds between API requests")
    args = parser.parse_args(argv)

    if not args.mailto:
        warn("Note: no --mailto given. Crossref/OpenAlex will throttle you harder.")
        warn("      Re-run with --mailto your.name@wisc.edu for the polite (fast) pool.\n")
        args.mailto = "anonymous@example.com"

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]

    info(f"paper-digest v{VERSION} - collecting {args.year}")
    records = collect(args)
    if not records:
        warn("\nNothing collected. Check the journal keys, the year, and your network connection.")
        return 1

    pattern = keyword_pattern(keywords)
    if pattern:
        for rec in records:
            rec["hit"] = bool(pattern.search(f"{rec['title']} {rec['abstract']}"))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{args.year}_abstracts"
    html_path = out_dir / f"{stem}.html"
    csv_path = out_dir / f"{stem}.csv"

    html_path.write_text(
        render_html(records, args.year, keywords, args.proxy_prefix, args.proxy_label),
        encoding="utf-8",
    )
    write_csv(records, csv_path)

    with_abs = sum(1 for r in records if r["abstract"])
    info("\n" + "=" * 58)
    info(f"  articles          : {len(records)}")
    info(f"  with abstract     : {with_abs} ({with_abs * 100 // max(len(records), 1)}%)")
    if pattern:
        info(f"  keyword matches   : {sum(1 for r in records if r.get('hit'))}")
    info(f"  HTML (print this) : {html_path}")
    info(f"  CSV  (sort/filter): {csv_path}")
    info("=" * 58)
    info("\nOpen the HTML in a browser, then Ctrl/Cmd+P -> Save as PDF.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
