"""
多智能体协同审查框架（Multi-Agent Collaborative Review, MACR）。

## 为什么需要多智能体

单一大模型审查采购需求文件存在三类结构性缺陷：

1. **规则覆盖不全** —— 一次性喂入全部规则与全文，模型注意力被稀释，
   位于文档中后部的规则容易被"跳过"。
2. **幻觉与误报** —— 模型倾向于"找出问题"，把合规表述误判为违规，
   在采购场景中误报代价很高（会误导起草人修改本来正确的条款）。
3. **结论不可溯源** —— 模型给出的法规依据可能与原文摘录不匹配，
   缺乏内部一致性校验。

## 五类智能体与协同机制

```
                    ┌──────────────────┐
   采购需求文件 ──▶ │ ① 分诊智能体      │  项目画像 + 规则路由
                    └────────┬─────────┘
                             │ 按业务域切分
        ┌────────┬────────┬──┴─────┬────────┬────────┐
        ▼        ▼        ▼        ▼        ▼        ▼
      ② 合法性  ② 技术   ② 商务   ② 评分   ② 政策   ② 完整性
        └────────┴────────┴──┬─────┴────────┴────────┘
                             │ 候选意见
                    ┌────────▼─────────┐
                    │ ③ 交叉验证智能体  │  逐条证伪（找反证）
                    └────────┬─────────┘
                             │ 支持/驳回/修正
                    ┌────────▼─────────┐
                    │ ④ 仲裁智能体      │  仅处理分歧
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │ ⑤ 汇总智能体      │  去重、定级、结论
                    └────────┬─────────┘
                             ▼
                        审查报告
```

三种协同机制：

- **认知分工（division of cognitive labor）**：按业务域拆给专项智能体，
  每个智能体只面对自己领域的规则与章节，注意力不被稀释。
- **对抗式复核（adversarial verification）**：交叉验证智能体的任务不是"再查一遍"，
  而是**主动为候选意见寻找反证**，找不到反证才维持原判。这是抑制误报的核心。
- **分歧仲裁（disagreement arbitration）**：专项智能体与验证智能体结论冲突时，
  由仲裁智能体在给定证据下作出终局判断，并说明采信哪一方的理由。

## 与单智能体模式的关系

本模块不替代 `reviewer.ReviewEngine`，而是提供可切换的第二条路径。
两种模式产出同构的 `ReviewReport`，下游（勾选、自动修订、导出）无需改动。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import settings
from .knowledge import KnowledgeBase, get_kb
from .llm import LLMGateway, LLMError, gather_limited
from .parser import ParsedDocument
from .reviewer import (
    CATEGORY_LABEL,
    SEVERITY_LABEL,
    SEVERITY_ORDER,
    Finding,
    ReviewEngine,
    ReviewReport,
)
from .rules import Rule, RuleLibrary, get_rule_library

ProgressFn = Callable[[str, int, int, str], None]


# ---------------------------------------------------------------------------
# 专项智能体编组
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SpecialistSpec:
    """一个专项审查智能体的定义。"""

    key: str
    name: str
    categories: tuple[str, ...]      # 负责的规则类别
    sections: tuple[str, ...]        # 关注的文档章节
    persona: str                     # 角色设定，决定审查视角
    focus_hint: str                  # 该域最易出错之处，用于强化提示


SPECIALISTS: tuple[SpecialistSpec, ...] = (
    SpecialistSpec(
        key="legality",
        name="合法性审查智能体",
        categories=("legality",),
        sections=("资格条件", "采购需求", "项目概况"),
        persona="你是一名政府采购法律顾问，专长是识别以不合理条件限制或排斥潜在供应商的违法条款。",
        focus_hint=(
            "重点核查：地域/行业业绩限制、指定品牌商标专利、限定所有制形式或注册地、"
            "以注册资本等规模条件设门槛、要求厂家授权作为资格条件、"
            "违规设置备选库/名录库、对供应商采取差别待遇。"
        ),
    ),
    SpecialistSpec(
        key="technical",
        name="技术参数审查智能体",
        categories=("technical",),
        sections=("技术参数", "采购需求"),
        persona="你是一名技术规格评审专家，专长是识别技术参数中的倾向性与排他性设计。",
        focus_hint=(
            "重点核查：指定品牌型号、参数组合唯一指向某一供应商、"
            "非关键指标过度具体、以★▲条款设置过量实质性要求、"
            "指定特定专利或私有技术路线、不合理要求样品、未载明核心产品。"
        ),
    ),
    SpecialistSpec(
        key="commercial",
        name="商务条款审查智能体",
        categories=("commercial",),
        sections=("商务要求",),
        persona="你是一名合同与商务条款审核专家，专长是识别权利义务失衡与不当成本转嫁。",
        focus_hint=(
            "重点核查：投标/履约保证金超法定上限、限定保证金形式、"
            "付款比例与进度不合理、付款期限过长、质保期畸长、"
            "违约责任不对等、设定最低限价、无理由拒绝联合体。"
        ),
    ),
    SpecialistSpec(
        key="scoring",
        name="评分办法审查智能体",
        categories=("scoring",),
        sections=("评分办法",),
        persona="你是一名评标办法设计专家，专长是识别评审因素的主观化、倾向性与不可量化。",
        focus_hint=(
            "重点核查：评分标准未量化到等次、主观分占比过高、"
            "评审因素与采购标的质量无关、价格分权重不合规、"
            "价格分未采用低价优先法、资格条件重复充作评分项、"
            "以特定区域或行业奖项加分、评标标准未事先载明。"
        ),
    ),
    SpecialistSpec(
        key="policy",
        name="政策与预算审查智能体",
        categories=("policy", "budget"),
        sections=("采购需求", "项目概况", "预算与资金", "技术参数"),
        persona="你是一名政府采购政策研究员，专长是核查政策功能落实与预算合规性。",
        focus_hint=(
            "重点核查：中小企业预留份额与价格扣除、节能产品与环境标志产品、"
            "进口产品论证与核准、采购本国产品原则、支持创新政策、"
            "预算金额是否明确、是否超标准配置、是否化整为零规避公开招标。"
        ),
    ),
    SpecialistSpec(
        key="completeness",
        name="需求完整性审查智能体",
        categories=("completeness", "procedure"),
        sections=("项目概况", "采购需求", "商务要求", "技术参数"),
        persona="你是一名采购需求编制规范专家，专长是识别需求要素缺失与程序瑕疵。",
        focus_hint=(
            "重点核查：采购标的、数量、技术规格、执行标准、交付时间地点、"
            "付款方式、售后服务、培训、履约验收方案等要素是否齐备；"
            "采购方式选择、公开招标数额标准、集中采购目录、意向公开时限、"
            "公告期限、需求调查与重点审查程序是否合规。"
        ),
    ),
)

SPECIALIST_BY_KEY = {s.key: s for s in SPECIALISTS}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class ProjectProfile:
    """分诊智能体产出的项目画像。"""

    project_type: str = "未知"          # 货物 / 服务 / 工程
    budget: str = ""                    # 预算金额描述
    procurement_method: str = ""        # 采购方式
    risk_level: str = "中"              # 高 / 中 / 低
    risk_reasons: list[str] = field(default_factory=list)
    focus_areas: list[str] = field(default_factory=list)
    activated_specialists: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_type": self.project_type,
            "budget": self.budget,
            "procurement_method": self.procurement_method,
            "risk_level": self.risk_level,
            "risk_reasons": self.risk_reasons,
            "focus_areas": self.focus_areas,
            "activated_specialists": self.activated_specialists,
        }


@dataclass
class AgentTrace:
    """单个智能体的执行记录，用于可观测性与各环节开销分析。"""

    agent: str
    stage: str
    calls: int = 0
    prompt_chars: int = 0
    elapsed: float = 0.0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "stage": self.stage,
            "calls": self.calls,
            "prompt_chars": self.prompt_chars,
            "elapsed": round(self.elapsed, 2),
            "note": self.note,
        }


@dataclass
class ValidationVerdict:
    """交叉验证结论。"""

    finding_id: str
    verdict: str                 # uphold（维持） / reject（驳回） / revise（修正）
    reason: str = ""
    adjusted_confidence: float = 0.0
    adjusted_severity: str = ""
    counter_evidence: str = ""   # 找到的反证

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "verdict": self.verdict,
            "reason": self.reason,
            "adjusted_confidence": self.adjusted_confidence,
            "adjusted_severity": self.adjusted_severity,
            "counter_evidence": self.counter_evidence,
        }


@dataclass
class MultiAgentReport(ReviewReport):
    """多智能体审查报告：在标准报告基础上附带协同过程记录。"""

    profile: ProjectProfile = field(default_factory=ProjectProfile)
    traces: list[AgentTrace] = field(default_factory=list)
    verdicts: list[ValidationVerdict] = field(default_factory=list)
    mode: str = "multi"

    # 协同过程统计
    specialist_calls: int = 0
    validator_calls: int = 0
    arbiter_calls: int = 0
    rejected_count: int = 0
    revised_count: int = 0
    ecv_blocked_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update(
            {
                "mode": self.mode,
                "profile": self.profile.to_dict(),
                "traces": [t.to_dict() for t in self.traces],
                "verdicts": [v.to_dict() for v in self.verdicts],
                "collaboration": {
                    "specialist_calls": self.specialist_calls,
                    "validator_calls": self.validator_calls,
                    "arbiter_calls": self.arbiter_calls,
                    "rejected_count": self.rejected_count,
                    "revised_count": self.revised_count,
                    "ecv_blocked_count": self.ecv_blocked_count,
                },
            }
        )
        return base


# ---------------------------------------------------------------------------
# 提示词
# ---------------------------------------------------------------------------

TRIAGE_PROMPT = """你是一名政府采购项目分诊专家。请阅读采购需求文件的概况信息，
判断项目基本属性与审查重点，为后续专项审查分配注意力。

