# paper-digest — 年度期刊摘要汇编

把某一年（默认是去年）某几本期刊发表的全部论文抓成**一个可打印的 HTML 页面**：标题、作者、卷期页、摘要、DOI 链接、以及一条走威斯康星图书馆代理的全文链接。浏览器里 `Ctrl/Cmd + P` 就能存成 PDF，一次把一整年扫完。

默认三本：**Management Science**、**JASA**、**The Annals of Applied Statistics**。另外 9 本（AoS、JRSS-B、Biometrika、Biometrics、JBES、Operations Research、Marketing Science、JCGS、Statistical Science）已经配好，加个参数就能开。

---

## 关于图书馆权限的一件重要的事

**抓摘要不需要、也不应该用你的 UW 登录凭证。**

- 摘要属于**公开的元数据**，Crossref / OpenAlex 这些学术基础设施免费开放，任何人都能取。这个脚本走的就是这条路。
- 拿校园账号去批量抓 Taylor & Francis（JASA）、INFORMS（Management Science）的网页，违反它们的使用条款，而且很容易触发反爬、导致**整个学校 IP 段被封**——这属于会被图书馆找上门的那类事故。

所以脚本的做法是：摘要走开放 API，同时给每篇文章生成一条 EZproxy 链接（`UW full text`）。你略读的时候看到值得细看的，点那条链接，用你自己的权限正常进全文——这是图书馆本来就希望你用的方式。

---

## 快速开始

只要有 Python 3.8+，**不需要 pip install 任何东西**（纯标准库）。

```bash
cd paper-digest
python3 fetch_abstracts.py --mailto 你的邮箱@wisc.edu
```

跑完会生成：

```
output/2025_abstracts.html   ← 打开它，Ctrl/Cmd+P 存成 PDF
output/2025_abstracts.csv    ← 想用 Excel 排序筛选的话
```

`--mailto` 不是必填，但强烈建议填：Crossref 和 OpenAlex 会把带邮箱的请求放进「polite pool」，速度快很多也不容易被限流。你的邮箱只发给这两个学术 API，不会给别人。

Windows 上把 `python3` 换成 `python` 即可。

### 加上你的研究关键词（推荐）

```bash
python3 fetch_abstracts.py --mailto 你的邮箱@wisc.edu \
    --keywords "causal inference,bandit,insurance,high-dimensional,reinforcement learning"
```

命中的论文会在标题和摘要里**高亮关键词**，左边加一条红边，目录里标出每本刊命中几篇。页面顶部还有个「only keyword matches」勾选框——先只看命中的，再翻剩下的，比从头硬啃省一半时间。

### 全部十二本刊

```bash
python3 fetch_abstracts.py --mailto 你的邮箱@wisc.edu --journals all
```

---

## 常用参数

| 参数 | 说明 |
| --- | --- |
| `--year 2025` | 抓哪一年，默认是**去年**（今年是 2026，默认就抓 2025） |
| `--journals` | `default`（三本）/ `all`（十二本）/ `mgmtsci,jasa,aoas,jrssb` 这样点名 |
| `--keywords "a,b,c"` | 高亮并标记的关键词 |
| `--mailto` | 你的邮箱，进 API 的快速通道 |
| `--strict-year` | 只保留**期号年份**等于该年的文章，剔掉 online-first 溢出到次年正刊的那些 |
| `--include-front-matter` | 保留 correction / editorial / 书评等（默认剔掉） |
| `--refresh` | 忽略缓存重新下载 |
| `--use-s2` | 摘要还缺的话，再问一次 Semantic Scholar |
| `--proxy-prefix` | 换图书馆代理前缀，默认 `https://ezproxy.library.wisc.edu/login?url=` |
| `--out-dir` / `--cache-dir` | 输出和缓存目录 |

`python3 fetch_abstracts.py --help` 看完整列表。

原始 API 返回缓存在 `.cache/`，所以第二次跑（比如换个关键词重新排版）是秒出的，不会再打扰 API。

---

## 它是怎么工作的

1. **Crossref** 按 ISSN 拉出该刊该年的全部 journal-article，游标翻页，一本刊一般几百条。同时带回 Crossref 里存了的摘要。
2. **OpenAlex** 补摘要：Crossref 里缺摘要的（IMS 系的 AOAS、AoS 尤其常见），按 DOI 批量去 OpenAlex 查，把它的 `abstract_inverted_index` 还原成正文。
3. **Semantic Scholar**（可选，`--use-s2`）兜最后一层。
4. 清洗：剥掉 JATS/HTML 标签、还原 HTML 实体、去掉开头多余的 "Abstract:"、跨 ISSN 按 DOI 去重、剔掉 correction / erratum / editorial / 书评这类非研究性条目。
5. 渲染成一个自包含的 HTML（无外部依赖，断网也能开），带打印样式。

**摘要覆盖率**：Management Science 和 JASA 通常能到九成以上；AOAS 因为 IMS 往 Crossref 存摘要不全，主要靠 OpenAlex 补，个别文章可能仍然没有——这种会显示一行提示，点标题去出版商页面看。跑完终端会报告实际覆盖率。

---

## 打印成 PDF

浏览器打开 `output/2025_abstracts.html`，点右上角 **Print / Save as PDF**（或 `Ctrl/Cmd+P`），目标选「另存为 PDF」。打印样式已经调好了：

- 每本期刊单独起一页
- 顶部的搜索框、按钮不会印出来
- 每篇论文不会被跨页切断
- 每条前面有个小方框 ☐，纸上读的时候可以直接打勾

---

## 导师说的那个年终习惯

思路是每年年底把去年的好刊过一遍，找出一两篇对自己有用的。建议的用法：

1. 12 月底跑一次，`--year` 用刚过去的这年。
2. `--keywords` 填上你现在的研究方向，**先只看命中的那些**。
3. 再从头翻一遍全部标题，只在标题勾起兴趣时才读摘要——纸上打勾。
4. 勾出来的用 `UW full text` 链接进全文。
5. CSV 留档，明年可以对比看某个方向是在升温还是降温。

明年要改期刊列表就编辑 `journals.json`——加一本刊只要填 `key`、`name`、`issn`（把印刷版和电子版 ISSN 都写上，脚本会按 DOI 去重）。

---

## 测试

```bash
python3 test_offline.py
```

用假的 API 响应把整条流水线跑一遍，不联网。检查去重、front-matter 过滤、OpenAlex 回填、关键词高亮、`--strict-year`、HTML 转义、缓存等 29 项。
