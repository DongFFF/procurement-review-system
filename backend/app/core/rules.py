"""
审查规则库加载与粗筛。

规则以 YAML 存放于 knowledge/rules/，便于非程序员维护和版本管理。
支持：
  - 多文件合并（可按业务域拆分 rule pack）
  - 热重载（改完 YAML 不必重启程序）
  - 关键词粗筛（先排除明显不适用的规则，省 token 提速）
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import KNOWLEDGE_ROOT

RULES_DIR = KNOWLEDGE_ROOT / "rules"
REGULATIONS_DIR = KNOWLEDGE_ROOT / "regulations"

# 严重级别排序，用于结论汇总
SEVERITY_ORDER = {"blocker": 0, "high": 1, "medium": 2, "low": 3}

SEVERITY_LABEL = {
    "blocker": "严重（必须整改）",
    "high": "高",
    "medium": "中",
    "low": "低",
}

CATEGORY_LABEL = {
    "legality": "合法合规性",
    "completeness": "需求完整性",
    "technical": "技术参数倾向性",
    "commercial": "商务条款合理性",
    "scoring": "评分办法",
    "policy": "政策功能",
    "budget": "预算与资金",
    "procedure": "程序与时限",
}


@dataclass
class Rule:
    """一条审查规则。"""

    code: str
    name: str
    category: str = "completeness"
    severity: str = "medium"
    applies_to: list[str] = field(default_factory=list)
    legal_basis: str = ""
    check_prompt: str = ""
    violation_example: str = ""
    suggestion_template: str = ""
    keywords: list[str] = field(default_factory=list)
    enabled: bool = True
    source_file: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "category": self.category,
            "category_label": CATEGORY_LABEL.get(self.category, self.category),
            "severity": self.severity,
            "severity_label": SEVERITY_LABEL.get(self.severity, self.severity),
            "applies_to": self.applies_to,
            "legal_basis": self.legal_basis,
            "violation_example": self.violation_example,
            "suggestion_template": self.suggestion_template,
            "keywords": self.keywords,
            "enabled": self.enabled,
            "source_file": self.source_file,
        }


class RuleLibrary:
    """规则库。"""

    def __init__(self) -> None:
        self.rules: list[Rule] = []
        self.loaded_at: float = 0.0
        self.errors: list[str] = []

    # -- 加载 --------------------------------------------------------------
    def load(self) -> dict[str, Any]:
        RULES_DIR.mkdir(parents=True, exist_ok=True)
        rules: list[Rule] = []
        errors: list[str] = []
        seen: set[str] = set()

        files = sorted(RULES_DIR.glob("*.y*ml"))
        if not files:
            self.errors = ["未找到任何规则文件（knowledge/rules/*.yaml）"]
            self.rules = []
            return self.stats()

        for f in files:
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8"))
            except Exception as e:
                errors.append(f"{f.name}: YAML 解析失败 - {e}")
                continue

            items: list[dict[str, Any]] = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = data.get("rules") or []

            for i, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code") or "").strip()
                if not code:
                    errors.append(f"{f.name}: 第 {i+1} 条规则缺少 code，已跳过")
                    continue
                if code in seen:
                    errors.append(f"{f.name}: 规则代码重复 {code}，已跳过")
                    continue
                seen.add(code)

                applies = item.get("applies_to") or ["全部"]
                if isinstance(applies, str):
                    applies = [applies]

                rules.append(
                    Rule(
                        code=code,
                        name=str(item.get("name") or code),
                        category=str(item.get("category") or "completeness"),
                        severity=str(item.get("severity") or "medium"),
                        applies_to=[str(a) for a in applies],
                        legal_basis=str(item.get("legal_basis") or ""),
                        check_prompt=str(item.get("check_prompt") or ""),
                        violation_example=str(item.get("violation_example") or ""),
                        suggestion_template=str(item.get("suggestion_template") or ""),
                        keywords=[str(k) for k in (item.get("keywords") or [])],
                        enabled=bool(item.get("enabled", True)),
                        source_file=f.name,
                    )
                )

        # 按严重级别 + 类别排序，输出更有序
        rules.sort(
            key=lambda r: (
                SEVERITY_ORDER.get(r.severity, 9),
                r.category,
                r.code,
            )
        )
        self.rules = rules
        self.errors = errors
        self.loaded_at = time.time()
        return self.stats()

    def stats(self) -> dict[str, Any]:
        cats: dict[str, int] = {}
        sevs: dict[str, int] = {}
        for r in self.rules:
            cats[r.category] = cats.get(r.category, 0) + 1
            sevs[r.severity] = sevs.get(r.severity, 0) + 1
        return {
            "rule_count": len(self.rules),
            "enabled_count": sum(1 for r in self.rules if r.enabled),
            "by_category": {CATEGORY_LABEL.get(k, k): v for k, v in cats.items()},
            "by_severity": {SEVERITY_LABEL.get(k, k): v for k, v in sevs.items()},
            "loaded_at": self.loaded_at,
            "errors": self.errors,
        }

    # -- 粗筛 --------------------------------------------------------------
    def prefilter(
        self,
        doc_sections: dict[str, str],
        *,
        only_codes: list[str] | None = None,
    ) -> list[Rule]:
        """
        挑选适用于本文档的规则。

        策略（保守，宁可多审不可漏审）：
          1. 规则 applies_to 命中文档已有章节 → 适用
          2. 规则的 violation_example / keywords 关键词在文档中出现 → 适用
          3. applies_to 含「全部」→ 总是适用
        """
        full_text = "\n".join(doc_sections.values())
        section_names = set(doc_sections.keys())
        picked: list[Rule] = []

        for r in self.rules:
            if not r.enabled:
                continue
            if only_codes and r.code not in only_codes:
                continue

            # 1) 全部适用
            if any(a in ("全部", "all", "*") for a in r.applies_to):
                picked.append(r)
                continue

            # 2) 章节匹配
            if section_names & set(r.applies_to):
                picked.append(r)
                continue

            # 3) 关键词匹配（含规则自带 keywords 与违规示例中的特征词）
            kws = list(r.keywords)
            if not kws and r.violation_example:
                kws = [w for w in re.findall(r"[\u4e00-\u9fa5]{2,6}", r.violation_example)][:6]
            if kws and any(k and k in full_text for k in kws):
                picked.append(r)
                continue

            # 4) 章节名出现在规则 applies_to 里，但文档章节名有变体 → 模糊匹配
            for a in r.applies_to:
                if any(a in s or s in a for s in section_names if len(a) >= 2):
                    picked.append(r)
                    break

        return picked


# ---------------------------------------------------------------------------
# 法规文档加载
# ---------------------------------------------------------------------------

def _s(v: Any) -> str:
    """把 YAML 值安全转成字符串。

    YAML 会把 2021-05-10 解析成 datetime.date，直接放进 JSON 会序列化失败，
    这里统一转成字符串。
    """
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return str(v)


def load_regulation_docs() -> list[dict[str, Any]]:
    """
    从 knowledge/regulations/ 加载法规文档。

    支持三种形态（可共存）：
      1. *.md / *.txt          —— 法规全文，文件名即标题
      2. _metadata.yaml        —— 元数据清单（title/doc_number/source_url/summary）
      3. 仅元数据、无全文时    —— 用「标题 + 文号 + 摘要」构成可检索条目，
                                  保证知识库不会因为只有清单而完全空白。
    """
    REGULATIONS_DIR.mkdir(parents=True, exist_ok=True)
    docs: list[dict[str, Any]] = []
    meta_map: dict[str, dict[str, Any]] = {}

    # ---- 1) 读元数据清单 ----
    for f in REGULATIONS_DIR.glob("*.y*ml"):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        items = data if isinstance(data, list) else (data or {}).get("regulations", [])
        for item in items or []:
            if isinstance(item, dict) and item.get("title"):
                meta_map[_s(item["title"])] = item

    # ---- 2) 读正文文件 ----
    covered: set[str] = set()
    for f in sorted(REGULATIONS_DIR.iterdir()):
        if f.suffix.lower() not in (".md", ".txt"):
            continue
        text = f.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            continue

        title = re.sub(r"^\d+[-_]?", "", f.stem).strip()
        meta = meta_map.get(title, {})

        # 正文首行的 # 标题优先
        first = text.splitlines()[0].strip()
        if first.startswith("#"):
            title = first.lstrip("#").strip() or title

        covered.add(title)
        docs.append(
            {
                "doc_id": f.stem,
                "title": title,
                "doc_number": _s(meta.get("doc_number")),
                "category": _s(meta.get("category")) or "regulation",
                "source_url": _s(meta.get("source_url")),
                "text": text,
                "meta": {
                    "filename": f.name,
                    "issuing_body": _s(meta.get("issuing_body")),
                    "effective_date": _s(meta.get("effective_date")),
                },
            }
        )

    # ---- 3) 仅元数据的条目：用摘要构建可检索条目 ----
    for title, meta in meta_map.items():
        if title in covered:
            continue
        summary = _s(meta.get("summary"))
        doc_number = _s(meta.get("doc_number"))
        body = f"《{title}》"
        if doc_number:
            body += f"（{doc_number}）"
        body += "。"
        if summary:
            body += summary
        else:
            body += "该法规文件已在知识库登记，但尚未录入正文。请通过「知识库 → 添加法规」上传全文。"

        docs.append(
            {
                "doc_id": _s(meta.get("doc_id")) or f"meta_{len(docs)}",
                "title": title,
                "doc_number": doc_number,
                "category": _s(meta.get("category")) or "regulation",
                "source_url": _s(meta.get("source_url")),
                "text": body,
                "meta": {
                    "filename": "_metadata.yaml",
                    "issuing_body": _s(meta.get("issuing_body")),
                    "effective_date": _s(meta.get("effective_date")),
                    "metadata_only": True,
                },
            }
        )

    return docs


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------

_lib: RuleLibrary | None = None


def get_rule_library(reload: bool = False) -> RuleLibrary:
    global _lib
    if _lib is None:
        _lib = RuleLibrary()
        _lib.load()
    elif reload:
        _lib.load()
    return _lib