## 文件概况
{overview}

## 输出格式（严格 JSON，不要 markdown 围栏）
{{
  "project_type": "货物" 或 "服务" 或 "工程" 或 "未知",
  "budget": "预算金额，如「860万元」，未载明则留空字符串",
  "procurement_method": "文件中体现的采购方式，未载明则留空字符串",
  "risk_level": "高" 或 "中" 或 "低",
  "risk_reasons": ["风险理由，1-3条，每条不超过30字"],
  "focus_areas": ["建议重点审查的章节名，从给定章节中选，2-4个"]
}}

风险等级判定参考：预算1000万元以上、技术复杂或定制开发、涉及进口产品、
社会关注度高、曾发生质疑投诉的项目，倾向判为「高」。"""


def build_specialist_prompt(
    spec: SpecialistSpec,
    rule: Rule,
    doc_context: str,
    kb_context: str,
    profile: ProjectProfile,
) -> str:
    """专项智能体的单规则审查提示词。"""
    basis = rule.legal_basis or "（以知识库检索到的法规为准）"
    kb_block = kb_context.strip() or "（知识库暂无相关条文，请依据法规知识判断并降低置信度）"

    return f"""## 你的角色
{spec.persona}
你的审查范围是「{CATEGORY_LABEL.get(rule.category, rule.category)}」这一业务域，
不必也不应越界评论其他业务域的问题。

