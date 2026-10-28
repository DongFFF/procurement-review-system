"""
证据一致性校验（Evidence Consistency Verification, ECV）。

## 定位

大模型审查意见存在三类可被**确定性规则**捕获的缺陷，无需再调用模型即可识别：

| 缺陷类型 | 表现 | 可计算信号 |
|---------|------|-----------|
| 伪造引文 | 声称的"原文摘录"在文件中根本不存在（模型改写或臆造） | 摘录能否在文档文本中逐字定位 |
| 依据虚指 | 引用的法规条款号在该法规中不存在（条款编号幻觉） | 条款号能否在知识库法规全文中定位 |
| 领域错配 | 技术参数问题却引用商务条款作为依据 | 依据所属法规与规则类别是否匹配 |

这类校验成本极低（纯字符串与集合运算，毫秒级），却能在调用大模型复核之前
过滤掉相当比例的不可靠意见。因此在本框架中，ECV 作为**交叉验证的前置过滤器**：
先跑确定性校验，只有通过者才进入大模型对抗式复核。

## 设计取舍

ECV 只能证伪、不能证成：通过 ECV 的意见不代表一定成立（可能引文真实但法理误判），
但不通过 ECV 的意见几乎一定不可靠。所以它的作用是**提高进入昂贵复核环节的样本纯度**，
而不是替代人工或模型判断。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .knowledge import KnowledgeBase
from .rules import CATEGORY_LABEL


# 中文数字 → 整数（覆盖法规条款常用范围）
_CN_DIGITS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

_ARTICLE_RE = re.compile(r"第([一二三四五六七八九十百零]+)条")
_CLAUSE_ITEM_RE = re.compile(r"第([一二三四五六七八九十]+)项")


def cn_to_int(s: str) -> int:
    """中文数字转整数，支持「二十」「三十五」「一百」等写法。"""
    if not s:
        return 0
    if "百" in s:
        head, _, tail = s.partition("百")
        hundreds = _CN_DIGITS.get(head, 1) if head else 1
        rest = cn_to_int(tail) if tail else 0
        return hundreds * 100 + rest
    if "十" in s:
        head, _, tail = s.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        return tens * 10 + ones
    total = 0
    for ch in s:
        total = total * 10 + _CN_DIGITS.get(ch, 0)
    return total


def _norm(s: str) -> str:
    """归一化：去掉所有空白与常见全半角差异，用于宽松定位。"""
    if not s:
        return ""
    s = re.sub(r"\s+", "", s)
    table = str.maketrans({
        "（": "(", "）": ")", "，": ",", "。": ".", "；": ";",
        "：": ":", "“": '"', "”": '"', "‘": "'", "’": "'",
        "、": ",", "《": "<", "》": ">", "—": "-", "－": "-",
    })
    return s.translate(table)


# ---------------------------------------------------------------------------
# 法规领域映射：用于判断「依据—类别」是否错配
# ---------------------------------------------------------------------------

# 规则类别 → 允许引用的法规关键词（为空表示不限制）
CATEGORY_LAW_MAP: dict[str, tuple[str, ...]] = {
    "technical": ("招标投标管理办法", "需求管理办法", "实施条例", "政府采购法",
                  "进口产品", "需求标准", "品目"),
    "commercial": ("实施条例", "招标投标管理办法", "非招标采购方式", "政府采购法",
                   "中小企业", "需求管理办法"),
    "scoring": ("招标投标管理办法", "实施条例", "需求管理办法", "政府采购法",
                "中小企业", "框架协议"),
    "legality": ("实施条例", "政府采购法", "招标投标管理办法", "非招标采购方式",
                 "备选库", "信用记录", "公平竞争"),
    "policy": ("中小企业", "进口产品", "节能", "环境标志", "政府采购法",
               "需求管理办法", "合作创新", "集中采购目录"),
    "budget": ("政府采购法", "实施条例", "需求管理办法", "招标投标管理办法",
               "集中采购目录", "品目分类目录"),
    "completeness": ("招标投标管理办法", "需求管理办法", "实施条例", "政府采购法",
                     "需求标准"),
    "procedure": ("政府采购法", "实施条例", "需求管理办法", "非招标采购方式",
                  "招标投标管理办法", "意向公开", "集中采购目录", "框架协议",
                  "评审专家", "信息发布", "质疑和投诉"),
}


@dataclass
class EvidenceCheck:
    """一条意见的证据一致性校验结果。"""

    finding_id: str
    quote_found: bool = True          # 原文摘录能否在文档中定位
    quote_is_missing_type: bool = False  # 是否属于缺失类（本无原文）
    clause_exists: bool = True        # 引用条款能否在知识库中定位
    clause_checked: bool = False      # 是否实际做了条款核验
    domain_match: bool = True         # 依据与类别是否匹配
    domain_checked: bool = False

    passed: bool = True
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "quote_found": self.quote_found,
            "clause_exists": self.clause_exists,
            "clause_checked": self.clause_checked,
            "domain_match": self.domain_match,
            "domain_checked": self.domain_checked,
            "passed": self.passed,
            "reasons": self.reasons,
        }


class EvidenceVerifier:
    """确定性证据一致性校验器。"""

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb
        # 预先建立「法规标题 → 全文」索引，避免每次重复拼接
        self._corpus_by_title: dict[str, str] = {}
        try:
            from .rules import load_regulation_docs

            for d in load_regulation_docs():
                self._corpus_by_title[d["title"]] = d["text"]
        except Exception:
            pass
        self._all_corpus = "\n".join(self._corpus_by_title.values())

    # -- 单项校验 ----------------------------------------------------------
    def check(
        self,
        finding_id: str,
        *,
        category: str,
        original_text: str,
        legal_basis: str,
        doc_text: str,
        is_missing_type: bool = False,
    ) -> EvidenceCheck:
        chk = EvidenceCheck(finding_id=finding_id)

        # --- 校验一：原文摘录真实性 ---
        if is_missing_type or not (original_text or "").strip():
            chk.quote_is_missing_type = True
        else:
            hay = _norm(doc_text)
            needle = _norm(original_text)
            if not needle:
                chk.quote_is_missing_type = True
            elif needle in hay:
                chk.quote_found = True
            else:
                # 宽松重试：截取前 20 字做前缀匹配（容忍尾部省略）
                head = needle[:20]
                if len(head) >= 8 and head in hay:
                    chk.quote_found = True
                else:
                    chk.quote_found = False
                    chk.passed = False
                    chk.reasons.append("原文摘录未能在文件中逐字定位（疑似改写或臆造）")

        # --- 校验二：引用条款存在性 ---
        basis = (legal_basis or "").strip()
        if basis and self._all_corpus:
            ok, checked, reasons = self._check_clauses(basis)
            chk.clause_checked = checked
            if checked and not ok:
                chk.clause_exists = False
                chk.passed = False
                chk.reasons.extend(reasons)

        # --- 校验三：依据与类别匹配 ---
        if basis and category:
            allowed = CATEGORY_LAW_MAP.get(category)
            if allowed:
                chk.domain_checked = True
                if not any(kw in basis for kw in allowed):
                    chk.domain_match = False
                    chk.passed = False
                    chk.reasons.append(
                        f"依据与问题类别「{CATEGORY_LABEL.get(category, category)}」不匹配"
                    )

        return chk

    # -- 辅助 --------------------------------------------------------------
    @staticmethod
    def _clean(s: str) -> str:
        """去掉书名号、括号、空格等干扰符，便于标题与依据做包含匹配。"""
        return re.sub(r"[《》()（）\s]", "", s or "")

    def _locate_law(self, basis: str) -> str | None:
        """
        根据依据文本定位对应的法规全文。

        匹配策略：
          1. **标题后缀匹配** —— 取法规标题的最长后缀（≥5字）看是否出现在依据中。
             这是关键：规则依据常省略「中华人民共和国」前缀，写作
             「《政府采购法实施条例》」，而知识库标题是
             「中华人民共和国政府采购法实施条例」。只做整串匹配会全部落空。
             后缀匹配还能天然区分「政府采购法」与「政府采购法实施条例」——
             后者可匹配 8 字后缀，前者只有 5 字，取长者胜。
          2. **文号匹配** —— 依据中的文号出现在该法规正文中，作为强证据加权。

        注意：依据可能同时引用多部法规（用「；」分隔），
        因此本方法只负责**单个片段**的法规定位，分段由 _check_clauses 负责。
        """
        basis_clean = self._clean(basis)
        if not basis_clean:
            return None

        # 提取依据中的文号（如 财库〔2021〕22号 / 财政部令第87号）
        doc_no = ""
        m = re.search(r"[〔\[]\s*(\d{4})\s*[〕\]]\s*(\d+)\s*号", basis)
        if m:
            doc_no = f"{m.group(1)}{m.group(2)}"
        m2 = re.search(r"第\s*(\d+)\s*号", basis)
        if not doc_no and m2:
            doc_no = f"令{m2.group(1)}"

        best: tuple[int, str] | None = None

        for title, text in self._corpus_by_title.items():
            title_clean = self._clean(title)
            score = 0

            # 1) 标题后缀匹配：找最长的、出现在依据中的标题后缀
            best_suffix = 0
            for start in range(len(title_clean)):
                suffix = title_clean[start:]
                if len(suffix) >= 5 and suffix in basis_clean:
                    best_suffix = max(best_suffix, len(suffix))
            score += best_suffix * 2

            # 2) 文号匹配（强证据）
            if doc_no:
                text_head = self._clean(text[:3000])
                if doc_no in text_head:
                    score += 30

            if score > 0 and (best is None or score > best[0]):
                best = (score, text)

        return best[1] if best else None

    def _check_clauses(self, basis: str) -> tuple[bool, bool, list[str]]:
        """
        分段核验依据中的条款是否真实存在。

        返回 (全部存在, 是否实际核验过, 问题说明列表)。

        必须按「；」分段：一条规则的依据常同时引用多部法规，例如
        「《政府采购法实施条例》第三十三条、第四十八条；《关于进一步加大…通知》（财库〔2022〕19号）」。
        若不分段，就会把实施条例的条款号拿去通知里找，产生大量假失败。
        后续片段若省略法规名（如「；第十八条」），继承前一段的法规。
        """
        segments = [s for s in re.split(r"[；;]", basis) if s.strip()]
        checked = False
        reasons: list[str] = []
        inherited: str | None = None

        for seg in segments:
            arts = _ARTICLE_RE.findall(seg)
            if not arts:
                continue

            law = self._locate_law(seg) or inherited
            if law is None:
                reasons.append(f"引用的法规未在知识库中收录，无法核验：{seg.strip()[:40]}")
                continue

            inherited = law
            checked = True
            missing = [f"第{a}条" for a in arts if f"第{a}条" not in law]
            if missing:
                reasons.append(
                    f"引用条款在《{self._title_of(law)}》中不存在：{'、'.join(missing)}"
                )

        return (not reasons), checked, reasons

    def _title_of(self, text: str) -> str:
        for title, t in self._corpus_by_title.items():
            if t is text:
                return title
        return "该法规"

    # -- 批量 --------------------------------------------------------------
    def check_many(self, items: list[dict[str, Any]], doc_text: str) -> list[EvidenceCheck]:
        out: list[EvidenceCheck] = []
        for it in items:
            out.append(
                self.check(
                    finding_id=str(it.get("finding_id", "")),
                    category=str(it.get("category", "")),
                    original_text=str(it.get("original_text", "")),
                    legal_basis=str(it.get("legal_basis", "")),
                    doc_text=doc_text,
                    is_missing_type=bool(it.get("is_missing_type")),
                )
            )
        return out
