#!/usr/bin/env python3
"""Add Chinese commentary to a year's worth of abstracts, one paper at a time.

Reads the JSON that fetch_abstracts.py writes, and for each paper asks Claude for:

  * 中文标题            - the title in Chinese
  * 摘要翻译            - a faithful, complete translation of the abstract
  * 一句话              - what the paper actually does, in one sentence
  * 精算关联 · 想法火花  - how it connects to the reader's own research, and
                          concrete ideas it could spark (or an honest "no real link")
  * 这篇好在哪          - the actual contribution, plus a caveat
  * relevance 1-5       - so the digest can be sorted with the useful papers first

Writes {doi: annotation} to a JSON file that fetch_abstracts.py merges back in
with --annotations. Resumable: papers already in the output file are skipped.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...           # or: ant auth login
    python3 annotate.py --input output/2025_abstracts.json
    python3 fetch_abstracts.py --mailto you@wisc.edu \\
        --annotations output/2025_annotations.json --sort relevance

Requires:  pip install anthropic
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent

# $ per million tokens (input, output) - Anthropic first-party API rates.
PRICING = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
DEFAULT_MODEL = "claude-opus-5"

DEFAULT_PROFILE = """\
读者是威斯康星大学麦迪逊分校精算科学（Actuarial Science / Risk and Insurance）方向的博士生。
关心的问题包括：非寿险定价与准备金、寿险与年金、长期护理、健康险与医疗费用、
死亡率与长寿风险、巨灾与气候风险、相依性建模与尾部风险、保险数据上的因果推断、
定价公平性与监管合规、以及能落到实际保单/理赔数据上的统计与机器学习方法。
读者的目的：从去年发表的顶刊论文里，找到能接到自己研究上的方法或问题。
"""

SYSTEM_PROMPT = """\
你在帮一位统计/精算方向的博士生做年度文献速览。对方会给你一篇已发表论文的题目、
期刊出处和英文摘要，你要产出一段中文导读，让读者在 30 秒内判断这篇值不值得细读。

严格遵守：

1. 摘要翻译要忠实完整，逐句翻译，不要压缩成概要，不要漏掉方法名、数据来源和结论。
   专业术语保留英文原词并加中文，例如「copula（联结函数）」「conformal prediction（保形预测）」。
   摘要里没有的内容，绝对不要加进翻译。

2. 精算关联必须诚实。如果这篇论文和读者的方向没有实质联系，就直接写「与精算方向无实质
   关联」并简短说明，不要为了凑关联而生造牵强的联系——生造的关联比承认没关联更浪费读者时间。
   有关联时要具体：说清楚是哪个方法、哪个假设、哪类数据结构可以迁移过来，
   而不是泛泛地说「可用于保险领域」。

3. 想法火花要具体到可以动手：指出一个能做的题目，说明用什么数据、替换掉原文的哪个部件。
   宁可少写一条，也不要写正确但空洞的话。没有值得写的就留空数组。

4. 「这篇好在哪」要说清楚真实贡献——它解决了此前什么解决不了的问题、
   为什么能上这个级别的期刊。同时在 caveats 里写一条真实的局限或适用边界。
   不要吹捧，不要复述摘要。

5. relevance 打分（1-5）针对的是「对这位读者的研究有多大用」，不是论文本身的质量：
   5 = 直接相关，建议精读；4 = 方法可迁移，值得读；3 = 有启发，扫一眼；
   2 = 只需知道存在；1 = 与读者方向无关。
   大部分论文应该落在 1-3。只有真正相关的才给 4-5，分数虚高会让整份汇编失去筛选作用。

全部字段用简体中文书写（method_tags 可用英文术语）。"""

USER_TEMPLATE = """\
读者背景：
{profile}

--- 论文 ---
题目：{title}
作者：{authors}
出处：{journal} {year}{issue_part}
DOI：{doi}

英文摘要：
{abstract}
--- 结束 ---

