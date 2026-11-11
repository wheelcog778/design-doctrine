#!/usr/bin/env python3
"""doctrine_check —— 交付前自检（启发式，不是判定）。

两个模式：
  --doc    结论文档七件套齐不齐、有没有禁用语（防「忘了写」）
  --gates  交付状态卡的机器可读行，M2–M5 是否全 0、M1 是否达线（防「静默交付」）

判定口径（v2.10 起，按 references/self-check-tools.md §1/§2 执行）：
  1. 先剥 fenced 代码块与行内代码，再做任何匹配 —— 讲格式的文档不能被自己的例子命中
  2. 七件套按**标题**定位：必须有一个标题命中该项关键词，且该节正文非空
  3. 证据型两项（数字表 / 重算命令）在该节正文里找具体形态；空围栏不算命令、表头不算数字表
  4. gates 行必须完整落在 `<!-- ... -->` 内且顶格（缩进 ≥4 视为代码示例）；缺键 / 多键 / 重复键 / 非数字值 / 缺 M1_src 一律「无法判定」
  5. 所有分支返回同一套键：mode / verdict / problems / notes / gates

用法:
    python doctrine_check.py VERDICT.md                  # 默认 = --doc
    python doctrine_check.py VERDICT.md --explain
    python doctrine_check.py 交付状态.md --gates
    python doctrine_check.py --selftest

退出码: 0 = 可引用 / 验收通过；1 = 不可引用 / 验收不通过；2 = 无法判定或用法/文件错误；
        3 = 带风险交付（可交，但每项残留须进状态卡并由人签字）——不通过与带风险交付不共用 1。
"""
import argparse
import json
import os
import re
import sys

FORBIDDEN = ["基本有效", "大概率可用", "应该可以", "大概能用", "差不多能用", "看起来不错"]

# (序号名, 标题关键词, 正文形态, 是否必需)
CHECKS = [
    ("1 数据口径", ("口径", "数据", "样本", "观测"), "text", True),
    ("2 数字表", ("数字", "结果表", "数据表", "证据"), "table", True),
    ("3 判级理由", ("判级", "分级", "等级", "判据"), "text", True),
    ("4 可信度速查", ("可信度", "置信", "打折"), "text", True),
    ("5 未覆盖边界", ("边界", "未覆盖", "不覆盖", "没覆盖", "局限"), "text", True),
    ("6 重算命令", ("重算", "复现", "命令", "运行"), "cmd", True),
    ("7 可引用三条", ("可引用", "三条", "三句", "记住"), "text", False),
]

CMD_RE = re.compile(
    r"(?:^|[&;|]\s*)"
    r"(?:[A-Za-z]:[\/](?:[^\s&|;]*[\/])?|\.{0,2}[\/])?"
    r"(python3?|bash|sh|pwsh|powershell|cmd|node|Rscript|sqlite3|curl|uv)\b")
FENCE = re.compile(
    r"(?ms)^[ \t]*(?:(?P<bt>`{3,})[^\n]*\n.*?^[ \t]*(?P=bt)`*[ \t\r]*$"
    r"|(?P<td>~{3,})[^\n]*\n.*?^[ \t]*(?P=td)~*[ \t\r]*$)"
)
# 完整围栏剥离后仍存在的围栏标记按未闭合处理：到文件末尾都只能当代码。
UNFINISHED_FENCE = re.compile(r"(?ms)^[ \t]*(?:`{3,}|~{3,})[^\n]*(?:\n|$).*$")
FENCE_LINE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
INLINE = re.compile(r"`[^`\n]*`")
QUOTED = re.compile(r"「[^」\n]*」|“[^”\n]*”|\"[^\"\n]{0,30}\"")
BANNED_DISCUSSION = re.compile(
    r"(?:禁止|禁用|不得|不许|不要|避免)\s*"
    r"(?:出现|使用|用|写(?:成|下|作)?|采用|引用|说|表述)"
    r"|(?:禁用语|禁用词|禁止用语|禁止表述)"
)
# 豁免只覆盖「讨论句 + 紧跟其后的那一段引号」，不是整行。
# 否则一行里只要出现「不要用」，同一行末尾的禁用结论（"基本有效"）就能白嫖豁免。
BANNED_QUOTED = re.compile(
    r"(?:" + BANNED_DISCUSSION.pattern + r")"
    r"\s*[:：]?\s*(?P<q>「[^」\n]*」|“[^”\n]*”|\"[^\"\n]{0,30}\")"
)


