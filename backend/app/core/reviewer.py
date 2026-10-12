"""
审查引擎。

流程：
  1. 解析采购需求文件 → 分节
  2. 规则粗筛 → 挑出适用规则
  3. 每条规则：
       a. 从知识库检索对应法规依据（RAG）
       b. 组装提示词，调用大模型做「结构化判断」
       c. 强制 JSON 输出，解析为审查意见
  4. 汇总去重 → 生成清晰结论（总体判定 + 问题清单 + 风险分布）
  5. 每条意见都带：定位、法规依据、原文摘录、修改建议、可直接套用的替换文本

设计原则：
  - 结论必须「可追溯」：每条意见都能指回法规条款和原文位置。
  - 结论必须「可执行」：每条意见都给出可直接替换的修订文本。
  - 不确定就说不确定：模型被要求给出 confidence，低置信度会被标注待人工复核。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import settings
from .knowledge import KnowledgeBase, get_kb
from .llm import LLMGateway, LLMError, gather_limited
from .parser import ParsedDocument
from .rules import CATEGORY_LABEL, SEVERITY_LABEL, SEVERITY_ORDER, Rule, RuleLibrary


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    """一条审查意见。"""

    id: str
    rule_code: str
    rule_name: str
    category: str
    severity: str
    # 判断结果
    violated: bool = False
    title: str = ""
    detail: str = ""
    # 定位
    location: str = ""            # 如「技术参数 第 12 段」
    original_text: str = ""       # 原文摘录
    # 依据
    legal_basis: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    # 建议
    suggestion: str = ""
    replacement: str = ""         # 建议替换文本，用于自动修订
    # 元信息
    confidence: float = 0.0
    needs_human_review: bool = False
    selected: bool = True         # 用户是否勾选采纳
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "rule_code": self.rule_code,
            "rule_name": self.rule_name,
            "category": self.category,
            "category_label": CATEGORY_LABEL.get(self.category, self.category),
            "severity": self.severity,
            "severity_label": SEVERITY_LABEL.get(self.severity, self.severity),
            "violated": self.violated,
            "title": self.title,
            "detail": self.detail,
            "location": self.location,
            "original_text": self.original_text,
            "legal_basis": self.legal_basis,
            "citations": self.citations,
            "suggestion": self.suggestion,
            "replacement": self.replacement,
            "confidence": self.confidence,
            "needs_human_review": self.needs_human_review,
            "selected": self.selected,
            "error": self.error,
        }


@dataclass
class ReviewReport:
    """一次完整审查的结论。"""

    review_id: str
    filename: str
    created_at: float
    findings: list[Finding] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    doc_meta: dict[str, Any] = field(default_factory=dict)
    model_info: dict[str, Any] = field(default_factory=dict)
    elapsed_sec: float = 0.0
    rules_evaluated: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def issues(self) -> list[Finding]:
        """真正有问题的意见（违规或缺失）。"""
        return [f for f in self.findings if f.violated]

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "filename": self.filename,
            "created_at": self.created_at,
            "elapsed_sec": round(self.elapsed_sec, 1),
            "rules_evaluated": self.rules_evaluated,
            "summary": self.summary,
            "doc_meta": self.doc_meta,
            "model_info": self.model_info,
            "errors": self.errors,
            "findings": [f.to_dict() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# 提示词
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是一名资深的政府采购合规审查专家，长期为中央部门直属高校（中直高校）审核采购需求文件。

你的职责：依据中国政府采购法律法规，逐条核查采购需求文件，找出**真实存在**的合规问题、缺失要素和倾向性条款。

审查铁律：
1. **只报告有依据的问题**。每条判断必须能对应到具体的法规要求。没有依据的猜测不要报。
2. **区分「违规」与「缺失」**：
   - 违规：文件里有这个内容，但内容不合法/不合理/有倾向性。
   - 缺失：文件里应当有但没有这个内容。
3. **给出可直接使用的整改文本**。建议不能停留在「应当完善」，必须写出具体怎么改、改成什么。
4. **不确定就标低置信度**。信息不足时把 confidence 设为 0.5 以下，并说明需要补充什么信息，绝不硬编一个结论。
5. **原文摘录必须逐字准确**，从给定文本中原样复制，不要改写、不要概括。若找不到具体原文（属于"缺失"类问题），original_text 留空字符串。
6. 不要因为文件写得简短就臆造问题；也不要因为文件很长就放过明显问题。

输出必须是严格的 JSON，不要任何解释性文字、不要 markdown 围栏。"""


