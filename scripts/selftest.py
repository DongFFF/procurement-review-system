"""后端核心模块自检脚本。运行：python scripts/selftest.py"""

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

PASS, FAIL = [], []


def check(name, fn):
    try:
        result = fn()
        PASS.append(f"{name}: {result}" if result else name)
    except Exception as e:
        FAIL.append(f"{name}: {type(e).__name__}: {e}")
        traceback.print_exc()


# --- 1. 导入 ---
def t_imports():
    from app.core.config import PRESET_PROVIDERS, settings
    from app.core.fetcher import html_to_text
    from app.core.knowledge import KnowledgeBase
    from app.core.llm import LLMGateway, extract_json
    from app.core.parser import chunk_text, parse_document
    from app.core.reviewer import ReviewEngine
    from app.core.rewriter import DocumentRewriter
    from app.core.rules import RuleLibrary
    from app.core.scheduler import scheduler_status

    return f"{len(settings.providers)} 个预置供应商"


# --- 2. JSON 鲁棒解析 ---
def t_json():
    from app.core.llm import extract_json

    cases = [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '好的，结果如下：\n```json\n{"violated": true, "confidence": 0.9}\n```\n以上。',
        '{"a": 1,}',
        '前言 {"x": [1,2,3]} 后语',
    ]
    for c in cases:
        extract_json(c)
    return f"{len(cases)} 种格式全部解析成功"


# --- 3. 文本切片 ---
def t_chunk():
    from app.core.parser import chunk_text

    text = "。".join([f"这是第{i}句测试文本内容" for i in range(300)])
    chunks = chunk_text(text, size=500, overlap=80)
    assert len(chunks) > 3, f"切片数量异常: {len(chunks)}"
    assert all(len(c) <= 600 for c in chunks), "存在超长切片"
    return f"长文切为 {len(chunks)} 片"


# --- 4. HTML 正文提取 ---
def t_html():
    from app.core.fetcher import html_to_text

    html = """
    <html><head><style>body{color:red}</style></head><body>
    <div class="nav">首页 新闻 政策</div>
    <div class="TRS_Editor">
      <p>第一条 为了规范政府采购需求管理，制定本办法。</p>
      <p>第二条 采购人应当科学合理确定采购需求。</p>
    </div>
    <div class="foot">版权所有</div>
    </body></html>
    """
    text = html_to_text(html)
    assert "第一条" in text, f"未提取到正文: {text[:200]}"
    assert "color:red" not in text, "未清除 style"
    return f"提取 {len(text)} 字"


# --- 5. 知识库构建与检索 ---
def t_kb():
    from app.core.knowledge import KnowledgeBase

    kb = KnowledgeBase()
    docs = [
        {
            "doc_id": "test1",
            "title": "政府采购法实施条例",
            "doc_number": "国务院令第658号",
            "category": "regulation",
            "text": (
                "第二十条 采购人或者采购代理机构有下列情形之一的，属于以不合理的条件"
                "对供应商实行差别待遇或者歧视待遇：\n"
                "（一）就同一采购项目向供应商提供有差别的项目信息；\n"
                "（二）设定的资格、技术、商务条件与采购项目的具体特点和实际需要不相适应"
                "或者与合同履行无关；\n"
                "（三）采购需求中的技术、服务等要求指向特定供应商、特定产品；\n"
                "（四）以特定行政区域或者特定行业的业绩、奖项作为加分条件或者中标、成交条件；\n"
                "（五）对供应商采取不同的资格审查或者评审标准；\n"
                "（六）限定或者指定特定的专利、商标、品牌或者供应商；\n"
                "（七）非法限定供应商的所有制形式、组织形式或者所在地；\n"
                "（八）以其他不合理条件限制或者排斥潜在供应商。\n"
            ),
        },
        {
            "doc_id": "test2",
            "title": "政府采购需求管理办法",
            "doc_number": "财库〔2021〕22号",
            "category": "rule",
            "text": (
                "第十一条 采购需求应当完整、明确，包括以下内容：\n"
                "（一）采购标的需实现的功能或者目标，以及为落实政府采购政策需满足的要求；\n"
                "（二）采购标的需执行的相关标准、规范；\n"
                "（三）采购标的需满足的质量、安全、技术规格、物理特性等要求；\n"
                "（四）采购标的的数量、采购项目交付或者实施的时间和地点；\n"
                "（五）采购标的需满足的服务标准、期限、效率等要求；\n"
                "（六）采购标的的验收标准；\n"
                "（七）采购标的的其他技术、服务等要求。\n"
            ),
        },
    ]
    stats = kb.build(docs)
    assert stats["chunk_count"] > 0, "索引为空"

    # 语义检索
    hits = kb.search("以不合理条件限制供应商", top_k=3)
    assert hits, "语义检索无结果"
    assert "第二十条" in hits[0].chunk.text, f"检索结果不相关: {hits[0].chunk.text[:80]}"

    # 文号精确检索
    hits2 = kb.search("财库〔2021〕22号", top_k=3)
    assert hits2, "文号检索无结果"

    # 上下文拼装
    ctx, cites = kb.context_for("采购需求应当包括哪些内容", top_k=2)
    assert ctx and cites, "上下文拼装失败"

    return f"{stats['chunk_count']} 片段 / 语义+文号检索均命中"


