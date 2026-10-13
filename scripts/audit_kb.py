"""知识库质量审计：验证规则库引用的法规条款是否真的能在知识库中检索到。

这是保证审查结论可信的关键检查：
如果某条规则引用「《政府采购法实施条例》第二十条」，
但知识库里检索不到该条款，那么模型只能凭自己的记忆判断，结论就不可靠。

运行：python scripts/audit_kb.py
"""

import re
import sys
from collections import Counter
from pathlib import Path

# Windows 控制台默认 GBK，无法输出 ✓/✗ 等符号，强制 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.knowledge import get_kb  # noqa: E402
from app.core.rules import get_rule_library  # noqa: E402

# 法规简称 → 知识库文档标题关键词
# 键是规则 legal_basis 里可能出现的写法，值是知识库文件名里的特征词。
DOC_ALIASES = {
    # 法律 / 行政法规
    "政府采购法实施条例": "实施条例",
    "中华人民共和国政府采购法": "中华人民共和国政府采购法",
    "《政府采购法》": "中华人民共和国政府采购法",
    # 部门规章
    "货物和服务招标投标管理办法": "招标投标管理办法",
    "财政部令第87号": "招标投标管理办法",
    "非招标采购方式管理办法": "非招标采购方式管理办法",
    "财政部令第74号": "非招标采购方式管理办法",
    "框架协议采购方式管理暂行办法": "框架协议",
    "财政部令第110号": "框架协议",
    "信息发布管理办法": "信息发布管理办法",
    "财政部令第101号": "信息发布管理办法",
    "质疑和投诉办法": "质疑和投诉办法",
    "财政部令第94号": "质疑和投诉办法",
    "合作创新采购方式管理暂行办法": "合作创新",
    # 规范性文件
    "政府采购需求管理办法": "需求管理办法",
    "促进中小企业发展管理办法": "促进中小企业发展管理办法",
    "进一步加大政府采购支持中小企业力度": "进一步加大政府采购支持中小企业力度",
    "进口产品管理办法": "进口产品管理办法",
    "评审专家管理办法": "评审专家管理办法",
    "代理机构管理暂行办法": "代理机构管理暂行办法",
    "公平竞争优化营商环境": "公平竞争优化营商环境",
    "查询及使用信用记录": "信用记录",
    "备选库、名录库、资格库": "备选库",
    "意向公开": "意向公开",
    "节能产品、环境标志产品政府采购执行机制": "执行机制",
    "节能产品政府采购品目清单": "节能产品政府采购品目清单",
    "环境标志产品政府采购品目清单": "环境标志产品政府采购品目清单",
    "透明度和采购效率": "透明度和采购效率",
    "品目分类目录": "品目分类目录",
    "集中采购目录及标准": "集中采购目录及标准",
    "集中采购目录实施方案": "集中采购目录实施方案",
    "必须招标的基础设施和公用事业项目范围规定": "必须招标的基础设施",
    "三年行动方案": "三年行动方案",
    "物业管理服务政府采购需求标准": "物业管理服务",
    "商品包装": "商品包装",
    "绿色数据中心": "绿色数据中心",
    "台式计算机政府采购需求标准": "台式计算机",
    "便携式计算机政府采购需求标准": "便携式计算机",
    "通用服务器政府采购需求标准": "通用服务器",
    "操作系统政府采购需求标准": "操作系统",
    "数据库政府采购需求标准": "数据库",
    "公告和公示信息格式规范": "公告和公示信息格式规范",
}

