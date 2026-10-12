"""审查接口：SSE 流式进度 + 结果管理。"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..core.config import UPLOAD_DIR, settings
from ..core.knowledge import get_kb
from ..core.llm import LLMGateway, LLMError
from ..core.parser import ParseError, parse_document
from ..core.reviewer import ReviewEngine, ReviewReport
from ..core.rewriter import export_report_docx, export_report_markdown
from ..core.rules import get_rule_library

router = APIRouter()

# 审查结果缓存（进程内）
_REPORTS: dict[str, ReviewReport] = {}


class ReviewIn(BaseModel):
    file_id: str
    provider_id: str | None = None
    only_rules: list[str] | None = None
    concurrency: int | None = None


def _gateway(provider_id: str | None) -> LLMGateway:
    profile = settings.get_provider(provider_id)
    if profile is None:
        if not settings.providers:
            raise HTTPException(
                400, "尚未配置任何模型。请先到「设置」页面填写模型 API 信息。"
            )
        raise HTTPException(
            400, f"未找到模型档案：{provider_id}。请到「设置」页面选择或新建。"
        )
    if not profile.base_url or not profile.model:
        raise HTTPException(
            400, f"模型档案「{profile.display()}」配置不完整，请填写接口地址和模型名称。"
        )
    try:
        return LLMGateway(profile)
    except LLMError as e:
        raise HTTPException(400, str(e)) from e


def _resolve_upload(file_id: str) -> tuple[Path, str]:
    from .upload import _UPLOADS

    meta = _UPLOADS.get(file_id)
    if meta:
        return Path(meta["stored_path"]), meta["original_name"]
    matches = list(UPLOAD_DIR.glob(f"{file_id}.*"))
    if not matches:
        raise HTTPException(404, f"未找到已上传文件：{file_id}")
    return matches[0], matches[0].name


@router.post("/start")
async def start_review(payload: ReviewIn) -> dict:
    """同步执行审查（规则少时用）。规则多时建议用 /stream。"""
    path, name = _resolve_upload(payload.file_id)
    try:
        doc = parse_document(path)
    except ParseError as e:
        raise HTTPException(400, str(e)) from e

    gateway = _gateway(payload.provider_id)
    if payload.concurrency:
        settings.concurrency = max(1, min(16, payload.concurrency))

    engine = ReviewEngine(gateway, kb=get_kb(), library=get_rule_library())
    report = await engine.review(doc, only_rules=payload.only_rules)
    _REPORTS[report.review_id] = report
    return report.to_dict()


@router.post("/stream")
async def stream_review(payload: ReviewIn) -> StreamingResponse:
    """流式审查：通过 SSE 实时推送进度，避免长任务超时。"""
    path, name = _resolve_upload(payload.file_id)
    try:
        doc = parse_document(path)
    except ParseError as e:
        raise HTTPException(400, str(e)) from e

    gateway = _gateway(payload.provider_id)
    if payload.concurrency:
        settings.concurrency = max(1, min(16, payload.concurrency))

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def worker() -> None:
        try:
            engine = ReviewEngine(gateway, kb=get_kb(), library=get_rule_library())

            def on_progress(code: str, done: int, total: int, msg: str) -> None:
                queue.put_nowait(
                    {
                        "type": "progress",
                        "code": code,
                        "done": done,
                        "total": total,
                        "message": msg,
                        "percent": round(done / max(total, 1) * 100, 1),
                    }
                )

            await queue.put(
                {
                    "type": "start",
                    "message": f"开始审查「{name}」",
                    "filename": name,
                }
            )

            report = await engine.review(
                doc, only_rules=payload.only_rules, on_progress=on_progress
            )
            _REPORTS[report.review_id] = report

            await queue.put({"type": "done", "report": report.to_dict()})
        except Exception as e:  # noqa: BLE001
            await queue.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            await queue.put({"type": "__end__"})

    task = asyncio.create_task(worker())

    async def event_gen():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=600)
                except asyncio.TimeoutError:
                    yield "data: " + json.dumps({"type": "ping"}, ensure_ascii=False) + "\n\n"
                    continue
                if item.get("type") == "__end__":
                    break
                yield "data: " + json.dumps(item, ensure_ascii=False) + "\n\n"
        except asyncio.CancelledError:
            task.cancel()
            raise
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/reports")
async def list_reports() -> dict:
    """列出历史审查结果。"""
    items = []
    for rid, r in _REPORTS.items():
        items.append(
            {
                "review_id": rid,
                "filename": r.filename,
                "created_at": r.created_at,
                "issue_count": len(r.issues),
                "verdict": r.summary.get("verdict", ""),
                "verdict_level": r.summary.get("verdict_level", ""),
            }
        )
    items.sort(key=lambda x: -x["created_at"])
    return {"reports": items, "count": len(items)}


@router.get("/reports/{review_id}")
async def get_report(review_id: str) -> dict:
    r = _REPORTS.get(review_id)
    if r is None:
        raise HTTPException(404, f"未找到审查结果：{review_id}")
    return r.to_dict()


@router.delete("/reports/{review_id}")
async def delete_report(review_id: str) -> dict:
    if review_id not in _REPORTS:
        raise HTTPException(404, "未找到审查结果")
    del _REPORTS[review_id]
    return {"ok": True}


class SelectionIn(BaseModel):
    review_id: str
    selected_ids: list[str]


@router.post("/select")
async def update_selection(payload: SelectionIn) -> dict:
    """更新用户对审查意见的勾选状态。"""
    r = _REPORTS.get(payload.review_id)
    if r is None:
        raise HTTPException(404, "未找到审查结果")

    sel = set(payload.selected_ids)
    for f in r.findings:
        f.selected = f.id in sel
    return {
        "ok": True,
        "selected": sum(1 for f in r.findings if f.selected),
        "total": len(r.findings),
    }


@router.post("/reports/{review_id}/export")
async def export_report(review_id: str, fmt: str = "docx") -> dict:
    """导出审查报告（docx / md）。"""
    r = _REPORTS.get(review_id)
    if r is None:
        raise HTTPException(404, "未找到审查结果")

    data = r.to_dict()
    try:
        if fmt == "md":
            path = export_report_markdown(data)
        else:
            path = export_report_docx(data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"导出失败：{e}") from e

    return {"ok": True, "path": path, "filename": Path(path).name}


# ---------------------------------------------------------------------------
# 单规则重审（用户对某条结论有疑问时可重跑）
# ---------------------------------------------------------------------------

class RecheckIn(BaseModel):
    review_id: str
    finding_id: str
    provider_id: str | None = None


@router.post("/recheck")
async def recheck(payload: RecheckIn) -> dict:
    """对单条意见重新审查（换模型或重跑，用于交叉验证）。"""
    r = _REPORTS.get(payload.review_id)
    if r is None:
        raise HTTPException(404, "未找到审查结果")

    target = next((f for f in r.findings if f.id == payload.finding_id), None)
    if target is None:
        raise HTTPException(404, "未找到该条意见")

    lib = get_rule_library()
    rule = next((x for x in lib.rules if x.code == target.rule_code), None)
    if rule is None:
        raise HTTPException(404, f"规则已不存在：{target.rule_code}")

    path, name = None, r.filename
    # 重新解析原始文档
    from .upload import _UPLOADS

    stored = None
    for fid, meta in _UPLOADS.items():
        if meta["original_name"] == r.filename:
            stored = Path(meta["stored_path"])
            break
    if stored is None or not stored.exists():
        matches = [p for p in UPLOAD_DIR.iterdir() if p.is_file()]
        raise HTTPException(
            400, "原始文件已不在缓存中，请重新上传后再审。"
        )

    doc = parse_document(stored)
    gateway = _gateway(payload.provider_id)
    engine = ReviewEngine(gateway, kb=get_kb(), library=lib)

    doc_context = engine._build_doc_context(doc.sections(), settings.max_chunk_chars)  # noqa: SLF001
    new_finding = await engine._evaluate_rule(rule, doc_context, name)  # noqa: SLF001
    new_finding.id = target.id  # 保持 id 稳定，便于前端替换

    idx = r.findings.index(target)
    r.findings[idx] = new_finding
    r.summary = engine._summarize(r)  # noqa: SLF001

    return {"ok": True, "finding": new_finding.to_dict(), "summary": r.summary}