def strip_code(text):
    """去掉完整或未闭合的围栏代码块与行内代码（保留行结构），再做匹配"""
    def blank(m):
        return "\n" * m.group(0).count("\n")
    stripped = FENCE.sub(blank, text)
    stripped = UNFINISHED_FENCE.sub(blank, stripped)
    return INLINE.sub(lambda m: " " * (m.end() - m.start()), stripped)


def read_text_strict(path):
    """严读：utf-8-sig → gb18030 → 报错。绝不静默替换字符。"""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法解码（既不是 UTF-8 也不是 GB18030）")


def sections(text):
    """按 markdown 标题切节，返回 [(heading, body)]；首段 heading 为空串。
    代码围栏里的 `# 注释` 不是标题，必须跳过。"""
    out, head, buf = [], "", []
    fence = None
    for line in text.splitlines():
        marker = FENCE_LINE.match(line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
                buf.append(line)
                continue
            if token[0] == fence[0] and len(token) >= len(fence):
                fence = None
                buf.append(line)
                continue
            # 围栏里的另一种标记只是正文，不得改变解析状态。
        if fence is None:
            m = re.match(r"^(#{1,6})\s*(.+)$", line)
            if m:
                out.append((head, "\n".join(buf)))
                head, buf = m.group(2).strip(), []
                continue
        buf.append(line)
    out.append((head, "\n".join(buf)))
    return out


def matching_sections(secs, words):
    """返回全部标题候选；正文是否有效由证据类型分别判定。"""
    return [(head, body) for head, body in secs if any(w in head for w in words)]


def has_data_table(body):
    """至少 1 行数据行（表头与分隔线都不算）"""
    lines = strip_code(body).splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if not (s.startswith("|") and s.count("|") >= 3) or re.fullmatch(r"[|:\-\s]+", s):
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if nxt.startswith("|") and re.fullmatch(r"[|:\-\s]+", nxt):
            continue                        # 这是表头
        if re.search(r"\d", s):
            return s[:70]
    return None


def has_command(body):
    """围栏内要有真正可执行的命令行：跳过注释行，允许 `cd X && E:/python.exe -m ...` 这种写法"""
    for match in FENCE.finditer(body):
        block = match.group(0)
        for line in block.splitlines()[1:]:          # 首行是围栏与语言标记
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if CMD_RE.search(s):
                return s[:70]
    for line in strip_code(body).splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        if CMD_RE.search(s):
            return s[:70]
    return None


NUM_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_.,])([+\-−]\d+\.\d+(?:%|pp)?|\d+\.\d+%)(?![A-Za-z0-9_])")
COMMON_THRESHOLDS = {"1.96", "2.50", "2.5", "2.60", "2.6", "2.80", "2.8", "2.87", "3.02",
                     "0.05", "0.01", "0.10", "0.15", "0.20", "0.25", "0.30", "5.0%", "1.0%", "10.0%"}
SCRIPT_ARG_RE = re.compile(r"\b(?:python3?|bash|sh)\s+([^\s&|;#]+\.(?:py|sh))\b")


def _norm_num(tok: str) -> str:
    """归一到「数值 + 单位」。

    单位（pp / %）要保留：量纲不同不算同一个数（t=1.23 与 IC 1.23% 是两回事）。
    符号**不**参与比对：正文「每笔少赚 0.532%」对表格「−0.532%/笔」是同一件事，
    硬按符号判会把包内合规文档自己判死。符号翻转属「写错了」，不在本检查射程内。
    无符号按正数处理（0.21 与 +0.21 同值）。
    """
    s = tok.replace("−", "-").strip()
    unit = ""
    for suf in ("pp", "%"):
        if s.endswith(suf):
            unit, s = suf, s[: -len(suf)]
            break
    try:
        return f"{abs(float(s)):.6g}|{unit}"
    except ValueError:
        return tok


