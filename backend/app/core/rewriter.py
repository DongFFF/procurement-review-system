"""
自动修订引擎。

输入：原文档 + 用户勾选的审查意见
输出：修订后的文档 + 变更对照表

修订策略（按可靠性排序）：
  1. **替换**：意见带有 original_text + replacement → 在文档中定位该段落并替换。
     跨 run 的文本也能处理（Word 常把一个句子拆成多个 run）。
  2. **新增**：意见为「缺失」类且给出 replacement 条款文本 → 插入到对应章节。
  3. **批注**：无法自动定位的 → 写入修订说明表，供人工处理，绝不静默丢弃。

同时生成「修订说明表」段落，便于送审时说明改了什么、依据是什么。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import EXPORT_DIR
from .reviewer import Finding


class RewriteError(RuntimeError):
    """修订失败。"""


@dataclass
class ChangeRecord:
    """一条变更记录。"""

    finding_id: str
    rule_name: str
    severity: str
    action: str            # replaced | inserted | manual | skipped
    location: str = ""
    before: str = ""
    after: str = ""
    legal_basis: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "rule_name": self.rule_name,
            "severity": self.severity,
            "action": self.action,
            "location": self.location,
            "before": self.before,
            "after": self.after,
            "legal_basis": self.legal_basis,
            "note": self.note,
        }


@dataclass
class RewriteResult:
    """修订结果。"""

    output_path: str
    changes: list[ChangeRecord] = field(default_factory=list)
    replaced: int = 0
    inserted: int = 0
    manual: int = 0
    skipped: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_path": self.output_path,
            "output_name": Path(self.output_path).name,
            "replaced": self.replaced,
            "inserted": self.inserted,
            "manual": self.manual,
            "skipped": self.skipped,
            "total": len(self.changes),
            "changes": [c.to_dict() for c in self.changes],
        }


def _fmt_cn_datetime(dt: datetime, with_time: bool = True) -> str:
    """
    格式化为中文日期字符串，例如「2026年10月13日 09:30」。

    不能用 dt.strftime('%Y年%m月%d日')：Windows 上 strftime 会用系统区域编码
    去编码格式串，在非中文/非 UTF-8 区域的机器上会抛
    UnicodeEncodeError: 'locale' codec can't encode character '\\u5e74'。
    所以这里只对数字部分用 strftime（纯 ASCII 安全），中文部分直接拼接。
    """
    date_part = f"{dt.strftime('%Y')}年{dt.strftime('%m')}月{dt.strftime('%d')}日"
    if not with_time:
        return date_part
    return f"{date_part} {dt.strftime('%H:%M')}"


# ---------------------------------------------------------------------------
# 文本归一化与匹配
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    """归一化：去空白、全角转半角标点，用于宽松匹配。"""
    if not s:
        return ""
    s = re.sub(r"\s+", "", s)
    table = str.maketrans({
        "（": "(", "）": ")", "，": ",", "。": ".", "；": ";",
        "：": ":", "“": '"', "”": '"', "‘": "'", "’": "'",
        "、": ",", "《": "<", "》": ">",
    })
    return s.translate(table)


def _find_paragraph_index(paragraphs: list, needle: str) -> int:
    """在段落列表中定位包含 needle 的段落，返回索引，找不到返回 -1。"""
    if not needle or not needle.strip():
        return -1

    n_norm = _norm(needle)

    # 精确匹配（归一化后）
    for i, p in enumerate(paragraphs):
        if n_norm and n_norm in _norm(p.text):
            return i

    # 宽松匹配：取 needle 的前 20 个字符做前缀匹配
    if len(n_norm) >= 8:
        head = n_norm[:20]
        for i, p in enumerate(paragraphs):
            if head in _norm(p.text):
                return i

    # 再宽松：按字符重合度（适用于模型轻微改写的情况）
    if len(n_norm) >= 12:
        best_i, best_score = -1, 0.0
        grams = {n_norm[i : i + 4] for i in range(len(n_norm) - 3)}
        if grams:
            for i, p in enumerate(paragraphs):
                pn = _norm(p.text)
                if len(pn) < 4:
                    continue
                pgrams = {pn[j : j + 4] for j in range(len(pn) - 3)}
                if not pgrams:
                    continue
                score = len(grams & pgrams) / len(grams)
                if score > best_score:
                    best_score, best_i = score, i
        if best_score >= 0.6:
            return best_i

    return -1


def _replace_in_paragraph(paragraph, old: str, new: str) -> bool:
    """
    在段落内替换文本，尽量保留格式。

    Word 会把一句话拆成多个 run，直接改 run.text 会破坏匹配。
    做法：把 run 文本拼起来定位，命中后把新文本写入首个 run，其余清空。
    """
    runs = paragraph.runs
    if not runs:
        return False

    full = "".join(r.text for r in runs)
    if not full.strip():
        return False

    # 定位
    idx = -1
    target = old
    if old and old in full:
        idx = full.find(old)
    else:
        # 归一化定位
        n_full = _norm(full)
        n_old = _norm(old)
        if n_old and n_old in n_full:
            # 归一化索引映射回原串（近似：按比例缩放）
            ratio = len(full) / max(len(n_full), 1)
            approx = int(n_full.find(n_old) * ratio)
            idx = max(0, min(approx, len(full) - 1))
            # 从 approx 附近找到最接近的真实起点
            best, best_d = -1, 10**9
            for cand in range(max(0, approx - 30), min(len(full), approx + 30)):
                d = abs(cand - approx)
                if d < best_d:
                    best_d, best = d, cand
            idx = best
            target = full[idx : idx + max(len(old), 1)]
        else:
            return False

    if idx < 0:
        return False

    end = idx + len(target)
    new_full = full[:idx] + new + full[end:]

    # 写回：第一个 run 承载全部文本，其余清空（保留首 run 格式）
    runs[0].text = new_full
    for r in runs[1:]:
        r.text = ""
    return True


# ---------------------------------------------------------------------------
# 修订器
# ---------------------------------------------------------------------------

class DocumentRewriter:
    """对 .docx 执行修订。"""

    def __init__(self, source_path: str | Path):
        self.source = Path(source_path)
        if not self.source.exists():
            raise RewriteError(f"源文件不存在：{self.source}")
        if self.source.suffix.lower() != ".docx":
            raise RewriteError(
                f"自动修订目前仅支持 .docx 格式（当前为 {self.source.suffix}）。"
                f"请将文件另存为 .docx 后重试。"
            )

    # -- 主流程 ------------------------------------------------------------
    def apply(
        self,
        findings: list[Finding],
        *,
        output_name: str | None = None,
        append_change_log: bool = True,
        add_highlights: bool = True,
    ) -> RewriteResult:
        try:
            from docx import Document  # type: ignore
            from docx.enum.text import WD_COLOR_INDEX  # type: ignore
        except ImportError as e:
            raise RewriteError("缺少 python-docx，无法执行自动修订") from e

        # 复制一份再改，原文件绝不动
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = self.source.stem
        out_name = output_name or f"{stem}_已修订_{ts}.docx"
        if not out_name.lower().endswith(".docx"):
            out_name += ".docx"
        out_path = EXPORT_DIR / out_name

        shutil.copy2(self.source, out_path)

        try:
            doc = Document(str(out_path))
        except Exception as e:
            raise RewriteError(f"打开文档失败：{e}") from e

        paragraphs = doc.paragraphs
        changes: list[ChangeRecord] = []
        result = RewriteResult(output_path=str(out_path))

        # 只处理用户勾选且确有问题的意见
        targets = [f for f in findings if f.selected and f.violated]

        for f in targets:
            rec = self._apply_one(
                doc, paragraphs, f, WD_COLOR_INDEX, add_highlights
            )
            changes.append(rec)
            if rec.action == "replaced":
                result.replaced += 1
            elif rec.action == "inserted":
                result.inserted += 1
            elif rec.action == "manual":
                result.manual += 1
            else:
                result.skipped += 1

        # 追加修订说明表
        if append_change_log and changes:
            try:
                self._append_change_log(doc, changes)
            except Exception:
                pass  # 说明表失败不影响正文修订

        try:
            doc.save(str(out_path))
        except Exception as e:
            raise RewriteError(f"保存修订文档失败：{e}") from e

        result.changes = changes
        return result

    # -- 单条处理 ----------------------------------------------------------
    def _apply_one(
        self,
        doc,
        paragraphs: list,
        f: Finding,
        wd_color,
        add_highlights: bool,
    ) -> ChangeRecord:
        rec = ChangeRecord(
            finding_id=f.id,
            rule_name=f.rule_name,
            severity=f.severity,
            action="skipped",
            location=f.location,
            before=f.original_text,
            after=f.replacement,
            legal_basis=f.legal_basis or f.rule_name,
        )

        has_original = bool(f.original_text and f.original_text.strip())
        has_replacement = bool(f.replacement and f.replacement.strip())

        # --- 情形 1：有原文 + 有替换文本 → 定位替换 ---
        if has_original and has_replacement:
            idx = _find_paragraph_index(paragraphs, f.original_text)
            if idx >= 0:
                p = paragraphs[idx]
                ok = _replace_in_paragraph(p, f.original_text, f.replacement)
                if ok:
                    if add_highlights:
                        try:
                            for r in p.runs:
                                if r.text:
                                    r.font.highlight_color = wd_color.YELLOW
                        except Exception:
                            pass
                    rec.action = "replaced"
                    rec.note = f"已替换第 {idx + 1} 段"
                    return rec

            # 定位失败 → 转人工
            rec.action = "manual"
            rec.note = "未能自动定位原文（模型摘录与原文可能有差异），已列入修订说明表，请人工处理"
            return rec

        # --- 情形 2：缺失类 + 有新增条款文本 → 插入到对应章节 ---
        if not has_original and has_replacement:
            ok = self._insert_clause(doc, paragraphs, f, wd_color, add_highlights)
            if ok:
                rec.action = "inserted"
                rec.note = f"已在「{f.location or '文末'}」新增条款"
            else:
                rec.action = "manual"
                rec.note = "未能确定插入位置，已列入修订说明表，请人工补充"
            return rec

        # --- 情形 3：有问题但模型没给可替换文本 → 人工 ---
        rec.action = "manual"
        rec.note = "该意见未给出可直接套用的修订文本，请按建议人工修改"
        return rec

    # -- 插入新条款 --------------------------------------------------------
    @staticmethod
    def _insert_clause(doc, paragraphs: list, f: Finding, wd_color, highlight: bool) -> bool:
        """把新增条款插入到匹配章节的末尾。"""
        target_section = (f.location or "").strip()

        anchor_idx = -1
        if target_section:
            # 找章节标题段
            for i, p in enumerate(paragraphs):
                t = p.text.strip()
                if not t or len(t) > 60:
                    continue
                if target_section in t or t in target_section:
                    anchor_idx = i
                    break

        new_text = f.replacement.strip()

        try:
            if anchor_idx >= 0:
                anchor = paragraphs[anchor_idx]
                new_p = anchor.insert_paragraph_before(new_text)
                # 把新段挪到 anchor 之后
                anchor._p.addnext(new_p._p)
            else:
                new_p = doc.add_paragraph(new_text)

            if highlight:
                try:
                    for r in new_p.runs:
                        r.font.highlight_color = wd_color.BRIGHT_GREEN
                except Exception:
                    pass
            return True
        except Exception:
            return False

    # -- 修订说明表 --------------------------------------------------------
    @staticmethod
    def _append_change_log(doc, changes: list[ChangeRecord]) -> None:
        """在文末追加修订说明表。"""
        doc.add_page_break()
        h = doc.add_paragraph()
        run = h.add_run("附：采购需求文件修订说明表")
        try:
            run.bold = True
            run.font.size = run.font.size.__class__(14 * 12700)  # 14pt
        except Exception:
            pass

        meta = doc.add_paragraph(
            f"修订时间：{_fmt_cn_datetime(datetime.now())}    "
            f"修订项数：{len(changes)}"
        )
        try:
            meta.runs[0].font.size = meta.runs[0].font.size.__class__(9 * 12700)
        except Exception:
            pass

        table = doc.add_table(rows=1, cols=5)
        try:
            table.style = "Table Grid"
        except Exception:
            pass

        headers = ["序号", "问题（规则）", "处理方式", "修改依据", "修改内容"]
        for i, htext in enumerate(headers):
            cell = table.rows[0].cells[i]
            cell.text = htext
            try:
                for p in cell.paragraphs:
                    for r in p.runs:
                        r.bold = True
            except Exception:
                pass

        action_label = {
            "replaced": "已替换",
            "inserted": "已新增",
            "manual": "待人工处理",
            "skipped": "未处理",
        }

        for i, c in enumerate(changes, 1):
            row = table.add_row().cells
            row[0].text = str(i)
            row[1].text = c.rule_name
            row[2].text = action_label.get(c.action, c.action)
            row[3].text = c.legal_basis
            if c.action == "replaced":
                row[4].text = f"原：{c.before[:120]}\n改为：{c.after[:200]}"
            elif c.action == "inserted":
                row[4].text = f"新增：{c.after[:250]}"
            else:
                row[4].text = f"{c.note}。建议：{c.after[:200] or '见审查意见'}"


# ---------------------------------------------------------------------------
# 导出审查报告
# ---------------------------------------------------------------------------

def export_report_docx(report_dict: dict[str, Any], *, output_name: str | None = None) -> str:
    """把审查报告导出为 Word 文档。"""
    try:
        from docx import Document  # type: ignore
        from docx.shared import Pt, RGBColor  # type: ignore
    except ImportError as e:
        raise RewriteError("缺少 python-docx，无法导出报告") from e

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = output_name or f"采购需求审查报告_{ts}.docx"
    if not out_name.lower().endswith(".docx"):
        out_name += ".docx"
    out_path = EXPORT_DIR / out_name

    doc = Document()
    summary = report_dict.get("summary", {})

    # 标题
    title = doc.add_paragraph()
    tr = title.add_run("中直高校采购需求文件审查报告")
    tr.bold = True
    tr.font.size = Pt(18)
    title.alignment = 1  # center

    # 基本信息
    info = doc.add_paragraph()
    info.add_run(f"被审查文件：{report_dict.get('filename', '')}\n").font.size = Pt(10.5)
    info.add_run(
        f"审查时间：{_fmt_cn_datetime(datetime.fromtimestamp(report_dict.get('created_at', 0)))}\n"
    ).font.size = Pt(10.5)
    mi = report_dict.get("model_info", {})
    info.add_run(f"审查模型：{mi.get('provider', '')} / {mi.get('model', '')}\n").font.size = Pt(10.5)
    info.add_run(
        f"审查规则数：{report_dict.get('rules_evaluated', 0)}    "
        f"耗时：{report_dict.get('elapsed_sec', 0)} 秒"
    ).font.size = Pt(10.5)

    # 审查结论
    doc.add_paragraph()
    h2 = doc.add_paragraph()
    r2 = h2.add_run("一、审查结论")
    r2.bold = True
    r2.font.size = Pt(14)

    verdict_p = doc.add_paragraph()
    vr = verdict_p.add_run(f"总体判定：{summary.get('verdict', '')}")
    vr.bold = True
    vr.font.size = Pt(12)
    level = summary.get("verdict_level", "")
    try:
        if level == "fail":
            vr.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
        elif level == "warn":
            vr.font.color.rgb = RGBColor(0xE0, 0x6C, 0x00)
        else:
            vr.font.color.rgb = RGBColor(0x1E, 0x7D, 0x32)
    except Exception:
        pass

    doc.add_paragraph(summary.get("verdict_text", ""))

    # 统计
    stat = doc.add_paragraph()
    stat.add_run(
        f"问题统计：严重 {summary.get('blocker_count', 0)} 项、"
        f"高 {summary.get('high_count', 0)} 项、"
        f"中 {summary.get('medium_count', 0)} 项、"
        f"低 {summary.get('low_count', 0)} 项；"
        f"通过 {summary.get('pass_count', 0)} 项。"
    )
    by_cat = summary.get("by_category") or {}
    if by_cat:
        doc.add_paragraph(
            "问题分布：" + "；".join(f"{k} {v} 项" for k, v in by_cat.items())
        )

    # 问题清单
    doc.add_paragraph()
    h3 = doc.add_paragraph()
    r3 = h3.add_run("二、问题清单与整改建议")
    r3.bold = True
    r3.font.size = Pt(14)

    issues = [f for f in report_dict.get("findings", []) if f.get("violated")]
    if not issues:
        doc.add_paragraph("未发现合规性问题。")
    for i, f in enumerate(issues, 1):
        p = doc.add_paragraph()
        head = p.add_run(f"{i}. [{f.get('severity_label', '')}] {f.get('title') or f.get('rule_name')}")
        head.bold = True
        head.font.size = Pt(12)

        body_lines = []
        if f.get("location"):
            body_lines.append(f"问题位置：{f['location']}")
        body_lines.append(f"问题类别：{f.get('category_label', '')}")
        if f.get("detail"):
            body_lines.append(f"问题说明：{f['detail']}")
        if f.get("original_text"):
            body_lines.append(f"原文摘录：{f['original_text']}")
        if f.get("legal_basis"):
            body_lines.append(f"法规依据：{f['legal_basis']}")
        if f.get("suggestion"):
            body_lines.append(f"整改建议：{f['suggestion']}")
        if f.get("replacement"):
            body_lines.append(f"建议修改为：{f['replacement']}")
        cites = f.get("citations") or []
        if cites:
            body_lines.append(
                "知识库依据：" + "；".join(c.get("citation", "") for c in cites[:3] if c.get("citation"))
            )
        body_lines.append(f"判定置信度：{f.get('confidence', 0):.0%}")
        if f.get("needs_human_review"):
            body_lines.append("⚠ 该项置信度较低，建议人工复核")

        doc.add_paragraph("\n".join(body_lines))

    # 附录：通过项
    passed = [f for f in report_dict.get("findings", []) if not f.get("violated")]
    if passed:
        doc.add_paragraph()
        h4 = doc.add_paragraph()
        r4 = h4.add_run("三、审查通过项")
        r4.bold = True
        r4.font.size = Pt(14)
        for f in passed:
            doc.add_paragraph(f"✓ {f.get('rule_name')}（{f.get('category_label', '')}）", style="List Bullet")

    doc.save(str(out_path))
    return str(out_path)


def export_report_markdown(report_dict: dict[str, Any], *, output_name: str | None = None) -> str:
    """把审查报告导出为 Markdown。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = output_name or f"采购需求审查报告_{ts}.md"
    if not out_name.lower().endswith(".md"):
        out_name += ".md"
    out_path = EXPORT_DIR / out_name

    s = report_dict.get("summary", {})
    mi = report_dict.get("model_info", {})
    lines = [
        "# 中直高校采购需求文件审查报告",
        "",
        f"- **被审查文件**：{report_dict.get('filename', '')}",
        f"- **审查模型**：{mi.get('provider', '')} / {mi.get('model', '')}",
        f"- **审查规则数**：{report_dict.get('rules_evaluated', 0)}",
        f"- **耗时**：{report_dict.get('elapsed_sec', 0)} 秒",
        "",
        "## 一、审查结论",
        "",
        f"**总体判定：{s.get('verdict', '')}**",
        "",
        s.get("verdict_text", ""),
        "",
        f"- 严重：{s.get('blocker_count', 0)} 项",
        f"- 高：{s.get('high_count', 0)} 项",
        f"- 中：{s.get('medium_count', 0)} 项",
        f"- 低：{s.get('low_count', 0)} 项",
        f"- 通过：{s.get('pass_count', 0)} 项",
        "",
        "## 二、问题清单与整改建议",
        "",
    ]

    issues = [f for f in report_dict.get("findings", []) if f.get("violated")]
    if not issues:
        lines.append("未发现合规性问题。")
    for i, f in enumerate(issues, 1):
        lines += [
            f"### {i}. [{f.get('severity_label', '')}] {f.get('title') or f.get('rule_name')}",
            "",
            f"- **问题位置**：{f.get('location') or '—'}",
            f"- **问题类别**：{f.get('category_label', '')}",
            f"- **问题说明**：{f.get('detail', '')}",
        ]
        if f.get("original_text"):
            lines.append(f"- **原文摘录**：`{f['original_text']}`")
        lines.append(f"- **法规依据**：{f.get('legal_basis') or '—'}")
        lines.append(f"- **整改建议**：{f.get('suggestion', '')}")
        if f.get("replacement"):
            lines.append(f"- **建议修改为**：{f['replacement']}")
        lines.append(f"- **置信度**：{f.get('confidence', 0):.0%}")
        if f.get("needs_human_review"):
            lines.append("- ⚠️ **建议人工复核**")
        lines.append("")

    passed = [f for f in report_dict.get("findings", []) if not f.get("violated")]
    if passed:
        lines += ["## 三、审查通过项", ""]
        for f in passed:
            lines.append(f"- ✅ {f.get('rule_name')}（{f.get('category_label', '')}）")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return str(out_path)
