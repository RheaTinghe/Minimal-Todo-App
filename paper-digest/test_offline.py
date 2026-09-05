#!/usr/bin/env python3
"""Offline end-to-end test: fake Crossref/OpenAlex responses through the real pipeline.

Run:  python3 test_offline.py
No network access required - every HTTP call is stubbed.
"""

import json
import shutil
import sys
import tempfile
import urllib.parse
from pathlib import Path

import fetch_abstracts as fa

HERE = Path(__file__).resolve().parent
FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        FAILURES.append(label)


# --------------------------------------------------------------------------
# Fixtures - shaped like real Crossref / OpenAlex payloads
# --------------------------------------------------------------------------

CROSSREF_ITEMS = {
    "0025-1909": [
        {
            "DOI": "10.1287/mnsc.2024.01234",
            "title": ["Dynamic Pricing under Demand Learning"],
            "author": [
                {"given": "Jane", "family": "Doe"},
                {"given": "Wei", "family": "Zhang"},
            ],
            "abstract": "<jats:p>We study a firm that sets prices while learning demand. "
                        "Our policy attains <jats:italic>O</jats:italic>(&#x221A;T) regret.</jats:p>",
            "published-print": {"date-parts": [[2025, 3, 1]]},
            "issued": {"date-parts": [[2025, 1, 14]]},
            "volume": "71", "issue": "3", "page": "1500-1521",
            "type": "journal-article",
            "URL": "https://doi.org/10.1287/mnsc.2024.01234",
            "is-referenced-by-count": 7,
        },
        {   # abstract missing from Crossref -> should be filled by OpenAlex
            "DOI": "10.1287/mnsc.2024.05678",
            "title": ["Causal Inference for Platform Experiments"],
            "author": [{"given": "Ana", "family": "Ruiz"}],
            "published-print": {"date-parts": [[2025, 7, 1]]},
            "volume": "71", "issue": "7", "page": "3001-3020",
            "type": "journal-article",
        },
        {   # front matter -> should be dropped
            "DOI": "10.1287/mnsc.2025.corr01",
            "title": ["Correction to: Dynamic Pricing under Demand Learning"],
            "author": [{"given": "Jane", "family": "Doe"}],
            "published-print": {"date-parts": [[2025, 9, 1]]},
            "volume": "71", "page": "4000", "type": "journal-article",
        },
    ],
    "1526-5501": [
        {   # same DOI as above under the electronic ISSN -> must de-duplicate
            "DOI": "10.1287/mnsc.2024.01234",
            "title": ["Dynamic Pricing under Demand Learning"],
            "author": [{"given": "Jane", "family": "Doe"}],
            "published-print": {"date-parts": [[2025, 3, 1]]},
            "volume": "71", "issue": "3", "page": "1500-1521", "type": "journal-article",
        }
    ],
    "0162-1459": [
        {
            "DOI": "10.1080/01621459.2025.9999",
            "title": ["High-Dimensional Inference with <i>Nuisance</i> Parameters"],
            "author": [{"given": "Li", "family": "Chen"}, {"name": "The R Core Team"}],
            "abstract": "Abstract: We propose a debiased estimator for high-dimensional models "
                        "and establish its asymptotic normality.",
            "published-print": {"date-parts": [[2025, 12]]},
            "volume": "120", "issue": "552", "page": "2100-2115",
            "type": "journal-article",
        },
        {   # online-first spillover: issue year 2026 -> dropped only under --strict-year
            "DOI": "10.1080/01621459.2025.8888",
            "title": ["Nonparametric Bandit Policies for Sequential Trials"],
            "author": [{"given": "Sam", "family": "Patel"}],
            "abstract": "<jats:p>A nonparametric bandit policy is analyzed.</jats:p>",
            "published-print": {"date-parts": [[2026, 2]]},
            "published-online": {"date-parts": [[2025, 11, 3]]},
            "volume": "121", "page": "10-25", "type": "journal-article",
        },
    ],
    "1537-274X": [],
    "1932-6157": [
        {
            "DOI": "10.1214/25-aoas1900",
            "title": ["A Spatial Model for Wildfire Risk"],
            "author": [{"given": "Marta", "family": "Silva"}],
            "published-print": {"date-parts": [[2025, 6]]},
            "volume": "19", "issue": "2", "page": "800-830", "type": "journal-article",
        }
    ],
}

OPENALEX_ABSTRACTS = {
    "10.1287/mnsc.2024.05678": {
        "Randomized": [0], "experiments": [1], "on": [2], "platforms": [3],
        "suffer": [4], "from": [5], "interference;": [6], "we": [7],
        "develop": [8], "a": [9], "causal": [10], "estimator": [11],
        "that": [12], "remains": [13], "consistent.": [14],
    },
    # 10.1214/25-aoas1900 is deliberately absent from every source: it exercises
    # the "no abstract in the open metadata" fallback in the rendered page.
}