def check_number_consistency(secs) -> list[str]:
    """检查『3 判级理由』中引用的带符号小数/百分比指标是否在其余数据章节或表格中出现过。"""
    reason_words = CHECKS[2][1]
    ref_text = "\n".join(strip_code(b) for h, b in secs if not any(w in h for w in reason_words))
    reason_text = "\n".join(strip_code(b) for h, b in secs if any(w in h for w in reason_words))
    if not ref_text or not reason_text:
        return []
    ref_raw = set(NUM_TOKEN_RE.findall(ref_text))
    ref_norm = {_norm_num(x) for x in ref_raw}
    ungrounded = []
    for tok in NUM_TOKEN_RE.findall(reason_text):
        clean = tok.replace("−", "-").lstrip("+")
        bare = clean
        for suf in ("pp", "%"):
            if bare.endswith(suf):
                bare = bare[: -len(suf)]
                break
        if bare in COMMON_THRESHOLDS or clean in COMMON_THRESHOLDS or tok in COMMON_THRESHOLDS:
            continue
        if tok not in ref_raw and _norm_num(tok) not in ref_norm:
            if tok not in ungrounded:
                ungrounded.append(tok)
    return ungrounded


def check_cmd_scripts(secs, root: str | None) -> list[str]:
    """当提供 --root 时，校验『6 重算命令』里引用的相对路径脚本在项目目录下真实存在。"""
    if not root:
        return []
    cmd_words = CHECKS[5][1]
    cmd_text = "\n".join(b for h, b in secs if any(w in h for w in cmd_words))
    missing = []
    for m in SCRIPT_ARG_RE.finditer(cmd_text):
        rel = m.group(1)
        if os.path.isabs(rel):
            continue
        cand = os.path.join(root, rel)
        if not os.path.isfile(cand) and rel not in missing:
            missing.append(rel)
    return missing


def check_doc(text, root=None):
    secs = sections(text)
    items = []
    for name, words, kind, req in CHECKS:
        ev = None
        # 撞词或同名标题可能有多个；当前候选缺有效证据时继续找后续候选/子节。
        for head, body in matching_sections(secs, words):
            if kind == "table":
                candidate = has_data_table(body)
            elif kind == "cmd":
                candidate = has_command(body)
            else:
                candidate = head if strip_code(body).strip() else None
            if candidate is not None:
                ev = candidate
                break
        items.append({"item": name, "pass": ev is not None, "required": req, "evidence": ev})

    last_head = next((head for head, _ in reversed(secs) if head), "")
    if items[4]["pass"] and not any(w in last_head for w in CHECKS[4][1]):
        items[4]["pass"] = False
        items[4]["evidence"] = None

    # 只有明确的禁用语讨论句才剔除引号；结论里的引号不提供豁免。
    prose_lines = []
    for line in strip_code(text).splitlines():
        if BANNED_DISCUSSION.search(line):
            # 只剥紧跟讨论句的那段引号；同一行其它引号仍然算结论
            line = BANNED_QUOTED.sub(lambda m: " " * (m.end() - m.start()), line)
        prose_lines.append(line)
    prose = "\n".join(prose_lines)
    hits = [w for w in FORBIDDEN if w in prose]
    problems = []
    ungrounded = check_number_consistency(secs)
    if ungrounded:
        problems.append("判级理由出现未在口径/数字表登记的数字: " + "、".join(ungrounded))
    missing_scripts = check_cmd_scripts(secs, root)
    if missing_scripts:
        problems.append("重算命令引用的脚本不存在: " + "、".join(missing_scripts))
    miss_req = [i["item"] for i in items if i["required"] and not i["pass"]]
    miss_opt = [i["item"] for i in items if not i["required"] and not i["pass"]]
    synthetic = re.search(r"synthetic\s*[:：]\s*true|合成示例|合成值", text) is not None
    if not miss_req and not hits and not problems:
        verdict = "仅结构完整（合成示例，不可引用）" if synthetic else "可引用"
    else:
        verdict = "仅方法演练，不可引用"
    return {"mode": "doc", "verdict": verdict, "problems": problems, "notes": [], "gates": {},
            "missing_required": miss_req, "missing_optional": miss_opt,
            "forbidden": hits, "items": items}


GATES_RE = re.compile(r"<!--\s*gates:\s*(.*?)\s*-->", re.S)
TOKEN_RE = re.compile(r"\b(M\d+)\s*=\s*(\S+)")


