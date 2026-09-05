#!/usr/bin/env python3
"""Offline test for annotate.py + annotated rendering. No network, no API spend.

The Anthropic client is stubbed; everything else (schema model, prompt building,
resume logic, JSON round-trip, HTML rendering) is the real code path.

Run:  python3 test_annotate.py     (needs: pip install anthropic)
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

import annotate
import fetch_abstracts as fa

HERE = Path(__file__).resolve().parent
FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        FAILURES.append(label)


RECORDS = [
    {
        "journal": "The Annals of Applied Statistics", "title": "Trade credit insurance networks",
        "authors": "W. Yoo, S. Tseung", "doi": "10.1214/25-aoas2106",
        "url": "https://doi.org/10.1214/25-aoas2106", "volume": "19", "issue": "4",
        "pages": "2852-2877", "date": "2025-12-01", "year": 2025,
        "abstract": "We propose a network-augmented generalized linear mixed model for trade credit insurance.",
        "abstract_source": "crossref", "cited_by": 3, "subjects": "", "hit": True,
    },
    {
        "journal": "Journal of the American Statistical Association", "title": "A method with no abstract",
        "authors": "A. Author", "doi": "10.1080/01621459.2025.0001", "url": "", "volume": "120",
        "issue": "552", "pages": "1-10", "date": "2025-12-01", "year": 2025,
        "abstract": "", "abstract_source": "", "cited_by": 0, "subjects": "", "hit": False,
    },
    {
        "journal": "Journal of the American Statistical Association", "title": "Fairness in machine learning",
        "authors": "B. Author", "doi": "10.1080/01621459.2025.2579579", "url": "", "volume": "120",
        "issue": "552", "pages": "20-40", "date": "2025-12-01", "year": 2025,
        "abstract": "We review fairness-enhancing mechanisms and organize them into three categories.",
        "abstract_source": "crossref", "cited_by": 11, "subjects": "", "hit": False,
    },
]


class FakeUsage:
    input_tokens = 900
    output_tokens = 1200
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class FakeMessages:
    def __init__(self):
        self.prompts = []
        self.systems = []

    def count_tokens(self, model, system, messages):
        class R:
            input_tokens = 950
        return R()

    def parse(self, model, max_tokens, system, messages, output_format):
        self.prompts.append(messages[0]["content"])
        self.systems.append(system)
        parsed = output_format(
            title_zh="贸易信用保险网络数据的统计学习",
            abstract_zh="我们提出了一个网络增强的广义线性混合模型（GLMM）用于贸易信用保险。",
            one_liner_zh="把买方之间的网络结构塞进 GLMM，用来同时建模索赔概率和报案时滞。",
            actuarial_link="直接就是非寿险定价与准备金问题，网络结构对应再保险分入分出关系。",
            idea_sparks=["把网络项换成车险的家庭保单结构", "用同样的 SEM 算法做 IBNR"],
            why_good="第一次把网络效应正式引入信用险的定价—准备金联合模型。",
            caveats="数据来自单一亚洲保险公司，外推到其他市场需谨慎。",
            method_tags=["GLMM", "network data", "stochastic EM"],
            relevance=5,
        )

        class R:
            parsed_output = parsed
            usage = FakeUsage()
        return R()


class FakeClient:
    def __init__(self, *a, **kw):
        self.messages = FakeMessages()


LAST_CLIENT = {}


def fake_anthropic_factory(*a, **kw):
    client = FakeClient()
    LAST_CLIENT["c"] = client
    return client


def main():
    import anthropic

    print("\n--- schema + prompt ---")
    schema = annotate.build_schema_model()
    inst = schema(title_zh="t", abstract_zh="a", one_liner_zh="o", actuarial_link="l",
                  idea_sparks=["x"], why_good="w", caveats="c", method_tags=["m"], relevance=4)
    dumped = inst.model_dump()
    check("schema model builds and dumps",
          dumped["relevance"] == 4 and dumped["idea_sparks"] == ["x"])
    check("schema has every field the renderer reads",
          set(dumped) >= {"title_zh", "abstract_zh", "one_liner_zh", "actuarial_link",
                          "idea_sparks", "why_good", "caveats", "method_tags", "relevance"})

    prompt = annotate.build_user_prompt(RECORDS[0], annotate.DEFAULT_PROFILE)
    check("prompt carries title, abstract and citation",
          "Trade credit insurance networks" in prompt
          and "network-augmented generalized linear mixed model" in prompt
          and "19(4), 2852-2877" in prompt)
    check("prompt carries the reader profile", "精算" in prompt)
    check("system prompt forbids inventing a connection",
          "不要为了凑关联而生造" in annotate.SYSTEM_PROMPT)

    real = anthropic.Anthropic
    anthropic.Anthropic = fake_anthropic_factory
    tmp = Path(tempfile.mkdtemp(prefix="annot-test-"))
    try:
        print("\n--- annotate run ---")
        in_path = tmp / "2025_abstracts.json"
        in_path.write_text(json.dumps(RECORDS, ensure_ascii=False), encoding="utf-8")
        code = annotate.main(["--input", str(in_path), "--yes", "--concurrency", "1"])
        out_path = tmp / "2025_annotations.json"
        check("exit code 0", code == 0, f"(got {code})")
        check("annotations written", out_path.exists())

        anns = json.loads(out_path.read_text(encoding="utf-8"))
        check("paper without an abstract is skipped", len(anns) == 2, f"(got {len(anns)})")
        check("keyed by DOI", "10.1214/25-aoas2106" in anns)
        check("model recorded on each annotation",
              all(a.get("_model") == annotate.DEFAULT_MODEL for a in anns.values()))

        client = LAST_CLIENT["c"]
        check("system prompt marked for prompt caching",
              client.messages.systems[0][0]["cache_control"] == {"type": "ephemeral"})

        print("\n--- resume ---")
        code2 = annotate.main(["--input", str(in_path), "--yes"])
        check("second run is a no-op", code2 == 0 and len(client.messages.prompts) == 2,
              f"(calls={len(client.messages.prompts)})")

        print("\n--- filters ---")
        (tmp / "hits.json").write_text(json.dumps(RECORDS, ensure_ascii=False), encoding="utf-8")
        annotate.main(["--input", str(tmp / "hits.json"), "--yes", "--only-hits",
                       "--output", str(tmp / "hits_ann.json")])
        hits = json.loads((tmp / "hits_ann.json").read_text(encoding="utf-8"))
        check("--only-hits annotates just the flagged paper", list(hits) == ["10.1214/25-aoas2106"])

        print("\n--- annotated rendering ---")
        records = json.loads(in_path.read_text(encoding="utf-8"))
        for rec in records:
            if rec["doi"] in anns:
                rec["annotation"] = anns[rec["doi"]]
        page = fa.render_html(records, 2025, [], "https://ezproxy.library.wisc.edu/login?url=",
                              "UW full text", sort_by="relevance")
        check("Chinese title rendered", "贸易信用保险网络数据的统计学习" in page)
        check("translation section rendered", "摘要翻译" in page and "广义线性混合模型" in page)
        check("actuarial link section rendered", "精算关联" in page and "再保险分入分出" in page)
        check("idea sparks rendered as a list", "<li>用同样的 SEM 算法做 IBNR</li>" in page)
        check("why-good and caveats rendered",
              "好在哪" in page and "保留" in page and "单一亚洲保险公司" in page)
        check("verdict badge rendered", 'class="verdict v5">必读<' in page)
        check("method tags rendered", 'class="tag">GLMM<' in page)
        check("annotation summary in masthead", "篇带中文标注" in page)
        check("un-annotated paper still renders",
              "A method with no abstract" in page)
        # A model-written field containing markup must be escaped, not injected.
        evil = dict(anns["10.1214/25-aoas2106"])
        evil["actuarial_link"] = '<img src=x onerror=alert(1)> 与 <b>定价</b> 相关'
        poisoned = [dict(records[0], annotation=evil)]
        page_evil = fa.render_html(poisoned, 2025, [], "", "UW full text")
        check("model-written markup is escaped, not injected",
              "&lt;img src=x onerror=alert(1)&gt;" in page_evil
              and "<img src=x" not in page_evil)

        print("\n--- merge through the CLI ---")
        merged = fa.render_html(records, 2025, ["fairness"],
                                "https://ezproxy.library.wisc.edu/login?url=", "UW full text")
        check("keyword highlighting still works alongside annotations",
              "<mark>fairness</mark>" in merged.lower() or "<mark>Fairness</mark>" in merged)
    finally:
        anthropic.Anthropic = real
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED: {', '.join(FAILURES)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
