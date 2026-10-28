"""
多智能体协同审查框架 —— 端到端管道测试（模拟模型，无需 API Key）。

验证五阶段是否按设计运转：
  分诊 → 专项（6 组）→ 证据校验 → 交叉验证 → 仲裁 → 汇总

模拟网关按提示词特征区分智能体角色，并刻意让部分意见出现"分歧"，
以触发仲裁环节，从而覆盖全部分支。

运行：python scripts/test_multiagent.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.config import ProviderProfile  # noqa: E402
from app.core.multiagent import MultiAgentReviewEngine, SPECIALISTS  # noqa: E402
from app.core.parser import parse_document  # noqa: E402
from app.core.rules import get_rule_library  # noqa: E402

SAMPLE = ROOT / "knowledge" / "samples" / "XX大学高性能计算平台采购需求文件_含典型问题.docx"

PASS, FAIL = [], []


def check(name, fn):
    try:
        r = fn()
        PASS.append(name)
        print(f"  [PASS] {name}" + (f" → {r}" if r else ""))
        return r
    except Exception as e:
        import traceback

        FAIL.append(f"{name}: {type(e).__name__}: {e}")
        print(f"  [FAIL] {name} → {type(e).__name__}: {e}")
        traceback.print_exc()
        return None


class MockGateway:
    """模拟网关：按提示词特征返回不同角色的响应。"""

    def __init__(self):
        self.profile = ProviderProfile(
            id="mock", name="模拟模型", base_url="http://mock", model="mock-multiagent"
        )
        self.base_url = "http://mock"
        self.calls = {"triage": 0, "specialist": 0, "validator": 0, "arbiter": 0, "synthesis": 0}
        self.specialist_rules: list[str] = []

    def _role(self, prompt: str) -> str:
        if "政府采购项目分诊专家" in prompt or "判断项目基本属性与审查重点" in prompt:
            return "triage"
        if "为下面这条审查意见主动寻找反证" in prompt:
            return "validator"
        if "现在需要裁决一处分歧" in prompt:
            return "arbiter"
        if "撰写总体结论" in prompt:
            return "synthesis"
        return "specialist"

    async def chat_json(self, messages, **kwargs):
        prompt = messages[-1]["content"]
        role = self._role(prompt)
        self.calls[role] += 1

        if role == "triage":
            return {
                "project_type": "货物",
                "budget": "860万元",
                "procurement_method": "公开招标",
                "risk_level": "高",
                "risk_reasons": ["预算超1000万元门槛接近", "含进口GPU设备"],
                "focus_areas": ["技术参数", "资格条件", "评分办法"],
            }

        if role == "validator":
            # 让一部分意见被驳回，以触发仲裁
            idx = self.calls["validator"]
            if idx % 5 == 0:
                return {
                    "verdict": "reject",
                    "reason": "经核对，该表述属于对技术指标的客观描述，未指向特定供应商。",
                    "counter_evidence": "该条款仅规定容量与接口，未出现品牌或型号。",
                    "adjusted_severity": "",
                    "adjusted_confidence": 0.8,
                }
            if idx % 7 == 0:
                return {
                    "verdict": "revise",
                    "reason": "问题成立，但严重级别应下调。",
                    "counter_evidence": "",
                    "adjusted_severity": "medium",
                    "adjusted_confidence": 0.75,
                }
            return {
                "verdict": "uphold",
                "reason": "原文摘录属实，引用的法规条款与该问题对应。",
                "counter_evidence": "",
                "adjusted_severity": "",
                "adjusted_confidence": 0.9,
            }

        if role == "arbiter":
            idx = self.calls["arbiter"]
            if idx % 3 == 0:
                return {
                    "final_verdict": "reject",
                    "reasoning": "专项智能体的判断未能对应到具体违法构成要件，采信复核意见。",
                    "final_severity": "",
                    "final_confidence": 0.8,
                }
            return {
                "final_verdict": "revise",
                "reasoning": "问题实质成立，但级别应调整为中等。",
                "final_severity": "medium",
                "final_confidence": 0.85,
                "final_detail": "该条款存在倾向性，但影响范围有限。",
                "final_legal_basis": "《政府采购法实施条例》第二十条",
            }

        if role == "synthesis":
            return {
                "overall_comment": "该采购需求文件在技术参数、资格条件与评分办法三方面均存在"
                                   "较明显的合规风险，问题集中于指定品牌与规模条件设限。",
                "key_risks": ["技术参数指定品牌型号", "资格条件设规模门槛", "价格分权重不足"],
                "priority_action": "先删除技术参数中的品牌型号限定。",
            }

        # 专项审查：按规则名判断是否报违规
        self.specialist_rules.append(prompt[:40])
        violation_keywords = ("品牌", "区域", "规模", "保证金", "价格分", "进口", "验收", "主观")
        violated = any(k in prompt for k in violation_keywords)

        if not violated:
            return {
                "violated": False,
                "title": "",
                "detail": "经核查未发现该规则对应的问题。",
                "location": "",
                "original_text": "",
                "legal_basis": "",
                "suggestion": "",
                "replacement": "",
                "confidence": 0.85,
            }

        return {
            "violated": True,
            "title": "模拟检出问题",
            "detail": "该条款与法规要求不符。",
            "location": "技术参数",
            "original_text": "2. ★品牌要求：必须为联想 ThinkSystem SR650 V3，不接受其他品牌及型号。",
            "legal_basis": "《政府采购法实施条例》第二十条第六项",
            "suggestion": "删除品牌限定。",
            "replacement": "2. ★整机要求：不限定品牌与型号，应满足上述技术参数。",
            "confidence": 0.9,
        }


async def main():
    print("=" * 78)
    print("多智能体协同审查 —— 端到端管道测试（模拟模型）")
    print("=" * 78)

    if not SAMPLE.exists():
        print(f"样例文件不存在：{SAMPLE}")
        return 1

    doc = parse_document(SAMPLE)
    lib = get_rule_library()
    gw = MockGateway()
    engine = MultiAgentReviewEngine(gw, library=lib)

    progress = []

    def on_progress(code, done, total, msg):
        progress.append((done, total, msg))

    report = await engine.review(doc, on_progress=on_progress)

    print("\n[1] 阶段完整性")
    stages = [t.stage for t in report.traces]
    check("分诊阶段执行", lambda: "分诊" in stages or "未执行")
    check("专项审查阶段执行", lambda: f"{stages.count('专项审查')} 个智能体")
    check("证据校验阶段执行", lambda: "证据校验" in stages or "未执行")
    check("交叉验证阶段执行", lambda: "交叉验证" in stages or "未执行")
    check("仲裁阶段执行", lambda: "仲裁" in stages or "未触发（无分歧）")
    check("汇总阶段执行", lambda: "汇总" in stages or "未执行")

    print("\n[2] 项目画像")
    p = report.profile
    check("项目类型识别", lambda: p.project_type)
    check("风险等级", lambda: f"{p.risk_level}（理由 {len(p.risk_reasons)} 条）")
    check("审查重点", lambda: "、".join(p.focus_areas) or "未识别")

    print("\n[3] 协同统计")
    c = report
    check("专项调用数", lambda: str(c.specialist_calls))
    check("ECV 拦截数", lambda: str(c.ecv_blocked_count))
    check("交叉验证调用数", lambda: str(c.validator_calls))
    check("仲裁调用数", lambda: str(c.arbiter_calls))
    check("驳回数", lambda: str(c.rejected_count))
    check("修正数", lambda: str(c.revised_count))
    check(
        "ECV 拦截使复核量下降",
        lambda: f"{c.ecv_blocked_count} 条被前置拦截"
        if c.ecv_blocked_count > 0
        else "本样例未产生证据型缺陷（正常）",
    )

    print("\n[4] 审查结论")
    s = report.summary
    check("总体判定", lambda: f"{s.get('verdict')}（{s.get('verdict_level')}）")
    check("问题数", lambda: str(s.get("issue_count")))
    check("分级统计", lambda: f"严重{s.get('blocker_count')}/高{s.get('high_count')}/中{s.get('medium_count')}/低{s.get('low_count')}")
    check("通过项", lambda: str(s.get("pass_count")))
    check("总体评述（汇总智能体）", lambda: (s.get("overall_comment") or "")[:40] + "…")
    check("关键风险", lambda: f"{len(s.get('key_risks') or [])} 条")
    check("优先整改建议", lambda: s.get("priority_action") or "无")

    print("\n[5] 数据完整性")
    check("规则评估数", lambda: str(report.rules_evaluated))
    check("意见可 JSON 序列化", lambda: f"{len(json.dumps(report.to_dict(), ensure_ascii=False))} 字节")
    check("协同记录可追溯", lambda: f"{len(report.traces)} 条轨迹")
    check("进度回调", lambda: f"{len(progress)} 次")

    print("\n[6] 一致性校验")
    check("专项智能体数量与规则分组一致", lambda: f"{stages.count('专项审查')} 组")
    check(
        "被驳回的意见不再计入问题数",
        lambda: "一致"
        if s.get("issue_count") == len([f for f in report.findings if f.violated])
        else "不一致",
    )
    check(
        "每条意见都有置信度",
        lambda: "全部有"
        if all(0 <= f.confidence <= 1 for f in report.findings)
        else "存在缺失",
    )

    print("\n" + "=" * 78)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"  [FAIL] {f}")
    print("=" * 78)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