def check_gates(text):
    rep = {"mode": "gates", "verdict": "无法判定", "problems": [], "notes": [], "gates": {}}
    blocks = GATES_RE.findall(strip_code(text))
    if not blocks:
        rep["problems"].append(
            "未找到完整 gates 行：`<!-- gates: M1=4 M2=0 M3=1 M4=0 M5=1 -->`")
        return rep
    if len(blocks) > 1:
        rep["problems"].append(f"gates 行出现 {len(blocks)} 次，无法判定以哪条为准")
        return rep
    # 缩进 ≥4 空格或以 Tab 开头的 gates 行按 CommonMark 是缩进代码块里的示例：不猜，按无法判定处理（fail-closed）
    if re.search(r"(?m)^(?: {4,}|\t)[ \t]*<!--\s*gates:", strip_code(text)):
        rep["problems"].append("gates 行缩进 ≥4 空格或以 Tab 开头（可能是缩进代码块里的示例），无法判定；请顶格写")
        return rep
    body = blocks[0]
    if not body.strip():
        rep["problems"].append("gates 行为空")
        return rep

    strict = re.fullmatch(r"(?:M[1-5]=\d{1,6}\s*)+(?:M1_src=(?:solo|dual)\s*)?", body.strip())
    if not strict:
        rep["problems"].append("gates 行含非法内容（只允许 `M1=… M2=… … M5=… M1_src=solo|dual`，不许夹注释或其它文字）")
        return rep
    if not re.search(r"(?:^|\s)M1_src=(?:solo|dual)(?:\s|$)", body.strip()):
        rep["problems"].append("缺少 M1_src=solo|dual：未声明 M1 计数来源，无法判定")
        return rep
    raw = TOKEN_RE.findall(body)
    unknown = sorted({k for k, _ in raw if k not in ("M1", "M2", "M3", "M4", "M5")})
    if unknown:
        rep["problems"].append("未知指标: " + ",".join(unknown))
        return rep
    bad = [f"{k}={v}" for k, v in raw if not re.fullmatch(r"\d{1,6}", v)]
    if bad:
        rep["problems"].append("指标值非法: " + ",".join(bad))
        return rep
    keys = [k for k, _ in raw]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        rep["problems"].append("指标重复: " + ",".join(dup))
        return rep
    gates = {k: int(v) for k, v in raw}
    miss = [k for k in ("M1", "M2", "M3", "M4", "M5") if k not in gates]
    if miss:
        rep["problems"].append("缺少指标: " + ",".join(miss))
        return rep

    problems, notes = [], []
    src = re.search(r"M1_src=(solo|dual)", body)
    if src and src.group(1) == "solo":
        notes.append("M1_src=solo：单人 M1 只能作前后对比，最高判「带风险交付」，不得判验收通过")
    m1 = gates["M1"]
    b = re.search(r"M1_before\s*=\s*(\d{1,6})", strip_code(text))
    if m1 > 4:
        if not b:
            problems.append(f"M1={m1} >4，且未写 `M1_before=` 基线")
        elif m1 > int(b.group(1)):
            problems.append(f"M1={m1} >4 且高于改造前基线 {b.group(1)}")
        else:
            notes.append(f"M1={m1} >4，但 ≤ 基线 {b.group(1)}：只能按「带风险交付」出，"
                         "且每项需入状态卡")
    for k in ("M2", "M3", "M4", "M5"):
        if gates[k] != 0:
            problems.append(f"{k}={gates[k]} >0（无豁免）")
    if problems:
        verdict = "验收不通过"
    elif notes:
        verdict = "带风险交付"          # M1 超线但 ≤ 基线：可交付，但不是「验收通过」
    else:
        verdict = "验收通过"
    rep.update(verdict=verdict, problems=problems, notes=notes, gates=gates)
    return rep


def exit_code(rep) -> int:
    """四态结论 → 退出码。带风险交付单列 3：它和「验收不通过」都不该是绿，
    但自动化必须能分清「可交、残留需签字」与「不许交」，否则 CI 只会一刀切。"""
    v = rep.get("verdict")
    if v in ("验收通过", "可引用"):
        return 0
    if v == "无法判定":
        return 2
    if v == "带风险交付":
        return 3
    return 1


def render_doc(rep, path="", explain=False):
    out = [f"文件: {path or '(selftest)'}"]
    for r in rep["items"]:
        tag = "OK  " if r["pass"] else ("MISS" if r["required"] else "建议补")
        out.append(f"  [{tag}] {r['item']}" + ("" if r["pass"] else "  <- 缺这一节或无有效证据"))
        if explain and r.get("evidence"):
            out.append(f"         命中: {r['evidence']}")
    if rep["forbidden"]:
        out.append("  [WARN] 命中禁用语: " + "、".join(rep["forbidden"]) + "  <- 只允许分级字样")
    for p in rep.get("problems", []):
        out.append(f"  [FAIL] {p}")
    tail = f"结论: {rep['verdict']}"
    if rep["missing_required"]:
        tail += f"（缺必需 {len(rep['missing_required'])} 项）"
    elif rep["missing_optional"]:
        tail += "（必需项齐；缺可选项，建议补）"
    return "\n".join(out + [tail])