# --- 6. 规则库 ---
def t_rules():
    from app.core.rules import get_rule_library

    lib = get_rule_library(reload=True)
    assert len(lib.rules) > 0, f"规则库为空。错误: {lib.errors}"

    sections = {
        "技术参数": "★处理器：Intel Core i7-13700，品牌限定为联想。",
        "资格条件": "投标人须具有本省行政区域内同类项目业绩。",
        "评分办法": "主观分占70分。",
    }
    picked = lib.prefilter(sections)
    assert picked, "粗筛未选出任何规则"
    return f"{len(lib.rules)} 条规则，粗筛命中 {len(picked)} 条"


# --- 7. 文档解析 + 修订（端到端） ---
def t_docx_roundtrip():
    from docx import Document

    from app.core.config import UPLOAD_DIR
    from app.core.parser import parse_document
    from app.core.rewriter import DocumentRewriter
    from app.core.reviewer import Finding

    # 造一个测试采购需求文件
    src = UPLOAD_DIR / "_selftest_procurement.docx"
    d = Document()
    d.add_heading("XX大学实验设备采购需求文件", 0)
    d.add_heading("一、项目概况", 1)
    d.add_paragraph("本项目采购高性能计算服务器一批，用于人工智能科研平台建设。")
    d.add_heading("二、技术参数", 1)
    d.add_paragraph("1. CPU：Intel Xeon Platinum 8480+，2颗。")
    d.add_paragraph("2. 品牌要求：必须为联想ThinkSystem SR650 V3，不接受其他品牌。")
    d.add_paragraph("3. 内存：512GB DDR5 ECC。")
    d.add_heading("三、资格条件", 1)
    d.add_paragraph("投标人须提供本省行政区域内高校同类项目业绩不少于3个。")
    d.add_paragraph("投标人注册资本不低于人民币5000万元。")
    d.add_heading("四、评分办法", 1)
    d.add_paragraph("技术方案主观评分70分，价格分30分。")
    d.save(str(src))

    # 解析
    doc = parse_document(src)
    assert doc.blocks, "解析结果为空"
    sections = doc.sections()
    assert "技术参数" in sections, f"章节识别失败: {list(sections.keys())}"
    assert "资格条件" in sections, f"章节识别失败: {list(sections.keys())}"

    # 构造一条意见并执行修订
    f = Finding(
        id="t1",
        rule_code="TEST",
        rule_name="指定品牌",
        category="technical",
        severity="high",
        violated=True,
        title="指定特定品牌",
        detail="技术参数中限定品牌",
        original_text="2. 品牌要求：必须为联想ThinkSystem SR650 V3，不接受其他品牌。",
        replacement="2. 品牌要求：本项目不限定品牌，投标产品应满足上述技术参数要求。",
        selected=True,
    )
    rw = DocumentRewriter(src)
    result = rw.apply([f], output_name="_selftest_revised.docx")
    assert result.replaced == 1, f"替换失败: {result.to_dict()}"

    # 校验修订结果
    out = Document(str(result.output_path))
    full = "\n".join(p.text for p in out.paragraphs)
    assert "不限定品牌" in full, "修订内容未写入"
    assert "不接受其他品牌" not in full, "原文未删除"
    assert "修订说明表" in full, "未追加修订说明表"

    # 清理
    src.unlink(missing_ok=True)
    Path(result.output_path).unlink(missing_ok=True)

    return f"解析→分节→替换→说明表 全链路通过（{len(sections)} 个章节）"


# --- 8. FastAPI 应用装配 ---
def t_app():
    """校验 FastAPI 应用装配：所有预期路由都已注册。

    注意：不能遍历 app.routes 来判断——新版 FastAPI 会把 include_router
    的结果包成 _IncludedRouter，其子路由无法通过 .routes/.router 访问。
    权威来源是 app.openapi()，它反映真正对外暴露的接口。
    """
    from app.main import app

    spec = app.openapi()
    paths = sorted(spec.get("paths", {}).keys())
    assert paths, "OpenAPI 未生成任何路径，应用装配失败"

    # 关键接口必须存在
    need = [
        "/api/health",
        "/api/config",
        "/api/kb/status",
        "/api/kb/search",
        "/api/kb/rebuild",
        "/api/kb/rules",
        "/api/upload",
        "/api/upload/formats",
        "/api/review/stream",
        "/api/review/reports",
        "/api/rewrite/apply",
        "/api/rewrite/preview",
    ]
    missing = [n for n in need if n not in paths]
    assert not missing, f"缺少关键路由: {missing}"

    # 分组统计，便于确认模块都挂上了
    groups: dict[str, int] = {}
    for p in paths:
        parts = p.strip("/").split("/")
        key = parts[1] if len(parts) > 1 else parts[0]
        groups[key] = groups.get(key, 0) + 1

    summary = "、".join(f"{k} {v}" for k, v in sorted(groups.items()))
    return f"{len(paths)} 条路由（{summary}）"


if __name__ == "__main__":
    check("1. 核心模块导入", t_imports)
    check("2. JSON 鲁棒解析", t_json)
    check("3. 文本切片", t_chunk)
    check("4. HTML 正文提取", t_html)
    check("5. 知识库构建与检索", t_kb)
    check("6. 规则库与粗筛", t_rules)
    check("7. 文档解析与自动修订", t_docx_roundtrip)
    check("8. FastAPI 应用装配", t_app)

    print("\n" + "=" * 70)
    for p in PASS:
        print(f"  [PASS] {p}")
    for f in FAIL:
        print(f"  [FAIL] {f}")
    print("=" * 70)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    sys.exit(1 if FAIL else 0)