CN_NUM = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15,
    "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20, "二十一": 21,
    "二十二": 22, "二十三": 23, "二十四": 24, "二十五": 25, "二十六": 26,
    "二十七": 27, "二十八": 28, "二十九": 29, "三十": 30, "三十一": 31,
    "三十二": 32, "三十三": 33, "三十四": 34, "三十五": 35, "三十六": 36,
    "三十七": 37, "三十八": 38, "三十九": 39, "四十": 40, "四十一": 41,
    "四十二": 42, "四十三": 43, "四十四": 44, "四十五": 45, "四十六": 46,
    "四十七": 47, "四十八": 48, "四十九": 49, "五十": 50, "五十一": 51,
    "五十二": 52, "五十三": 53, "五十四": 54, "五十五": 55, "五十六": 56,
    "五十七": 57, "五十八": 58, "五十九": 59, "六十": 60, "六十一": 61,
    "六十二": 62, "六十三": 63, "六十四": 64, "六十五": 65, "六十六": 66,
    "六十七": 67, "六十八": 68, "六十九": 69, "七十": 70, "七十一": 71,
    "七十二": 72, "七十三": 73, "七十四": 74, "七十五": 75, "七十六": 76,
    "七十七": 77, "七十八": 78, "七十九": 79, "八十": 80, "八十一": 81,
    "八十二": 82, "八十三": 83, "八十四": 84, "八十五": 85, "八十六": 86,
    "八十七": 87, "八十八": 88, "八十九": 89, "九十": 90,
}

ARTICLE_RE = re.compile(r"第([一二三四五六七八九十百]+)条")


def cn2int(s: str) -> int:
    if s in CN_NUM:
        return CN_NUM[s]
    if "百" in s:
        parts = s.split("百")
        h = CN_NUM.get(parts[0], 1)
        t = CN_NUM.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return h * 100 + t
    return 0