## 项目画像（由分诊环节提供）
项目类型：{profile.project_type}　预算：{profile.budget or "未载明"}　
采购方式：{profile.procurement_method or "未载明"}　风险等级：{profile.risk_level}
审查重点：{"；".join(profile.focus_areas) or "未指定"}

## 本域审查要点提示
{spec.focus_hint}

## 待审查内容（已按本域关注的章节裁剪）
{doc_context}

## 知识库检索到的法规依据
{kb_block}

## 本次审查任务
规则名称：{rule.name}
规则类别：{CATEGORY_LABEL.get(rule.category, rule.category)}
严重级别：{SEVERITY_LABEL.get(rule.severity, rule.severity)}
法定依据：{basis}

审查要求：
{rule.check_prompt}

{f"典型违规表述参考（仅作判断参照，不要照搬）：{rule.violation_example}" if rule.violation_example else ""}

## 审查纪律
1. 只报有法规依据的问题；没有依据的猜测不要报。
2. 原文摘录必须从上面「待审查内容」中逐字复制，不得改写或概括；
   若属于「应当有但没有」的缺失类问题，原文摘录留空字符串。
3. 给出可直接套用的替换文本，不要停留在「应当完善」。
4. 把握不足时把 confidence 设在 0.6 以下，不要硬下结论。

## 输出格式（严格 JSON，不要 markdown 围栏）
{{
  "violated": true 或 false,
  "title": "问题一句话概括（20字以内，通过则留空）",
  "detail": "详细说明：哪里有问题、为什么违反规定、依据是什么",
  "location": "问题所在章节或位置",
  "original_text": "逐字复制的原文片段（缺失类问题留空字符串）",
  "legal_basis": "对应法规条款",
  "suggestion": "具体整改建议",
  "replacement": "建议直接替换原文的完整文本（无法给出则留空）",
  "confidence": 0.0 到 1.0 之间的数字
}}"""


VALIDATOR_PROMPT = """你是一名政府采购审查的复核专家。你的职责不是"再查一遍"，
而是**为下面这条审查意见主动寻找反证**，判断它是否站得住脚。

请特别注意以下三类常见误判：
1. **把合规表述误判为违规** —— 例如条款本身合法，只是措辞不够规范；
2. **把「未载明」误判为「违规」** —— 文件确实没写某事项，但该事项并非本项目必需；
3. **法规依据错配** —— 引用的条款与所指问题不对应，或条款已被修订。

## 待复核的审查意见
问题概括：{title}
问题说明：{detail}
所指位置：{location}
原文摘录：{original_text}
引用依据：{legal_basis}
原判严重级别：{severity}
原判置信度：{confidence}

## 采购需求文件全文（用于核对原文摘录是否属实）
{doc_context}

## 知识库检索到的法规依据
{kb_context}

## 判断标准
- 若原文摘录确实存在、且该表述确实违反所引法规 → verdict = "uphold"
- 若原文摘录不存在于文件、或该表述经核查并不违法、或依据明显错配 → verdict = "reject"
- 若问题成立但严重级别或表述有偏差（例如应降级、应换依据） → verdict = "revise"

## 输出格式（严格 JSON，不要 markdown 围栏）
{{
  "verdict": "uphold" 或 "reject" 或 "revise",
  "reason": "判断理由，须具体说明你核对到了什么",
  "counter_evidence": "你找到的反证原文片段；未找到反证则留空字符串",
  "adjusted_severity": "若 verdict 为 revise 或 reject，给出你认为恰当的级别（blocker/high/medium/low）；否则留空",
  "adjusted_confidence": 0.0 到 1.0，你对该意见成立与否的最终把握
}}"""


ARBITER_PROMPT = """你是一名资深政府采购合规专家，现在需要裁决一处分歧。

