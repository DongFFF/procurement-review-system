"""知识库 + 规则库集成自检。运行：python scripts/test_kb.py"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.knowledge import get_kb  # noqa: E402
from app.core.rules import get_rule_library, load_regulation_docs  # noqa: E402

print("=" * 70)
print("1) 加载法规文档")
docs = load_regulation_docs()
print(f"   共 {len(docs)} 个文档")
only_meta = sum(1 for d in docs if d["meta"].get("metadata_only"))
print(f"   其中仅元数据（无全文）：{only_meta}")

print("\n2) 构建索引")
kb = get_kb()
stats = kb.build(docs)
print(f"   片段数：{stats['chunk_count']}")
print(f"   文档数：{stats['document_count']}")
print(f"   向量模式：{stats['embed_mode']}")
print(f"   类别分布：{stats['categories']}")
kb.save()
print("   索引已保存")

print("\n3) 检索验证")
queries = [
    ("以不合理条件限制供应商", "实施条例第二十条"),
    ("资格条件 注册资本 规模条件", "87号令第十七条"),
    ("价格分权重 货物 30%", "87号令第五十五条"),
    ("投标保证金 不得超过 2%", "实施条例第三十三条"),
    ("履约保证金 10%", "实施条例第四十八条"),
    ("小微企业 价格扣除 10%-20%", "财库〔2022〕19号"),
    ("进口产品 论证 专家", "财库〔2007〕119号"),
    ("采购需求 应当包括 内容", "需求管理办法"),
    ("化整为零 规避公开招标", "政府采购法第二十八条"),
    ("评审因素 量化", "需求管理办法第二十一条"),
]
ok = 0
for q, expect in queries:
    hits = kb.search(q, top_k=3)
    if not hits:
        print(f"   [MISS] {q}")
        continue
    top = hits[0]
    joined = " ".join(h.chunk.text for h in hits)
    found = expect.replace(" ", "") in joined.replace(" ", "")
    mark = "OK  " if found else "WARN"
    if found:
        ok += 1
    print(f"   [{mark}] {q}")
    print(f"          → {top.chunk.citation()} (score={top.score:.3f})")
print(f"   命中预期依据：{ok}/{len(queries)}")

print("\n4) 规则库")
lib = get_rule_library(reload=True)
s = lib.stats()
print(f"   规则数：{s['rule_count']}  启用：{s['enabled_count']}")
print(f"   类别：{s['by_category']}")
print(f"   级别：{s['by_severity']}")
if s["errors"]:
    print(f"   ⚠ 加载警告 {len(s['errors'])} 条：")
    for e in s["errors"][:5]:
        print(f"      - {e}")

print("\n5) 规则粗筛（模拟一份有问题的需求文件）")
sections = {
    "技术参数": "品牌要求：必须为联想ThinkSystem SR650 V3，不接受其他品牌。",
    "资格条件": "投标人须具有本省行政区域内高校同类项目业绩3个，注册资本不低于5000万元。",
    "评分办法": "价格分20分，技术方案主观评分60分，去掉最高最低报价取平均价作为基准价。",
    "商务要求": "履约保证金为合同金额的20%，验收合格后一年付款。",
}
picked = lib.prefilter(sections)
print(f"   从 {len(lib.rules)} 条中筛出 {len(picked)} 条适用规则")
from collections import Counter  # noqa: E402

sev = Counter(r.severity for r in picked)
print(f"   其中 blocker={sev.get('blocker',0)} high={sev.get('high',0)} medium={sev.get('medium',0)}")
print("   示例：")
for r in picked[:6]:
    print(f"      [{r.severity}] {r.code} - {r.name}")

print("\n6) RAG 上下文拼装")
ctx, cites = kb.context_for("技术参数指定品牌是否违规", top_k=3)
print(f"   上下文 {len(ctx)} 字，引用 {len(cites)} 条")
for c in cites:
    print(f"      - {c['citation']}")

print("\n" + "=" * 70)
print("知识库与规则库集成测试完成")