def main() -> int:
    kb = get_kb()
    lib = get_rule_library()

    print("=" * 74)
    print("知识库质量审计")
    print("=" * 74)
    print(f"\n知识库：{len(kb.documents)} 个文档 / {kb.size} 个检索片段")
    print(f"规则库：{len(lib.rules)} 条规则")

    # 建立「文档 → 全文」索引，用于精确验证条款存在性
    from app.core.rules import load_regulation_docs

    docs = load_regulation_docs()
    full_by_title = {d["title"]: d["text"] for d in docs}
    all_text = "\n".join(full_by_title.values())

    # ---- 1. 统计规则引用的法规 ----
    print("\n" + "-" * 74)
    print("1) 规则引用的法规分布")
    print("-" * 74)

    cited_docs = Counter()
    rules_with_article = 0
    rules_no_article = 0

    for r in lib.rules:
        basis = r.legal_basis or ""
        found_any = False
        for alias, kw in DOC_ALIASES.items():
            if alias in basis:
                cited_docs[kw] += 1
                found_any = True
        if ARTICLE_RE.search(basis):
            rules_with_article += 1
        else:
            rules_no_article += 1

    for doc, cnt in cited_docs.most_common():
        exists = any(doc in t for t in full_by_title)
        mark = "✓" if exists else "✗ 缺失"
        print(f"   {mark} {doc}: {cnt} 条规则引用")

    print(f"\n   引用到具体条款号的规则：{rules_with_article} 条")
    print(f"   仅引用到文件名的规则：{rules_no_article} 条")

    # ---- 2. 验证条款是否存在 ----
    print("\n" + "-" * 74)
    print("2) 条款存在性验证（规则引用的条款是否真的在知识库全文中）")
    print("-" * 74)

    verified, unverified, no_article = [], [], []

    for r in lib.rules:
        basis = r.legal_basis or ""
        arts = ARTICLE_RE.findall(basis)
        if not arts:
            no_article.append(r)
            continue

        # 一条规则的依据可能引用多部法规，用「；」拆开分别核对。
        # 否则会把 A 法规的条款号拿去 B 法规里找，产生假失败。
        # 注意：写法常为「《X办法》第十四条；第十八条」——后段省略了法规名，
        # 需继承前一段的法规，否则无法定位。
        segments = [s for s in re.split(r"[；;]", basis) if s.strip()]

        problems: list[str] = []
        inherited_title: str | None = None

        for seg in segments:
            seg_arts = ARTICLE_RE.findall(seg)
            if not seg_arts:
                continue

            # 在片段内匹配法规文档
            target_title = None
            for alias, kw in DOC_ALIASES.items():
                if alias in seg:
                    for t in full_by_title:
                        if kw in t:
                            target_title = t
                            break
                    if target_title:
                        break

            if target_title:
                inherited_title = target_title
            else:
                # 省略法规名的后续片段，继承前一段的法规
                target_title = inherited_title

            if not target_title:
                problems.append(f"未匹配到法规文档（{seg.strip()[:40]}）")
                continue

            text = full_by_title[target_title]
            for a in seg_arts:
                if f"第{a}条" not in text:
                    problems.append(f"{target_title} 中未找到 第{a}条")

        if problems:
            unverified.append((r, "；".join(problems)))
        else:
            verified.append(r)

    print(f"   ✓ 条款可在知识库中验证：{len(verified)} 条")
    print(f"   ✗ 条款未能验证：{len(unverified)} 条")
    print(f"   - 未引用具体条款：{len(no_article)} 条")

    if unverified:
        print("\n   未能验证的规则（需人工复核）：")
        for r, reason in unverified[:25]:
            print(f"     [{r.severity:7}] {r.code}")
            print(f"              依据：{r.legal_basis}")
            print(f"              原因：{reason}")

    # ---- 3. 检索质量抽样 ----
    print("\n" + "-" * 74)
    print("3) 检索质量抽样（用规则名作为查询）")
    print("-" * 74)

    hit_top1 = 0
    hit_top5 = 0
    total = 0

    for r in lib.rules[:30]:
        q = f"{r.name} {r.legal_basis}"
        hits = kb.search(q, top_k=5)
        if not hits:
            continue
        total += 1

        # 判断是否命中该规则引用的法规
        target_kw = None
        for alias, kw in DOC_ALIASES.items():
            if alias in (r.legal_basis or ""):
                target_kw = kw
                break

        if target_kw:
            if target_kw in hits[0].chunk.doc_title or target_kw in hits[0].chunk.text:
                hit_top1 += 1
            if any(target_kw in h.chunk.doc_title or target_kw in h.chunk.text for h in hits):
                hit_top5 += 1
        else:
            total -= 1

    if total:
        print(f"   首位命中正确法规：{hit_top1}/{total} ({hit_top1/total*100:.0f}%)")
        print(f"   前5位命中正确法规：{hit_top5}/{total} ({hit_top5/total*100:.0f}%)")

    # ---- 4. 知识库覆盖度 ----
    print("\n" + "-" * 74)
    print("4) 知识库覆盖度")
    print("-" * 74)

    with_text = sum(1 for d in docs if not d["meta"].get("metadata_only"))
    only_meta = sum(1 for d in docs if d["meta"].get("metadata_only"))
    print(f"   有全文的法规：{with_text} 个")
    print(f"   仅有元数据的法规：{only_meta} 个")

    if only_meta:
        print("\n   仅元数据（建议补充全文）：")
        for d in docs:
            if d["meta"].get("metadata_only"):
                print(f"     - {d['title']}")

    total_chars = sum(len(t) for t in full_by_title.values())
    print(f"\n   知识库总字数：{total_chars:,}")

    # ---- 结论 ----
    print("\n" + "=" * 74)
    score = len(verified) / max(len(verified) + len(unverified), 1) * 100
    print(f"条款可验证率：{score:.0f}%（{len(verified)}/{len(verified)+len(unverified)}）")
    if score >= 90:
        print("评价：优秀 —— 绝大多数规则的法规依据都能在知识库中溯源")
    elif score >= 70:
        print("评价：良好 —— 部分规则依据需补充法规全文")
    else:
        print("评价：需改进 —— 较多规则依据无法溯源，建议补充法规全文")
    print("=" * 74)

    return 0


if __name__ == "__main__":
    sys.exit(main())