## 分歧情形
专项审查智能体认定该处存在违规，交叉验证智能体给出了「{validator_verdict}」的复核结论。

## 专项审查智能体的意见
问题概括：{title}
问题说明：{detail}
原文摘录：{original_text}
引用依据：{legal_basis}
原判级别：{severity}

## 交叉验证智能体的复核意见
复核结论：{validator_verdict}
复核理由：{validator_reason}
找到的反证：{counter_evidence}

## 待审查内容
{doc_context}

## 知识库检索到的法规依据
{kb_context}

## 裁决要求
1. 独立判断，不要因为"两方都有道理"就和稀泥。
2. 必须明确采信一方，并说明采信理由。
3. 若认为问题成立但需调整级别或依据，按 revise 处理。

## 输出格式（严格 JSON，不要 markdown 围栏）
{{
  "final_verdict": "uphold" 或 "reject" 或 "revise",
  "reasoning": "裁决理由，说明为何采信某一方",
  "final_severity": "blocker/high/medium/low",
  "final_confidence": 0.0 到 1.0,
  "final_detail": "修订后的问题说明（可沿用原说明或补充）",
  "final_legal_basis": "最终采信的法规依据"
}}"""


SYNTHESIS_PROMPT = """你是一名审查报告主笔。请基于以下审查结果，撰写总体结论。

## 项目画像
{profile}

## 审查统计
{stats}

## 已确认的问题清单
{issues}