def render_gates(rep, path=""):
    out = [f"文件: {path or '(selftest)'}"]
    if rep.get("gates"):
        out.append("  " + "  ".join(f"{k}={v}" for k, v in rep["gates"].items()))
    for n in rep.get("notes", []):
        out.append(f"  [NOTE] {n}")
    for p in rep.get("problems", []):
        out.append(f"  [FAIL] {p}")
    out.append(f"结论: {rep['verdict']}")
    return "\n".join(out)


# 仅用于解析器自测的合成 fixture；所有数字均为演示占位，不作为真实证据。
GOOD = """# 示例结论

## 一、数据口径
样本 100 个，期间 P1→P2，观测 1,000 条（均为合成占位），单边成本 0.20%。

## 二、数字表
| 持有期 | IC | t_HAC |
|---|---:|---:|
| 1 期 | -1.23 | -2.50 |

## 三、判级理由
判 L3。排除了「样本不足」「成本假设错」两条解释。

## 四、可信度
1 期 IC 可单独引用，其余打折。

## 六、重算命令
```bash
python scripts/example_recompute.py --input data/example.csv
```

## 七、可引用的三条事实
1. 一 2. 二 3. 三

## 五、未覆盖边界
未覆盖样本外与容量。
"""

BAD = """# 示例结论

结果基本有效，大概率可用。
"""

CARD_PASS = "# 交付状态\n<!-- gates: M1=4 M2=0 M3=0 M4=0 M5=0 M1_src=dual -->\n"
CARD_NO_SRC = "# 交付状态\n<!-- gates: M1=4 M2=0 M3=0 M4=0 M5=0 -->\n"
CARD_FAIL = "# 交付状态\n<!-- gates: M1=9 M2=0 M3=1 M4=0 M5=1 M1_src=dual -->\n"
CARD_EXEMPT = "# 交付状态\n基线: M1_before=9\n<!-- gates: M1=6 M2=0 M3=0 M4=0 M5=0 M1_src=dual -->\n"
CARD_MISS = "# 交付状态\n<!-- gates: M2=0 M3=0 M1_src=dual -->\n"
CARD_DUP = "# 交付状态\n<!-- gates: M1=9 M2=0 M3=0 M4=0 M5=0 M1=4 M1_src=dual -->\n"
CARD_BAD = "# 交付状态\n<!-- gates: M1=abc M2=0 M3=0 M4=0 M5=0 M1_src=dual -->\n"
CARD_HUGE = "# 交付状态\n<!-- gates: M1=999999999 M2=0 M3=0 M4=0 M5=0 M1_src=dual -->\n"
CARD_UNKNOWN = "# 交付状态\n<!-- gates: M1=4 M2=0 M3=0 M4=0 M5=0 M9=1 M1_src=dual -->\n"

FAKE_SHORT = """# 年会通知

| 口径 | 判级 | 状态 | 边界 |
|---|---|---|---|
| 1 | L1 | ✅ | 2 |

```bash
```
"""

FAKE_CODE = GOOD.replace("## 七、可引用的三条事实\n1. 一 2. 二 3. 三\n",
                         "## 七、可引用三条\n1. 一\n\n"
                         "另外，报告里禁止出现「基本有效」这种说法。\n")


