"""诊断 F2 组漏检原因。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.evidence import EvidenceVerifier, _ARTICLE_RE  # noqa: E402
from app.core.knowledge import get_kb  # noqa: E402
from app.core.parser import parse_document  # noqa: E402
from app.core.rules import load_regulation_docs  # noqa: E402

import experiment_multiagent as exp  # noqa: E402

doc = parse_document(exp.SAMPLE)
corpus = {d["title"]: d["text"] for d in load_regulation_docs()}
v = EvidenceVerifier(get_kb())
items = exp.build_dataset(doc, corpus)

f2 = [i for i in items if i["group"] == "F2"]
print(f"F2 共 {len(f2)} 条\n")
missed = 0
for it in f2:
    chk = v.check(
        finding_id=it["finding_id"],
        category=it["category"],
        original_text=it["original_text"],
        legal_basis=it["legal_basis"],
        doc_text=doc.full_text,
    )
    if chk.passed:
        missed += 1
        print(f"[漏检] {it['finding_id']}")
        print(f"  依据: {it['legal_basis']}")
        print(f"  条款号: {_ARTICLE_RE.findall(it['legal_basis'])}")
        # 逐段看定位到哪部法规
        import re
        for seg in re.split(r"[；;]", it["legal_basis"]):
            arts = _ARTICLE_RE.findall(seg)
            if not arts:
                continue
            law = v._locate_law(seg)
            title = v._title_of(law) if law else "未定位"
            print(f"    段: {seg.strip()[:50]}")
            print(f"      条款={arts} 法规={title}")
            if law:
                for a in arts:
                    print(f"      '第{a}条' 在该法规中: {f'第{a}条' in law}")
print(f"\n漏检 {missed}/{len(f2)}")