请按 schema 输出中文导读。"""


def build_schema_model():
    """The annotation schema, as a Pydantic model for structured outputs."""
    from typing import List

    from pydantic import BaseModel, Field

    class Annotation(BaseModel):
        title_zh: str = Field(description="论文标题的中文翻译")
        abstract_zh: str = Field(description="摘要的完整忠实中文翻译，不是概要")
        one_liner_zh: str = Field(description="一句话说清这篇论文做了什么")
        actuarial_link: str = Field(
            description="与读者精算研究方向的关联；没有实质关联就直说"
        )
        idea_sparks: List[str] = Field(
            description="具体可动手的研究点子，0-3 条；没有就留空"
        )
        why_good: str = Field(description="这篇论文真正的贡献，为什么能发在这个期刊")
        caveats: str = Field(description="一条真实的局限或适用边界")
        method_tags: List[str] = Field(description="2-5 个方法标签，可用英文")
        relevance: int = Field(description="对这位读者的相关性，1-5 的整数")

    return Annotation


def load_profile(path: str) -> str:
    if not path:
        return DEFAULT_PROFILE
    return Path(path).read_text(encoding="utf-8").strip()


def build_user_prompt(rec: dict, profile: str) -> str:
    issue = ""
    if rec.get("volume"):
        issue = f", {rec['volume']}"
        if rec.get("issue"):
            issue += f"({rec['issue']})"
        if rec.get("pages"):
            issue += f", {rec['pages']}"
    return USER_TEMPLATE.format(
        profile=profile,
        title=rec.get("title", ""),
        authors=rec.get("authors", "") or "(未列出)",
        journal=rec.get("journal", ""),
        year=rec.get("year", ""),
        issue_part=issue,
        doi=rec.get("doi", ""),
        abstract=rec.get("abstract", ""),
    )


def annotate_one(client, model: str, rec: dict, profile: str, schema) -> dict:
    system = [{
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }]
    # Thinking is on by default on Opus 5, so `thinking` is deliberately omitted.
    response = client.messages.parse(
        model=model,
        max_tokens=8000,
        system=system,
        messages=[{"role": "user", "content": build_user_prompt(rec, profile)}],
        output_format=schema,
    )
    ann = response.parsed_output.model_dump()
    ann["_model"] = model
    usage = response.usage
    return ann, (
        usage.input_tokens
        + (usage.cache_read_input_tokens or 0)
        + (usage.cache_creation_input_tokens or 0),
        usage.output_tokens,
    )


def estimate_cost(client, model: str, records: list, profile: str) -> tuple:
    """Measured input tokens on one sample, times the number of papers."""
    sample = build_user_prompt(records[0], profile)
    counted = client.messages.count_tokens(
        model=model,
        system=[{"type": "text", "text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": sample}],
    )
    per_in = counted.input_tokens
    per_out = 1300  # typical for this schema; thinking tokens push it higher
    in_rate, out_rate = PRICING.get(model, PRICING[DEFAULT_MODEL])
    n = len(records)
    low = (per_in * n / 1e6) * in_rate + (per_out * n / 1e6) * out_rate
    return per_in, per_out, low, low * 2.2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Add Chinese translation + actuarial commentary to a paper digest.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", required=True,
                        help="the *_abstracts.json written by fetch_abstracts.py")
    parser.add_argument("--output", default="",
                        help="where to write annotations (default: <input dir>/<year>_annotations.json)")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Claude model (default: {DEFAULT_MODEL})")
    parser.add_argument("--profile", default="",
                        help="text file describing your research direction "
                             "(default: a generic actuarial-science profile)")
    parser.add_argument("--limit", type=int, default=0,
                        help="annotate at most this many papers (0 = all)")
    parser.add_argument("--only-hits", action="store_true",
                        help="only papers flagged by --keywords in fetch_abstracts.py")
    parser.add_argument("--journals", default="",
                        help="comma-separated journal-name substrings to restrict to")
    parser.add_argument("--concurrency", type=int, default=4,
                        help="parallel requests (default 4)")
    parser.add_argument("--yes", action="store_true",
                        help="skip the cost confirmation prompt")
    parser.add_argument("--redo", action="store_true",
                        help="re-annotate papers already present in the output file")
    args = parser.parse_args(argv)

    try:
        import anthropic  # noqa: F401
    except ImportError:
        print("This step needs the Anthropic SDK:\n\n    pip install anthropic\n",
              file=sys.stderr)
        return 1
    import anthropic

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"Input not found: {in_path}", file=sys.stderr)
        return 1
    records = json.loads(in_path.read_text(encoding="utf-8"))
    out_path = Path(args.output) if args.output else in_path.with_name(
        in_path.stem.replace("_abstracts", "") + "_annotations.json")

    done = {}
    if out_path.exists() and not args.redo:
        done = json.loads(out_path.read_text(encoding="utf-8"))

    todo = []
    skipped_no_abstract = 0
    for rec in records:
        if not rec.get("abstract"):
            skipped_no_abstract += 1
            continue
        if not rec.get("doi") or (rec["doi"] in done and not args.redo):
            continue
        if args.only_hits and not rec.get("hit"):
            continue
        if args.journals:
            wanted = [j.strip().lower() for j in args.journals.split(",") if j.strip()]
            if not any(w in rec.get("journal", "").lower() for w in wanted):
                continue
        todo.append(rec)
    if args.limit:
        todo = todo[: args.limit]

    print(f"papers in digest   : {len(records)}")
    print(f"already annotated  : {len(done)}")
    print(f"no abstract (skip) : {skipped_no_abstract}")
    print(f"to annotate now    : {len(todo)}")
    if not todo:
        print("\nNothing to do.")
        return 0

    profile = load_profile(args.profile)
    client = anthropic.Anthropic()

    per_in, per_out, low, high = estimate_cost(client, args.model, todo, profile)
    print(f"\nmodel              : {args.model}")
    print(f"input tokens/paper : ~{per_in} (measured on the first paper)")
    print(f"estimated cost     : ${low:.2f} - ${high:.2f} for {len(todo)} papers")
    print("  (range covers thinking tokens; prompt caching cuts the input side further)")
    if not args.yes:
        reply = input("\nProceed? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Aborted - nothing spent.")
            return 0

    schema = build_schema_model()
    lock = threading.Lock()
    total_in = total_out = 0
    failures = []

    def work(rec):
        return rec, annotate_one(client, args.model, rec, profile, schema)

    print()
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = [pool.submit(work, rec) for rec in todo]
        for i, future in enumerate(as_completed(futures), 1):
            try:
                rec, (ann, (n_in, n_out)) = future.result()
            except Exception as exc:  # keep going; one bad paper must not kill the run
                failures.append(str(exc))
                print(f"  [{i}/{len(todo)}] FAILED: {exc}")
                continue
            with lock:
                done[rec["doi"]] = ann
                total_in += n_in
                total_out += n_out
                out_path.write_text(
                    json.dumps(done, ensure_ascii=False, indent=1), encoding="utf-8"
                )
            star = "*" * int(ann.get("relevance") or 0)
            print(f"  [{i}/{len(todo)}] {star:<5} {rec['title'][:64]}")

    in_rate, out_rate = PRICING.get(args.model, PRICING[DEFAULT_MODEL])
    spent = total_in / 1e6 * in_rate + total_out / 1e6 * out_rate
    print("\n" + "=" * 58)
    print(f"  annotated        : {len(done)} total in {out_path}")
    if failures:
        print(f"  failed           : {len(failures)} (re-run to retry them)")
    print(f"  tokens           : {total_in:,} in / {total_out:,} out")
    print(f"  actual cost      : ~${spent:.2f}")
    print("=" * 58)
    print("\nNow re-render the digest with the annotations merged in:")
    print(f"  python3 fetch_abstracts.py --mailto you@wisc.edu \\")
    print(f"      --annotations {out_path} --sort relevance")
    return 0


if __name__ == "__main__":
    sys.exit(main())