def selftest():
    fails = []

    def chk(name, cond):
        if not cond:
            fails.append(name)

    g, b = check_doc(GOOD), check_doc(BAD)
    chk("完整文档应可引用", g["verdict"] == "可引用" and not g["missing_required"])
    chk("残缺文档应缺 6 项", b["verdict"] != "可引用" and len(b["missing_required"]) == 6)
    chk("残缺文档应命中禁用语", "基本有效" in b["forbidden"])

    fp = check_doc(GOOD.replace("## 七、可引用的三条事实\n1. 一 2. 二 3. 三\n", ""))
    chk("缺可选项只报建议补", fp["missing_optional"] == ["7 可引用三条"])

    fake = check_doc(FAKE_SHORT)
    chk("空围栏+表头不得判定可引用", fake["verdict"] != "可引用")
    chk("只有表头的表不算数字表",
            "2 数字表" in check_doc("# t" + chr(10) + chr(10) +
                                   "| a | b |" + chr(10) + "|---|---|" + chr(10))["missing_required"])
    chk("空围栏不算重算命令", "6 重算命令" in fake["missing_required"])

    chk("引用禁用语不算命中", not check_doc(FAKE_CODE)["forbidden"])

    quoted_bad = GOOD.replace("判 L3。排除了「样本不足」「成本假设错」两条解释。",
                              "结论：“基本有效”。")
    chk("引号内真实结论仍命中禁用语", "基本有效" in check_doc(quoted_bad)["forbidden"])

    tilde_doc = "~~~markdown\n" + GOOD + "~~~\n"
    chk("~~~ 围栏内示例不得判可引用", check_doc(tilde_doc)["verdict"] != "可引用")
    tilde_card = "~~~markdown\n" + CARD_PASS + "~~~\n"
    chk("~~~ 围栏内 gates 不得通过", check_gates(tilde_card)["verdict"] == "无法判定")

    # 围栏闭合规则：闭合标记不短于开始标记即有效；未闭合围栏到文件末尾都是代码
    long_fence_doc = "```markdown\n" + GOOD + "````\n"
    chk("更长的闭合围栏内示例不得判可引用",
        check_doc(long_fence_doc)["verdict"] != "可引用")
    long_fence_card = "```markdown\n" + CARD_PASS + "````\n"
    chk("更长的闭合围栏内 gates 不得通过",
        check_gates(long_fence_card)["verdict"] == "无法判定")
    unclosed_card = "```markdown\n" + CARD_PASS
    chk("未闭合围栏内 gates 不得通过",
        check_gates(unclosed_card)["verdict"] == "无法判定")
    for pad in ("    ", "\t", "1. item\n\n    "):
        indented_card = "# t\n\n" + pad + "<!-- gates: M1=3 M2=0 M3=0 M4=0 M5=0 M1_src=dual -->\n"
        chk(f"缩进 gates 行不得通过（{pad!r}）",
            check_gates(indented_card)["verdict"] == "无法判定")
    chk("缩进 ≤3 空格的 gates 行仍可通过",
        check_gates("   <!-- gates: M1=3 M2=0 M3=0 M4=0 M5=0 M1_src=dual -->\n")["verdict"] == "验收通过")
    long_cmd = GOOD.replace(
        "```bash\npython scripts/example_recompute.py --input data/example.csv\n```",
        "```bash\npython scripts/example_recompute.py --input data/example.csv\n````")
    chk("更长的闭合围栏内命令仍是有效证据",
        check_doc(long_cmd)["verdict"] == "可引用")
    unclosed_cmd = GOOD.replace(
        "```bash\npython scripts/example_recompute.py --input data/example.csv\n```",
        "```bash\npython scripts/example_recompute.py --input data/example.csv\n")
    chk("未闭合围栏内命令不算证据", check_doc(unclosed_cmd)["verdict"] != "可引用")

    smuggle = GOOD.replace("判 L3。", "这个定义不要用，它的结论\"基本有效\"。")
    chk("讨论了禁用词也不能豁免同一行里的结论",
        "基本有效" in "\n".join(check_doc(smuggle)["problems"])
        or check_doc(smuggle)["verdict"] != "可引用")
    legit = GOOD.replace("判 L3。", "禁用词不要写成\"基本有效\"这类说法。")
    chk("正常讨论禁用词仍被豁免（不误报）",
        not any("基本有效" in p for p in check_doc(legit)["problems"]))

    empty_prose = GOOD
    for prose_txt in ("样本 100 个，期间 P1→P2，观测 1,000 条（均为合成占位），单边成本 0.20%。",
                      "判 L3。排除了「样本不足」「成本假设错」两条解释。",
                      "1 期 IC 可单独引用，其余打折。",
                      "未覆盖样本外与容量。"):
        empty_prose = empty_prose.replace(prose_txt, "```text\n```")
    empty_rep = check_doc(empty_prose)
    chk("空代码围栏不算 prose 正文",
        empty_rep["verdict"] != "可引用" and len(empty_rep["missing_required"]) == 4)
    chk("未覆盖边界必须是最后一节", check_doc(GOOD + "\n## 附录\n额外说明。\n")["verdict"] != "可引用")
    mixed_fence = GOOD.replace("python scripts/example_recompute.py --input data/example.csv",
                               "~~~ 只是围栏内正文\n# 也不是标题\npython scripts/example_recompute.py --input data/example.csv")
    chk("围栏内另一种标记不打断章节解析", check_doc(mixed_fence)["verdict"] == "可引用")

    chk("gates 全 0 且来源齐 = 通过", check_gates(CARD_PASS)["verdict"] == "验收通过")
    chk("缺 M1_src = 无法判定，不得通过", check_gates(CARD_NO_SRC)["verdict"] == "无法判定")
    f = check_gates(CARD_FAIL)
    chk("gates 超线不通过", f["verdict"] == "验收不通过" and len(f["problems"]) == 3)
    chk("gates 带基线 -> 带风险交付（不是通过）",
        check_gates(CARD_EXEMPT)["verdict"] == "带风险交付"
        and bool(check_gates(CARD_EXEMPT)["notes"]))
    chk("无 gates 行 = 无法判定", check_gates("没有状态卡的文档")["verdict"] == "无法判定")
    chk("缺键 = 无法判定", check_gates(CARD_MISS)["verdict"] == "无法判定")
    chk("重复键 = 无法判定", check_gates(CARD_DUP)["verdict"] == "无法判定")
    chk("非法值 = 无法判定", check_gates(CARD_BAD)["verdict"] == "无法判定")
    chk("未知键 = 无法判定", check_gates(CARD_UNKNOWN)["verdict"] == "无法判定")
    chk("超长数字不崩", check_gates(CARD_HUGE)["verdict"] in ("验收通过", "验收不通过", "无法判定"))
    # 对抗用例 1：gates 行夹杂文本（只认完整语法）
    NJ = chr(10)
    junk = "# t" + NJ + "<!-- gates: 备注随便写 M1=4 M2=0 M3=0 M4=0 M5=0 结束 -->" + NJ
    chk("gates 夹杂文本 = 无法判定", check_gates(junk)["verdict"] == "无法判定")

    # 对抗用例 3：单人 M1 不得判「验收通过」
    solo = "# t" + chr(10) + "<!-- gates: M1=3 M2=0 M3=0 M4=0 M5=0 M1_src=solo -->" + chr(10)
    chk("单人 M1 = 带风险交付", check_gates(solo)["verdict"] == "带风险交付")

    # 退出码四态：带风险交付单列 3，不得与「不通过」共用 1
    chk("exit 通过=0", exit_code({"verdict": "验收通过"}) == 0)
    chk("exit 不通过=1", exit_code({"verdict": "验收不通过"}) == 1)
    chk("exit 无法判定=2", exit_code({"verdict": "无法判定"}) == 2)
    chk("exit 带风险交付=3", exit_code({"verdict": "带风险交付"}) == 3)
    chk("exit 文档态：可引用=0 / 不可引用=1",
        exit_code({"verdict": "可引用"}) == 0
        and exit_code({"verdict": "仅方法演练，不可引用"}) == 1)

    # 数字对账：符号不参与比对，单位必须保留
    def _numdoc(reason):
        nj = chr(10)
        return nj.join([
            "# t", "",
            "## 一、数据口径", "样本 100 个。", "",
            "## 二、数字表", "| 项 | 值 |", "|---|---|", "| t_HAC | -1.23 |", "",
            "## 三、判级理由", reason, "",
            "## 四、可信度", "可引用。", "",
            "## 五、重算命令", "```bash", "python x.py", "```", "",
            "## 六、未覆盖的边界", "无。", ""])

    chk("数字对账：同号同单位可引用",
        check_doc(_numdoc("判级：t=−1.23 未过门槛。"))["verdict"] == "可引用")
    chk("数字对账：符号翻转不判死（见 _norm_num 注释）",
        check_doc(_numdoc("判级：t=+1.23 未过门槛。"))["verdict"] == "可引用")
    chk("数字对账：单位不同必须报",
        check_doc(_numdoc("判级：t=−1.23% 未过门槛。"))["verdict"] != "可引用")

    # 数字对账的参照文本必须先剥代码：代码块里的同值数字不能给结论作保
    code_number = _numdoc("判级：t=−9.99 未过门槛。")
    code_number = code_number.replace(
        "样本 100 个。", "样本 100 个。\n\n```text\n−9.99\n```")
    chk("代码块中的数字不算对账依据", check_doc(code_number)["verdict"] != "可引用")

    # 「不要」等词本身不是禁用语豁免：必须写成明确的禁用语讨论句
    bypass = _numdoc("不要漏记：最终结论是「基本有效」。")
    chk("普通『不要』句不豁免真实结论",
        "基本有效" in check_doc(bypass)["forbidden"])

    # 脚本核验：--root 下重算命令引用的脚本必须真实存在
    here_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    chk("--root 下不存在的重算脚本被拒",
        check_doc(GOOD, root=here_root)["verdict"] != "可引用")
    good_real_cmd = GOOD.replace("python scripts/example_recompute.py --input data/example.csv",
                                 "python scripts/doctrine_check.py --selftest")
    chk("--root 下真实存在的重算脚本通过",
        check_doc(good_real_cmd, root=here_root)["verdict"] == "可引用")

    # 对抗用例 4：合成示例不得判「可引用」
    syn = "synthetic: true" + chr(10) + chr(10) + GOOD
    chk("合成示例不得判可引用", check_doc(syn)["verdict"] != "可引用")

    # 对抗用例 2：数字表不在对应章节（全文找表 = 假通过）
    global_table = NJ.join([
        "# t", "",
        "## 一、数据口径", "样本 100 个。", "",
        "| a | b |", "|---|---|", "| 1 | 2 |", "",
        "## 三、判级理由", "L1。", "",
        "## 四、可信度", "可信。", "",
        "## 五、边界", "无。", "",
        "## 六、重算命令", "```bash", "python x.py", "```", "",
        "## 七、可引用三条", "1. a", ""])
    chk("无数字表章节 = 不可引用", check_doc(global_table)["verdict"] != "可引用")

    later_table = GOOD.replace("## 二、数字表\n",
                               "## 二、数字表说明\n这一节只有说明。\n\n## 二、数字表\n", 1)
    chk("同名候选要继续找有效证据节", check_doc(later_table)["verdict"] == "可引用")

    keys = {"mode", "verdict", "problems", "notes", "gates"}
    for rep in (check_gates(CARD_PASS), check_gates("x"), check_gates(CARD_MISS),
                check_gates(CARD_DUP), check_gates(CARD_BAD), check_doc(GOOD)):
        chk("分支返回键同构", keys <= set(rep.keys()))

    try:
        render_gates(check_gates("x"))
        render_gates(check_gates(CARD_PASS))
    except Exception as exc:                        # noqa: BLE001
        fails.append(f"render_gates 崩溃: {exc}")

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for fn in ("SKILL.md", "README.md"):
        p = os.path.join(here, fn)
        if os.path.exists(p):
            chk(f"{fn} 应判不可引用",
                check_doc(read_text_strict(p))["verdict"] != "可引用")

    print("selftest:", "PASS" if not fails else "FAIL")
    if fails:
        for item in fails:
            print("  [FAIL]", item)
    else:
        print("  doc: 完整/残缺/假阳性/代码块引用 四类回归全过")
        print("  gates: 通过/不通过/无法判定（缺行·缺键·重复·非法·未知·超长·围栏）全分支不崩")
        print("  对账/禁用语: 代码块数字不作依据；只有明确讨论句才豁免引号")
        print("  SKILL.md / README.md 判不可引用（自证线）")
    return not fails


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                               # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--doc", action="store_true", help="查结论文档七件套（默认）")
    ap.add_argument("--gates", action="store_true", help="查交付状态卡的 gates 行")
    ap.add_argument("--root", help="可选：项目根目录，校验重算命令引用的脚本文件是否存在")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--explain", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if not a.path or not os.path.isfile(a.path):
        print("用法: doctrine_check.py <文件> [--doc|--gates] [--explain|--json] | --selftest")
        sys.exit(2)
    try:
        text = read_text_strict(a.path)
    except (OSError, ValueError) as exc:            # noqa: BLE001
        print(f"读文件失败: {exc}")
        sys.exit(2)
    if a.gates:
        rep = check_gates(text)
        print(json.dumps(rep, ensure_ascii=False, indent=2) if a.json
              else render_gates(rep, a.path))
        sys.exit(exit_code(rep))
    rep = check_doc(text, root=a.root)
    print(json.dumps(rep, ensure_ascii=False, indent=2) if a.json
          else render_doc(rep, a.path, a.explain))
    sys.exit(exit_code(rep))


if __name__ == "__main__":
    main()
