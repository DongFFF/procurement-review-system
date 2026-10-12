"""知识库管理接口：入库、检索、重建索引、自动更新。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..core.config import KNOWLEDGE_ROOT, settings
from ..core.knowledge import get_kb
from ..core.parser import ParseError, chunk_text, parse_document
from ..core.rules import REGULATIONS_DIR, get_rule_library, load_regulation_docs

router = APIRouter()


# ---------------------------------------------------------------------------
# 状态与检索
# ---------------------------------------------------------------------------

@router.get("/status")
async def status() -> dict:
    kb = get_kb()
    lib = get_rule_library()
    return {
        "kb": kb.stats(),
        "rules": lib.stats(),
        "regulations_dir": str(REGULATIONS_DIR),
        "rules_dir": str(KNOWLEDGE_ROOT / "rules"),
    }


@router.get("/documents")
async def list_documents() -> dict:
    """列出知识库中的所有法规文档。"""
    kb = get_kb()
    docs = []
    for doc_id, meta in kb.documents.items():
        docs.append(
            {
                "doc_id": doc_id,
                "title": meta.get("title", ""),
                "doc_number": meta.get("doc_number", ""),
                "category": meta.get("category", ""),
                "source_url": meta.get("source_url", ""),
                "char_count": meta.get("char_count", 0),
                "meta": meta.get("meta", {}),
            }
        )
    docs.sort(key=lambda d: d["title"])
    return {"documents": docs, "count": len(docs)}


class SearchIn(BaseModel):
    query: str
    top_k: int = 8
    categories: list[str] | None = None


@router.post("/search")
async def search(payload: SearchIn) -> dict:
    """检索知识库，返回命中片段与评分（用于调试和溯源）。"""
    if not payload.query.strip():
        raise HTTPException(400, "查询内容不能为空")
    kb = get_kb()
    hits = kb.search(
        payload.query,
        top_k=payload.top_k,
        category_filter=payload.categories,
    )
    return {
        "query": payload.query,
        "count": len(hits),
        "hits": [h.to_dict() for h in hits],
    }


# ---------------------------------------------------------------------------
# 索引重建
# ---------------------------------------------------------------------------

async def rebuild_index() -> dict[str, Any]:
    """从 knowledge/regulations/ 重建索引。"""
    kb = get_kb()
    docs = load_regulation_docs()
    if not docs:
        return {"ok": False, "message": "knowledge/regulations/ 下没有找到法规文档", "chunk_count": 0}
    stats = kb.build(docs)
    kb.save()
    return {"ok": True, "documents": len(docs), **stats}


@router.post("/rebuild")
async def rebuild() -> dict:
    """重建知识库索引。"""
    result = await rebuild_index()
    if not result.get("ok"):
        raise HTTPException(400, result.get("message", "重建失败"))
    return result


# ---------------------------------------------------------------------------
# 法规文档上传与管理
# ---------------------------------------------------------------------------

@router.post("/documents/upload")
async def upload_regulation(
    file: UploadFile = File(...),
    title: str = "",
    doc_number: str = "",
    category: str = "regulation",
    source_url: str = "",
) -> dict:
    """上传法规文件入库（自动转 Markdown 存盘 + 重建索引）。"""
    if not file.filename:
        raise HTTPException(400, "缺少文件名")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".docx", ".doc", ".pdf", ".txt", ".md"):
        raise HTTPException(400, f"不支持的格式 {suffix}，请上传 docx/pdf/txt/md")

    tmp_dir = KNOWLEDGE_ROOT / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / file.filename

    content = await file.read()
    if not content:
        raise HTTPException(400, "文件内容为空")
    tmp_path.write_bytes(content)

    try:
        doc = parse_document(tmp_path)
    except ParseError as e:
        raise HTTPException(400, str(e)) from e
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass

    # 存为 Markdown
    stem = Path(file.filename).stem
    safe = "".join(c for c in stem if c not in r'\/:*?"<>|').strip() or "regulation"
    md_path = REGULATIONS_DIR / f"{safe}.md"
    body = doc.full_text
    md_path.write_text(f"# {title or stem}\n\n{body}", encoding="utf-8")

    # 更新元数据清单
    meta_path = REGULATIONS_DIR / "_metadata.yaml"
    items: list[dict[str, Any]] = []
    if meta_path.exists():
        try:
            data = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
            items = (data if isinstance(data, list) else (data or {}).get("regulations", [])) or []
        except Exception:
            items = []

    entry = {
        "title": title or stem,
        "doc_number": doc_number,
        "category": category,
        "source_url": source_url,
        "issuing_body": "",
        "effective_date": "",
        "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    items = [i for i in items if i.get("title") != entry["title"]]
    items.append(entry)
    meta_path.write_text(
        yaml.safe_dump({"regulations": items}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    stats = await rebuild_index()
    return {
        "ok": True,
        "saved_as": md_path.name,
        "title": entry["title"],
        "char_count": len(body),
        "index": stats,
    }


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str) -> dict:
    """删除一个法规文档（同时从索引移除）。"""
    kb = get_kb()
    if doc_id not in kb.documents:
        raise HTTPException(404, f"未找到文档：{doc_id}")

    # 找对应文件删除
    target = None
    for f in REGULATIONS_DIR.glob("*.md"):
        if f.stem == doc_id:
            target = f
            break
    if target is None:
        for f in REGULATIONS_DIR.glob("*.txt"):
            if f.stem == doc_id:
                target = f
                break
    if target and target.exists():
        target.unlink()

    stats = await rebuild_index()
    return {"ok": True, "deleted": doc_id, "index": stats}


# ---------------------------------------------------------------------------
# 规则库
# ---------------------------------------------------------------------------

@router.get("/rules")
async def list_rules(category: str = "", severity: str = "", q: str = "") -> dict:
    """列出审查规则。"""
    lib = get_rule_library()
    rules = lib.rules
    if category:
        rules = [r for r in rules if r.category == category]
    if severity:
        rules = [r for r in rules if r.severity == severity]
    if q:
        ql = q.lower()
        rules = [
            r for r in rules
            if ql in r.name.lower() or ql in r.code.lower() or ql in r.check_prompt.lower()
        ]
    return {"rules": [r.to_dict() for r in rules], "count": len(rules), "stats": lib.stats()}


@router.post("/rules/reload")
async def reload_rules() -> dict:
    """热重载规则库（改完 YAML 无需重启）。"""
    lib = get_rule_library(reload=True)
    return {"ok": True, "stats": lib.stats()}


# ---------------------------------------------------------------------------
# 知识库自动更新
# ---------------------------------------------------------------------------

class FetchIn(BaseModel):
    url: str
    title: str = ""
    doc_number: str = ""
    category: str = "regulation"


@router.post("/fetch")
async def fetch_url(payload: FetchIn) -> dict:
    """从 URL 抓取法规正文入库（用于自动更新）。"""
    from ..core.fetcher import fetch_and_ingest

    try:
        result = await fetch_and_ingest(
            payload.url,
            title=payload.title,
            doc_number=payload.doc_number,
            category=payload.category,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"抓取失败：{e}") from e

    stats = await rebuild_index()
    return {"ok": True, **result, "index": stats}


@router.post("/update/check")
async def check_update() -> dict:
    """检查知识库更新（比对远程来源）。"""
    from ..core.fetcher import check_updates

    return await check_updates()


@router.post("/update/run")
async def run_update(force: bool = False) -> dict:
    """执行知识库自动更新。"""
    from ..core.fetcher import run_auto_update

    result = await run_auto_update(force=force)
    if result.get("has_update"):
        result["index"] = await rebuild_index()
    return result


@router.get("/update/log")
async def update_log(limit: int = 50) -> dict:
    """查看知识库更新日志。"""
    from ..core.fetcher import get_update_log

    return {"logs": get_update_log(limit)}


@router.get("/update/scheduler")
async def scheduler_status() -> dict:
    """查看自动更新调度状态。"""
    from ..core.scheduler import scheduler_status as _st

    return _st()


@router.get("/sources")
async def list_sources() -> dict:
    """列出内置的法规自动更新来源。"""
    from ..core.fetcher import get_sources

    return {"sources": get_sources()}