def build_rule_prompt(
    rule: Rule,
    doc_context: str,
    kb_context: str,
    *,
    doc_title: str = "",
) -> str:
    """为单条规则组装用户提示词。"""
    basis = rule.legal_basis or "（以知识库检索到的法规为准）"
    kb_block = kb_context.strip() or "（知识库暂无相关条文，请依据你的法规知识判断，并降低置信度）"

    return f"""## 待审查文件
文件名：{doc_title or "采购需求文件"}

## 待审查内容（按章节组织）
{doc_context}

## 知识库检索到的法规依据
{kb_block}

## 本次审查任务
规则名称：{rule.name}
规则代码：{rule.code}
规则类别：{CATEGORY_LABEL.get(rule.category, rule.category)}
严重级别：{SEVERITY_LABEL.get(rule.severity, rule.severity)}
法定依据：{basis}

审查要求：
{rule.check_prompt}

{f"典型违规表述参考（仅作判断参照，不要照搬）：{rule.violation_example}" if rule.violation_example else ""}

## 输出格式（严格 JSON，不要 markdown 围栏）
{{
  "violated": true 或 false,
  "title": "问题一句话概括（20字以内，violated为false时留空）",
  "detail": "详细说明：哪里有问题、为什么违反规定、依据是什么（violated为false时简述为何通过）",
  "location": "问题所在章节或位置，如「技术参数」或「资格条件 第3条」（找不到留空）",
  "original_text": "从待审查内容中原样逐字复制的原文片段（缺失类问题留空字符串）",
  "legal_basis": "对应法规条款，如「《政府采购法实施条例》第二十条」（不确定则写文件名）",
  "suggestion": "具体整改建议，说明怎么改",
  "replacement": "建议直接替换原文的完整文本（若为缺失类问题，写出应当新增的完整条款文本；无法给出则留空字符串）",
  "confidence": 0.0 到 1.0 之间的数字，表示你对该判断的把握
}}"""


# ---------------------------------------------------------------------------
# 审查引擎
# ---------------------------------------------------------------------------

ProgressFn = Callable[[str, int, int, str], None]