## 输出格式（严格 JSON，不要 markdown 围栏）
{{
  "overall_comment": "对这份采购需求文件合规状况的总体评述，150-250字，
                      要具体指出问题集中在哪些方面，不要空泛表态",
  "key_risks": ["最需要优先整改的风险点，2-4条，每条不超过40字"],
  "priority_action": "如果只能先改一处，应该先改哪里，一句话"
}}"""


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------

class MultiAgentReviewEngine:
    """多智能体协同审查引擎。"""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        kb: KnowledgeBase | None = None,
        library: RuleLibrary | None = None,
        enable_triage: bool = True,
        enable_validation: bool = True,
        enable_arbitration: bool = True,
        enable_synthesis: bool = True,
        enable_evidence_check: bool = True,
    ):
        self.gateway = gateway
        self.kb = kb or get_kb()
        self.library = library or get_rule_library()
        self.enable_triage = enable_triage
        self.enable_validation = enable_validation
        self.enable_arbitration = enable_arbitration
        self.enable_synthesis = enable_synthesis
        self.enable_evidence_check = enable_evidence_check
        # 证据一致性校验器（懒加载，避免无谓开销）
        self._ecv = None
        # 复用单智能体引擎的工具方法，避免重复实现
        self._helper = ReviewEngine(gateway, kb=self.kb, library=self.library)

    def _get_ecv(self):
        """懒加载证据校验器。"""
        if self._ecv is None and self.enable_evidence_check:
            try:
                from .evidence import EvidenceVerifier

                self._ecv = EvidenceVerifier(self.kb)
            except Exception:
                self._ecv = False
        return self._ecv or None

    # -- 主流程 ------------------------------------------------------------
    async def review(
        self,
        doc: ParsedDocument,
        *,
        only_rules: list[str] | None = None,
        on_progress: ProgressFn | None = None,
    ) -> MultiAgentReport:
        t0 = time.perf_counter()
        sections = doc.sections()
        report = MultiAgentReport(
            review_id=uuid.uuid4().hex[:12],
            filename=doc.filename,
            created_at=time.time(),
            doc_meta={
                "filename": doc.filename,
                "filetype": doc.filetype,
                "char_count": len(doc.full_text),
                "sections": list(sections.keys()),
                "block_count": len(doc.blocks),
                **(doc.meta or {}),
            },
            model_info={
                "provider": self.gateway.profile.display(),
                "model": self.gateway.profile.model,
                "base_url": self.gateway.base_url,
            },
        )

        # ---------- 阶段一：分诊 ----------
        if self.enable_triage:
            report.profile = await self._triage(doc, sections, report)
        else:
            report.profile = self._default_profile(sections)

        # ---------- 阶段二：规则路由与专项审查 ----------
        applicable = self.library.prefilter(sections, only_codes=only_rules)
        if not applicable:
            report.errors.append("没有匹配到任何适用规则，请检查规则库配置")
            report.elapsed_sec = time.perf_counter() - t0
            report.summary = self._helper._summarize(report)  # noqa: SLF001
            return report

        # 按专项智能体分组
        groups: dict[str, list[Rule]] = {}
        for rule in applicable:
            spec_key = self._route(rule)
            groups.setdefault(spec_key, []).append(rule)

        # 只保留被分诊激活的智能体；若分诊未指定则全部激活
        active_keys = set(groups.keys())
        if report.profile.activated_specialists:
            allowed = set(report.profile.activated_specialists)
            # 但完整性域始终保留，避免漏审基础要素
            allowed.add("completeness")
            active_keys &= allowed
            if not active_keys:
                active_keys = set(groups.keys())

        total_rules = sum(len(groups[k]) for k in active_keys)
        report.rules_evaluated = total_rules

        done = 0
        lock = asyncio.Lock()

        async def run_specialist(spec_key: str) -> tuple[str, list[Finding], AgentTrace]:
            nonlocal done
            spec = SPECIALIST_BY_KEY[spec_key]
            rules = groups[spec_key]
            trace = AgentTrace(agent=spec.name, stage="专项审查")
            st = time.perf_counter()

            # 章节裁剪：只保留本域关注的章节
            scoped = self._scope_sections(sections, spec)
            doc_context = self._helper._build_doc_context(  # noqa: SLF001
                scoped, settings.max_chunk_chars
            )

            async def one(rule: Rule) -> Finding:
                nonlocal done
                f = await self._specialist_evaluate(spec, rule, doc_context, report.profile, doc.filename)
                async with lock:
                    done += 1
                    if on_progress:
                        try:
                            on_progress(
                                rule.code, done, total_rules,
                                f"{spec.name} · {rule.name} → {'发现问题' if f.violated else '通过'}",
                            )
                        except Exception:
                            pass
                return f

            results = await gather_limited(
                [one(r) for r in rules], limit=max(1, settings.concurrency // 2)
            )
            findings: list[Finding] = []
            for r in results:
                if isinstance(r, Exception):
                    trace.note += f"规则异常:{r};"
                    continue
                findings.append(r)

            trace.calls = len(rules)
            trace.prompt_chars = len(doc_context) * len(rules)
            trace.elapsed = time.perf_counter() - st
            return spec_key, findings, trace

        specialist_results = await asyncio.gather(
            *[run_specialist(k) for k in sorted(active_keys)]
        )

        candidates: list[Finding] = []
        for _key, findings, trace in specialist_results:
            candidates.extend(findings)
            report.traces.append(trace)
        report.specialist_calls = sum(t.calls for t in report.traces if t.stage == "专项审查")

        # ---------- 阶段三：证据校验 + 交叉验证 ----------
        violated = [f for f in candidates if f.violated]
        if self.enable_validation and violated:
            doc_context_full = self._helper._build_doc_context(  # noqa: SLF001
                sections, settings.max_chunk_chars
            )
            doc_text = doc.full_text

            # 3a. 证据一致性校验（ECV）—— 确定性、毫秒级，作为昂贵复核的前置过滤器
            ecv_blocked: set[str] = set()
            ecv = self._get_ecv()
            if ecv is not None:
                et = AgentTrace(agent="证据校验器(ECV)", stage="证据校验")
                st = time.perf_counter()
                for f in violated:
                    try:
                        chk = ecv.check(
                            finding_id=f.id,
                            category=f.category,
                            original_text=f.original_text,
                            legal_basis=f.legal_basis,
                            doc_text=doc_text,
                            is_missing_type=not (f.original_text or "").strip(),
                        )
                        if not chk.passed:
                            ecv_blocked.add(f.id)
                            f.violated = False
                            f._rejected = True  # type: ignore[attr-defined]
                            f.detail = (
                                f.detail
                                + "\n\n【证据校验拦截】" + "；".join(chk.reasons)
                            ).strip()
                    except Exception:
                        continue
                et.calls = len(violated)
                et.elapsed = time.perf_counter() - st
                et.note = f"拦截 {len(ecv_blocked)} 条，省去同等数量的大模型复核调用"
                report.traces.append(et)
                report.ecv_blocked_count = len(ecv_blocked)

            # 3b. 通过 ECV 的意见才进入大模型对抗式复核
            to_validate = [f for f in violated if f.id not in ecv_blocked]

            vt = AgentTrace(agent="交叉验证智能体", stage="交叉验证")
            st = time.perf_counter()
            verdicts = await gather_limited(
                [self._validate(f, doc_context_full) for f in to_validate],
                limit=settings.concurrency,
            )
            vt.calls = len(to_validate)
            vt.elapsed = time.perf_counter() - st
            vt.note = f"复核 {len(to_validate)} 条（已排除 ECV 拦截项）"
            report.traces.append(vt)
            report.validator_calls = len(to_validate)

            for f, v in zip(to_validate, verdicts):
                if isinstance(v, Exception):
                    report.errors.append(f"交叉验证异常：{v}")
                    continue
                report.verdicts.append(v)

            # ---------- 阶段四：仲裁（仅分歧） ----------
            disputes = [
                (f, v) for f, v in zip(to_validate, verdicts)
                if not isinstance(v, Exception) and v.verdict in ("reject", "revise")
            ]
            if self.enable_arbitration and disputes:
                at = AgentTrace(agent="仲裁智能体", stage="仲裁")
                st = time.perf_counter()
                arbitrated = await gather_limited(
                    [self._arbitrate(f, v, doc_context_full) for f, v in disputes],
                    limit=settings.concurrency,
                )
                at.calls = len(disputes)
                at.elapsed = time.perf_counter() - st
                report.traces.append(at)
                report.arbiter_calls = len(disputes)

                for (f, v), a in zip(disputes, arbitrated):
                    if isinstance(a, Exception):
                        report.errors.append(f"仲裁异常：{a}")
                        continue
                    self._apply_arbitration(f, v, a)

            # 应用非分歧的验证结论
            for f, v in zip(to_validate, verdicts):
                if isinstance(v, Exception):
                    continue
                if v.verdict not in ("reject", "revise"):
                    self._apply_validation(f, v)
                elif not disputes or not self.enable_arbitration:
                    self._apply_validation(f, v)

            report.rejected_count = sum(
                1 for f in candidates if getattr(f, "_rejected", False)
            )
            report.revised_count = sum(
                1 for f in candidates
                if getattr(f, "_revised", False) and not getattr(f, "_rejected", False)
            )

        # ---------- 阶段五：汇总 ----------
        final = self._helper._dedupe(candidates)  # noqa: SLF001
        final.sort(
            key=lambda f: (
                0 if f.violated else 1,
                SEVERITY_ORDER.get(f.severity, 9),
                -f.confidence,
            )
        )
        report.findings = final
        report.summary = self._helper._summarize(report)  # noqa: SLF001

        if self.enable_synthesis:
            await self._synthesize(report)

        report.elapsed_sec = time.perf_counter() - t0
        return report

    # -- 规则路由 ----------------------------------------------------------
    @staticmethod
    def _route(rule: Rule) -> str:
        """把规则分配给负责该业务域的专项智能体。"""
        for spec in SPECIALISTS:
            if rule.category in spec.categories:
                return spec.key
        return "completeness"

    # -- 章节裁剪 ----------------------------------------------------------
    @staticmethod
    def _scope_sections(
        sections: dict[str, str], spec: SpecialistSpec
    ) -> dict[str, str]:
        """
        只保留本域关注的章节。

        这是多智能体降低上下文开销的关键：每个智能体只看自己该看的部分，
        既提高注意力集中度，也显著减少 token 消耗。
        """
        scoped: dict[str, str] = {}
        for name, text in sections.items():
            for want in spec.sections:
                if want in name or name in want:
                    scoped[name] = text
                    break
        # 兜底：一个都没匹配上时给全文，避免漏审
        return scoped or sections

    # -- 分诊 --------------------------------------------------------------
    async def _triage(
        self, doc: ParsedDocument, sections: dict[str, str], report: MultiAgentReport
    ) -> ProjectProfile:
        trace = AgentTrace(agent="分诊智能体", stage="分诊")
        st = time.perf_counter()
        try:
            overview_parts = []
            for key in ("项目概况", "采购需求", "预算与资金"):
                if key in sections:
                    overview_parts.append(f"### {key}\n{sections[key][:1500]}")
            if not overview_parts:
                overview_parts.append(doc.full_text[:2500])
            overview = "\n\n".join(overview_parts)

            data = await self.gateway.chat_json(
                [
                    {"role": "system", "content": "你是政府采购项目分诊专家，输出严格 JSON。"},
                    {"role": "user", "content": TRIAGE_PROMPT.format(overview=overview)},
                ],
                temperature=0.1,
            )
            trace.calls = 1
            trace.prompt_chars = len(overview)

            if not isinstance(data, dict):
                raise ValueError("分诊返回结构异常")

            profile = ProjectProfile(
                project_type=str(data.get("project_type") or "未知"),
                budget=str(data.get("budget") or ""),
                procurement_method=str(data.get("procurement_method") or ""),
                risk_level=str(data.get("risk_level") or "中"),
                risk_reasons=[str(x) for x in (data.get("risk_reasons") or [])][:5],
                focus_areas=[str(x) for x in (data.get("focus_areas") or [])][:6],
            )
            # 分诊不决定激活哪些智能体（避免漏审），全部激活
            profile.activated_specialists = []
            trace.note = f"{profile.project_type}/风险{profile.risk_level}"
            return profile
        except Exception as e:  # noqa: BLE001
            trace.note = f"分诊失败，降级为默认画像：{e}"
            report.errors.append(f"分诊环节失败，已降级：{e}")
            return self._default_profile(sections)
        finally:
            trace.elapsed = time.perf_counter() - st
            report.traces.append(trace)

    @staticmethod
    def _default_profile(sections: dict[str, str]) -> ProjectProfile:
        return ProjectProfile(
            project_type="未知",
            risk_level="中",
            focus_areas=list(sections.keys())[:4],
            activated_specialists=[],
        )

    # -- 专项单规则评估 ----------------------------------------------------
    async def _specialist_evaluate(
        self,
        spec: SpecialistSpec,
        rule: Rule,
        doc_context: str,
        profile: ProjectProfile,
        doc_title: str,
    ) -> Finding:
        f = Finding(
            id=uuid.uuid4().hex[:10],
            rule_code=rule.code,
            rule_name=rule.name,
            category=rule.category,
            severity=rule.severity,
        )

        query = f"{rule.name} {rule.legal_basis} {rule.check_prompt[:120]}"
        kb_context = ""
        try:
            kb_context, cites = self.kb.context_for(
                query, top_k=settings.retrieval_top_k, max_chars=3000
            )
            f.citations = cites
        except Exception:
            pass

        prompt = build_specialist_prompt(spec, rule, doc_context, kb_context, profile)

        try:
            data = await self.gateway.chat_json(
                [
                    {"role": "system", "content": f"{spec.persona} 输出严格 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.05,
            )
        except LLMError as e:
            f.error = str(e)
            f.detail = f"该规则未能完成审查：{e}"
            f.needs_human_review = True
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

        try:
            f.confidence = float(data.get("confidence", 0.7))
        except (TypeError, ValueError):
            f.confidence = 0.7
        f.confidence = max(0.0, min(1.0, f.confidence))
        f.needs_human_review = f.confidence < 0.6

        if f.violated and not f.title:
            f.title = rule.name
        if f.violated and not f.suggestion:
            f.suggestion = rule.suggestion_template or "建议按法规要求修改该条款。"

        # 原文摘录真实性校验：摘录必须能在上下文中找到
        if f.original_text:
            import re as _re

            norm = lambda s: _re.sub(r"\s+", "", s)  # noqa: E731
            if f.original_text not in doc_context and norm(f.original_text) not in norm(doc_context):
                f.needs_human_review = True

        return f

    # -- 交叉验证 ----------------------------------------------------------
    async def _validate(self, f: Finding, doc_context: str) -> ValidationVerdict:
        query = f"{f.rule_name} {f.legal_basis}"
        try:
            kb_context, _ = self.kb.context_for(query, top_k=4, max_chars=2000)
        except Exception:
            kb_context = ""

        prompt = VALIDATOR_PROMPT.format(
            title=f.title or f.rule_name,
            detail=f.detail,
            location=f.location,
            original_text=f.original_text or "（缺失类问题，无原文摘录）",
            legal_basis=f.legal_basis,
            severity=SEVERITY_LABEL.get(f.severity, f.severity),
            confidence=f"{f.confidence:.2f}",
            doc_context=doc_context,
            kb_context=kb_context or "（无）",
        )

        try:
            data = await self.gateway.chat_json(
                [
                    {"role": "system", "content": "你是审查复核专家，职责是寻找反证。输出严格 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.05,
            )
        except Exception as e:  # noqa: BLE001
            # 复核失败时保守处理：维持原判，但标记人工复核
            return ValidationVerdict(
                finding_id=f.id,
                verdict="uphold",
                reason=f"复核环节调用失败，保守维持原判：{e}",
                adjusted_confidence=f.confidence,
            )

        if not isinstance(data, dict):
            return ValidationVerdict(
                finding_id=f.id, verdict="uphold",
                reason="复核返回结构异常，保守维持原判",
                adjusted_confidence=f.confidence,
            )

        verdict = str(data.get("verdict") or "uphold").lower()
        if verdict not in ("uphold", "reject", "revise"):
            verdict = "uphold"

        try:
            conf = float(data.get("adjusted_confidence", f.confidence))
        except (TypeError, ValueError):
            conf = f.confidence

        sev = str(data.get("adjusted_severity") or "").strip().lower()
        if sev not in SEVERITY_ORDER:
            sev = ""

        return ValidationVerdict(
            finding_id=f.id,
            verdict=verdict,
            reason=str(data.get("reason") or "").strip(),
            adjusted_confidence=max(0.0, min(1.0, conf)),
            adjusted_severity=sev,
            counter_evidence=str(data.get("counter_evidence") or "").strip(),
        )

    @staticmethod
    def _apply_validation(f: Finding, v: ValidationVerdict) -> None:
        """把复核结论落到意见上。"""
        if v.verdict == "reject":
            f.violated = False
            f._rejected = True  # type: ignore[attr-defined]
            f.detail = (f.detail + f"\n\n【交叉验证驳回】{v.reason}").strip()
            return
        if v.verdict == "revise":
            f._revised = True  # type: ignore[attr-defined]
            if v.adjusted_severity:
                f.severity = v.adjusted_severity
            f.confidence = v.adjusted_confidence
            f.detail = (f.detail + f"\n\n【交叉验证修正】{v.reason}").strip()
            return
        # uphold
        f.confidence = v.adjusted_confidence or f.confidence
        if v.reason:
            f.detail = (f.detail + f"\n\n【交叉验证】{v.reason}").strip()

    # -- 仲裁 --------------------------------------------------------------
    async def _arbitrate(
        self, f: Finding, v: ValidationVerdict, doc_context: str
    ) -> dict[str, Any]:
        query = f"{f.rule_name} {f.legal_basis}"
        try:
            kb_context, _ = self.kb.context_for(query, top_k=4, max_chars=2000)
        except Exception:
            kb_context = ""

        prompt = ARBITER_PROMPT.format(
            validator_verdict=v.verdict,
            title=f.title or f.rule_name,
            detail=f.detail,
            original_text=f.original_text or "（缺失类问题）",
            legal_basis=f.legal_basis,
            severity=SEVERITY_LABEL.get(f.severity, f.severity),
            validator_reason=v.reason,
            counter_evidence=v.counter_evidence or "（未找到反证）",
            doc_context=doc_context,
            kb_context=kb_context or "（无）",
        )

        try:
            data = await self.gateway.chat_json(
                [
                    {"role": "system", "content": "你是资深政府采购合规专家，负责裁决分歧。输出严格 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.05,
            )
        except Exception as e:  # noqa: BLE001
            # 仲裁失败：采信复核意见（对抗式复核的保守取向）
            return {"final_verdict": v.verdict, "reasoning": f"仲裁调用失败，采信复核意见：{e}"}

        if not isinstance(data, dict):
            return {"final_verdict": v.verdict, "reasoning": "仲裁返回异常，采信复核意见"}
        return data

    @staticmethod
    def _apply_arbitration(f: Finding, v: ValidationVerdict, a: dict[str, Any]) -> None:
        final = str(a.get("final_verdict") or v.verdict).lower()
        reasoning = str(a.get("reasoning") or "").strip()

        if final == "reject":
            f.violated = False
            f._rejected = True  # type: ignore[attr-defined]
            f.detail = (f.detail + f"\n\n【仲裁驳回】{reasoning}").strip()
            return

        if final == "revise":
            f._revised = True  # type: ignore[attr-defined]
            sev = str(a.get("final_severity") or "").strip().lower()
            if sev in SEVERITY_ORDER:
                f.severity = sev
            try:
                f.confidence = max(0.0, min(1.0, float(a.get("final_confidence", f.confidence))))
            except (TypeError, ValueError):
                pass
            if a.get("final_detail"):
                f.detail = str(a["final_detail"])
            if a.get("final_legal_basis"):
                f.legal_basis = str(a["final_legal_basis"])
            f.detail = (f.detail + f"\n\n【仲裁修正】{reasoning}").strip()
            return

        # uphold
        f.confidence = max(f.confidence, v.adjusted_confidence)
        f.detail = (f.detail + f"\n\n【仲裁维持】{reasoning}").strip()

    # -- 汇总叙述 ----------------------------------------------------------
    async def _synthesize(self, report: MultiAgentReport) -> None:
        issues = report.issues
        if not issues:
            report.summary["overall_comment"] = (
                "本次审查未发现合规性问题，采购需求文件在现有规则库覆盖范围内符合法规要求。"
            )
            report.summary["key_risks"] = []
            report.summary["priority_action"] = "无需整改。"
            return

        trace = AgentTrace(agent="汇总智能体", stage="汇总")
        st = time.perf_counter()
        try:
            issue_lines = []
            for i, f in enumerate(issues[:20], 1):
                issue_lines.append(
                    f"{i}. [{SEVERITY_LABEL.get(f.severity, f.severity)}] "
                    f"{f.title or f.rule_name}（{CATEGORY_LABEL.get(f.category, f.category)}）"
                    f"：{f.detail[:120]}"
                )
            stats = (
                f"共审查 {report.rules_evaluated} 条规则，发现问题 {len(issues)} 项；"
                f"严重 {report.summary.get('blocker_count', 0)} 项、"
                f"高 {report.summary.get('high_count', 0)} 项、"
                f"中 {report.summary.get('medium_count', 0)} 项、"
                f"低 {report.summary.get('low_count', 0)} 项。"
            )
            profile_desc = (
                f"项目类型 {report.profile.project_type}，"
                f"预算 {report.profile.budget or '未载明'}，"
                f"风险等级 {report.profile.risk_level}"
            )

            data = await self.gateway.chat_json(
                [
                    {"role": "system", "content": "你是审查报告主笔，输出严格 JSON。"},
                    {
                        "role": "user",
                        "content": SYNTHESIS_PROMPT.format(
                            profile=profile_desc,
                            stats=stats,
                            issues="\n".join(issue_lines),
                        ),
                    },
                ],
                temperature=0.3,
            )
            trace.calls = 1
            if isinstance(data, dict):
                report.summary["overall_comment"] = str(data.get("overall_comment") or "")
                report.summary["key_risks"] = [
                    str(x) for x in (data.get("key_risks") or [])
                ][:4]
                report.summary["priority_action"] = str(data.get("priority_action") or "")
        except Exception as e:  # noqa: BLE001
            trace.note = f"汇总失败：{e}"
            report.errors.append(f"汇总环节失败（不影响问题清单）：{e}")
        finally:
            trace.elapsed = time.perf_counter() - st
            report.traces.append(trace)


# ---------------------------------------------------------------------------
# 便捷入口
# ---------------------------------------------------------------------------

async def run_multi_agent_review(
    doc: ParsedDocument,
    gateway: LLMGateway,
    *,
    only_rules: list[str] | None = None,
    on_progress: ProgressFn | None = None,
) -> MultiAgentReport:
    engine = MultiAgentReviewEngine(gateway)
    return await engine.review(doc, only_rules=only_rules, on_progress=on_progress)