class FakeHttp(fa.Http):
    """Serves the fixtures above instead of touching the network."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls = []

    def get_json(self, url, quiet_404=False):
        self.calls.append(url)
        url = urllib.parse.unquote(url)
        if "api.crossref.org" in url:
            issn = url.split("/journals/")[1].split("/")[0]
            if "cursor=%2A" in url or "cursor=*" in url:
                return {"message": {"items": CROSSREF_ITEMS.get(issn, []), "next-cursor": "done"}}
            return {"message": {"items": [], "next-cursor": None}}
        if "api.openalex.org" in url:
            results = []
            for doi, inverted in OPENALEX_ABSTRACTS.items():
                if doi in url.lower():
                    results.append({
                        "doi": f"https://doi.org/{doi}",
                        "abstract_inverted_index": inverted,
                    })
            return {"results": results}
        raise AssertionError(f"unexpected URL: {url}")

    def post_json(self, url, payload):
        raise AssertionError("Semantic Scholar should not be called in this test")


def run_pipeline(extra_args, tmp):
    fa.Http = FakeHttp  # patched for collect()
    argv = [
        "--year", "2025",
        "--journals", "mgmtsci,jasa,aoas",
        "--mailto", "test@example.com",
        "--out-dir", str(tmp / "output"),
        "--cache-dir", str(tmp / "cache"),
        "--config", str(HERE / "journals.json"),
        "--delay", "0",
    ] + extra_args
    code = fa.main(argv)
    return code, tmp / "output" / "2025_abstracts.html", tmp / "output" / "2025_abstracts.csv"


def main():
    print("\n--- unit checks ---")
    check("JATS markup stripped",
          fa.clean_text("<jats:p>Hello <jats:italic>world</jats:italic>&#x221A;</jats:p>")
          == "Hello world √")
    check("leading 'Abstract:' label removed",
          fa.clean_text("Abstract: We propose") == "We propose")
    check("inverted index rebuilt",
          fa.abstract_from_inverted_index({"b": [1], "a": [0], "c": [2]}) == "a b c")
    check("author name from 'name' field",
          fa.format_authors([{"name": "The R Core Team"}]) == "The R Core Team")
    check("EZproxy link built",
          fa.proxy_link("10.1234/x", "https://ezproxy.library.wisc.edu/login?url=")
          == "https://ezproxy.library.wisc.edu/login?url=https://doi.org/10.1234/x")
    check("keyword highlighting is case-insensitive",
          "<mark>Causal</mark>" in fa.highlight("Causal inference", fa.keyword_pattern(["causal"])))
    check("front-matter regex catches corrections",
          bool(fa.NON_ARTICLE_TITLE.match("Correction to: Dynamic Pricing")))

    real_http = fa.Http
    tmp = Path(tempfile.mkdtemp(prefix="paper-digest-test-"))
    try:
        print("\n--- pipeline (default) ---")
        code, html_path, csv_path = run_pipeline(
            ["--keywords", "causal inference,bandit"], tmp)
        check("exit code 0", code == 0, f"(got {code})")
        check("HTML written", html_path.exists())
        check("CSV written", csv_path.exists())

        page = html_path.read_text(encoding="utf-8")
        rows = list(csv_path.read_text(encoding="utf-8-sig").splitlines())
        n_entries = page.count('<article class="entry')

        check("5 articles kept (dupe + correction dropped)", n_entries == 5, f"(got {n_entries})")
        check("CSV has one row per article", len(rows) == 6, f"(got {len(rows)} lines)")
        check("de-duplicated across ISSNs", page.count("Dynamic Pricing under Demand Learning") == 1)
        check("correction dropped", "Correction to" not in page)
        check("Crossref abstract present", "attains O (√T) regret" in page or "regret" in page)
        check("OpenAlex back-fill present", "interference; we develop a causal estimator" in page)
        check("italics in title stripped", "Nuisance</i>" not in page and "Nuisance" in page)
        check("multi-word keyword highlighted", "<mark>Causal Inference</mark>" in page)
        check("keyword hit flagged", 'class="entry hit"' in page)
        check("all three journals present",
              all(j in page for j in ["Management Science",
                                      "Journal of the American Statistical Association",
                                      "The Annals of Applied Statistics"]))
        check("EZproxy links in output", "ezproxy.library.wisc.edu" in page)
        check("print stylesheet present", "@media print" in page and "window.print()" in page)
        check("no unescaped fixture HTML leaked", "<i>Nuisance</i>" not in page)
        n_missing = page.count('class="abstract none"')
        check("no-abstract fallback rendered once", n_missing == 1,
              f"(got {n_missing} without abstract)")

        print("\n--- pipeline (--strict-year) ---")
        fa.Http = FakeHttp
        tmp2 = Path(tempfile.mkdtemp(prefix="paper-digest-test2-"))
        try:
            code2, html2, _ = run_pipeline(["--strict-year"], tmp2)
            page2 = html2.read_text(encoding="utf-8")
            check("exit code 0", code2 == 0)
            check("2026-issue article dropped under --strict-year",
                  "Nonparametric Bandit Policies" not in page2)
            n2 = page2.count('<article class="entry')
            check("4 articles remain", n2 == 4, f"(got {n2})")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)

        print("\n--- cache reuse ---")
        cached = sorted(p.name for p in (tmp / "cache").glob("*.json"))
        check("raw responses cached", len(cached) == 5, f"(got {cached})")
        check("cache is valid JSON",
              isinstance(json.loads((tmp / "cache" / cached[0]).read_text()), list))
    finally:
        fa.Http = real_http
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