class ReviewEngine:
    """审查引擎。"""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        kb: KnowledgeBase | None = None,
        library: RuleLibrary | None = None,
    ):
        self.gateway = gateway
        self.kb = kb or get_kb()
        self.library = library

    # -- 主流程 ------------------------------------------------------------
    async def review(
        self,
        doc: ParsedDocument,
        *,
        only_rules: list[str] | None = None,
        on_progress: ProgressFn | None = None,
    ) -> ReviewReport:
        t0 = time.perf_counter()
        review_id = uuid.uuid4().hex[:12]
        report = ReviewReport(
            review_id=review_id,
            filename=doc.filename,
            created_at=time.time(),
            doc_meta={
                "filename": doc.filename,
                "filetype": doc.filetype,
                "char_count": len(doc.full_text),
                "sections": list(doc.sections().keys()),
                "block_count": len(doc.blocks),
                **(doc.meta or {}),
            },
            model_info={
                "provider": self.gateway.profile.display(),
                "model": self.gateway.profile.model,
                "base_url": self.gateway.base_url,
            },
        )

        # 1) 规则粗筛
        lib = self.library
        if lib is None:
            from .rules import get_rule_library

            lib = get_rule_library()

        sections = doc.sections()
        applicable = lib.prefilter(sections, only_codes=only_rules)
        if not applicable:
            report.errors.append("没有匹配到任何适用规则，请检查规则库配置")
            report.elapsed_sec = time.perf_counter() - t0
            report.summary = self._summarize(report)
            return report

        report.rules_evaluated = len(applicable)

        # 2) 组装文档上下文（分节，超长则截断并保留关键章节）
        doc_context = self._build_doc_context(sections, settings.max_chunk_chars)

        # 3) 逐规则审查（并发）
        total = len(applicable)
        done = 0
        lock = asyncio.Lock()

        async def run_one(rule: Rule) -> Finding:
            nonlocal done
            f = await self._evaluate_rule(rule, doc_context, doc.filename)
            async with lock:
                done += 1
                if on_progress:
                    try:
                        on_progress(
                            rule.code,
                            done,
                            total,
                            f"{rule.name} → {'发现问题' if f.violated else '通过'}",
                        )
                    except Exception:
                        pass
            return f

        results = await gather_limited(
            [run_one(r) for r in applicable], limit=settings.concurrency
        )

        findings: list[Finding] = []
        for r in results:
            if isinstance(r, Exception):
                report.errors.append(f"规则执行异常：{r}")
                continue
            findings.append(r)

        # 4) 排序：严重级别优先，其次置信度
        findings.sort(
            key=lambda f: (
                0 if f.violated else 1,
                SEVERITY_ORDER.get(f.severity, 9),
                -f.confidence,
            )
        )
        report.findings = findings

        # 5) 去重合并（同一原文被多条规则命中时合并）
        report.findings = self._dedupe(report.findings)

        report.elapsed_sec = time.perf_counter() - t0
        report.summary = self._summarize(report)
        return report

    # -- 单规则评估 --------------------------------------------------------
    async def _evaluate_rule(
        self, rule: Rule, doc_context: str, doc_title: str
    ) -> Finding:
        f = Finding(
            id=uuid.uuid4().hex[:10],
            rule_code=rule.code,
            rule_name=rule.name,
            category=rule.category,
            severity=rule.severity,
        )

        # RAG 检索法规依据
        query = f"{rule.name} {rule.legal_basis} {rule.check_prompt[:120]}"
        kb_context = ""
        try:
            kb_context, citations = self.kb.context_for(
                query, top_k=settings.retrieval_top_k, max_chars=3500
            )
            f.citations = citations
        except Exception as e:
            f.citations = []
            kb_context = ""

        prompt = build_rule_prompt(
            rule, doc_context, kb_context, doc_title=doc_title
        )

        try:
            data = await self.gateway.chat_json(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.05,
            )
        except LLMError as e:
            f.error = str(e)
            f.violated = False
            f.title = ""
            f.detail = f"该规则未能完成审查：{e}"
            f.needs_human_review = True
            f.confidence = 0.0
            return f
        except Exception as e:  # noqa: BLE001
            f.error = f"{type(e).__name__}: {e}"
            f.detail = f"该规则审查异常：{e}"
            f.needs_human_review = True
            return f

        if not isinstance(data, dict):
            f.error = "模型返回结构不是对象"
            f.needs_human_review = True
            return f

        f.violated = bool(data.get("violated"))
        f.title = str(data.get("title") or "").strip()
        f.detail = str(data.get("detail") or "").strip()
        f.location = str(data.get("location") or "").strip()
        f.original_text = str(data.get("original_text") or "").strip()
        f.legal_basis = str(data.get("legal_basis") or rule.legal_basis).strip()
        f.suggestion = str(data.get("suggestion") or "").strip()
        f.replacement = str(data.get("replacement") or "").strip()

        # 置信度
        try:
            f.confidence = float(data.get("confidence", 0.7))
        except (TypeError, ValueError):
            f.confidence = 0.7
        f.confidence = max(0.0, min(1.0, f.confidence))

        # 低置信度标记人工复核
        f.needs_human_review = f.confidence < 0.6

        # 违规但没给标题，兜底用规则名
        if f.violated and not f.title:
            f.title = rule.name
        if f.violated and not f.suggestion:
            f.suggestion = rule.suggestion_template or "建议按法规要求修改该条款。"

        # 原文摘录校验：模型有时会改写，若在文档里找不到就清空避免误导
        if f.original_text and f.original_text not in doc_context:
            # 尝试模糊匹配（去空白后比对）
            norm = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
            if norm(f.original_text) not in norm(doc_context):
                f.needs_human_review = True
                f.original_text = f.original_text  # 保留但标记复核

        return f

    # -- 文档上下文 --------------------------------------------------------
    @staticmethod
    def _build_doc_context(sections: dict[str, str], max_chars: int) -> str:
        """按章节拼装文档上下文，超长时优先保留审查相关章节。"""
        if not sections:
            return ""

        # 审查重点章节优先级
        priority = [
            "技术参数", "资格条件", "评分办法", "商务要求",
            "采购需求", "预算与资金", "付款方式", "履约验收",
            "售后服务", "交付要求", "中小企业政策", "进口产品",
            "节能环保", "培训与实施", "项目概况",
        ]

        def rank(name: str) -> int:
            for i, p in enumerate(priority):
                if p in name or name in p:
                    return i
            return len(priority)

        ordered = sorted(sections.items(), key=lambda kv: rank(kv[0]))

        parts: list[str] = []
        used = 0
        for name, text in ordered:
            piece = f"### 【{name}】\n{text}"
            if used + len(piece) > max_chars and parts:
                remain = max_chars - used
                if remain > 500:
                    parts.append(piece[:remain] + "\n…（该章节内容过长已截断）")
                break
            parts.append(piece)
            used += len(piece)

        full = "\n\n".join(parts)
        if len(full) > max_chars:
            full = full[:max_chars] + "\n…（文档过长，已截断）"
        return full

    # -- 去重 --------------------------------------------------------------
    @staticmethod
    def _dedupe(findings: list[Finding]) -> list[Finding]:
        """同一原文被多条规则命中时合并，保留最严重的一条并附注其他规则。"""
        groups: dict[str, list[Finding]] = {}
        passthrough: list[Finding] = []

        for f in findings:
            if not f.violated or not f.original_text or len(f.original_text) < 8:
                passthrough.append(f)
                continue
            key = re.sub(r"\s+", "", f.original_text)[:60]
            groups.setdefault(key, []).append(f)

        merged: list[Finding] = []
        for key, group in groups.items():
            if len(group) == 1:
                merged.append(group[0])
                continue
            # 保留最严重的
            group.sort(key=lambda x: (SEVERITY_ORDER.get(x.severity, 9), -x.confidence))
            head = group[0]
            others = [g.rule_name for g in group[1:]]
            if others:
                head.detail += f"\n\n（同一处内容还被以下规则命中：{'、'.join(others)}）"
            merged.append(head)

        merged.extend(passthrough)
        merged.sort(
            key=lambda f: (
                0 if f.violated else 1,
                SEVERITY_ORDER.get(f.severity, 9),
                -f.confidence,
            )
        )
        return merged

    # -- 结论汇总 ----------------------------------------------------------
    @staticmethod
    def _summarize(report: ReviewReport) -> dict[str, Any]:
        issues = report.issues
        by_sev: dict[str, int] = {}
        by_cat: dict[str, int] = {}
        for f in issues:
            by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
            by_cat[f.category] = by_cat.get(f.category, 0) + 1

        blockers = by_sev.get("blocker", 0)
        highs = by_sev.get("high", 0)

        # 总体判定
        if blockers > 0:
            verdict = "不通过"
            verdict_level = "fail"
            verdict_text = (
                f"发现 {blockers} 项严重违规问题，该采购需求文件**不得直接发布**，"
                f"必须整改后重新审查。"
            )
        elif highs > 0:
            verdict = "需整改"
            verdict_level = "warn"
            verdict_text = (
                f"发现 {highs} 项高风险问题，建议整改后再进入采购程序。"
            )
        elif issues:
            verdict = "基本合格（有改进项）"
            verdict_level = "pass_with_notes"
            verdict_text = (
                f"未发现严重违规，但有 {len(issues)} 项建议改进内容，可酌情采纳。"
            )
        else:
            verdict = "通过"
            verdict_level = "pass"
            verdict_text = "未发现合规性问题，该采购需求文件符合现行法规要求。"

        needs_review = sum(1 for f in issues if f.needs_human_review)

        return {
            "verdict": verdict,
            "verdict_level": verdict_level,
            "verdict_text": verdict_text,
            "issue_count": len(issues),
            "blocker_count": blockers,
            "high_count": highs,
            "medium_count": by_sev.get("medium", 0),
            "low_count": by_sev.get("low", 0),
            "pass_count": sum(1 for f in report.findings if not f.violated),
            "by_category": {CATEGORY_LABEL.get(k, k): v for k, v in by_cat.items()},
            "needs_human_review_count": needs_review,
            "error_count": len(report.errors),
        }


# ---------------------------------------------------------------------------
# 便捷入口
# ---------------------------------------------------------------------------

async def run_review(
    doc: ParsedDocument,
    gateway: LLMGateway,
    *,
    only_rules: list[str] | None = None,
    on_progress: ProgressFn | None = None,
) -> ReviewReport:
    engine = ReviewEngine(gateway)
    return await engine.review(doc, only_rules=only_rules, on_progress=on_progress)
