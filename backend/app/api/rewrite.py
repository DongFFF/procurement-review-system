"""自动修订接口。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..core.config import EXPORT_DIR, UPLOAD_DIR
from ..core.rewriter import DocumentRewriter, RewriteError
from .review import _REPORTS

router = APIRouter()


class ApplyIn(BaseModel):
    review_id: str
    selected_ids: list[str] | None = None   # 不传则用报告里已勾选的状态
    append_change_log: bool = True
    add_highlights: bool = True
    output_name: str | None = None


def _find_source(filename: str) -> Path:
    """根据原始文件名找回上传的源文件。"""
    from .upload import _UPLOADS

    for meta in _UPLOADS.values():
        if meta["original_name"] == filename:
            p = Path(meta["stored_path"])
            if p.exists():
                return p
    # 回退：按名字模糊找
    for p in UPLOAD_DIR.iterdir():
        if p.is_file() and p.name == filename:
            return p
    raise HTTPException(
        400,
        f"找不到原始文件「{filename}」。可能程序重启导致缓存清空，请重新上传文件后再修订。",
    )


@router.post("/apply")
async def apply_rewrite(payload: ApplyIn) -> dict:
    """按勾选的审查意见自动修订采购需求文件。"""
    report = _REPORTS.get(payload.review_id)
    if report is None:
        raise HTTPException(404, f"未找到审查结果：{payload.review_id}")

    # 确定要应用的意见
    if payload.selected_ids is not None:
        sel = set(payload.selected_ids)
        for f in report.findings:
            f.selected = f.id in sel

    targets = [f for f in report.findings if f.selected and f.violated]
    if not targets:
        raise HTTPException(
            400, "没有勾选任何需要修订的意见。请先在审查结果中勾选要采纳的意见。"
        )

    source = _find_source(report.filename)

    try:
        rewriter = DocumentRewriter(source)
        result = rewriter.apply(
            report.findings,
            output_name=payload.output_name,
            append_change_log=payload.append_change_log,
            add_highlights=payload.add_highlights,
        )
    except RewriteError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"修订失败：{type(e).__name__}: {e}") from e

    return {"ok": True, **result.to_dict()}


class PreviewIn(BaseModel):
    review_id: str
    selected_ids: list[str] | None = None


@router.post("/preview")
async def preview_rewrite(payload: PreviewIn) -> dict:
    """预览将要执行的修订（不实际写文件），让用户先确认。"""
    report = _REPORTS.get(payload.review_id)
    if report is None:
        raise HTTPException(404, "未找到审查结果")

    if payload.selected_ids is not None:
        sel = set(payload.selected_ids)
        targets = [f for f in report.findings if f.id in sel and f.violated]
    else:
        targets = [f for f in report.findings if f.selected and f.violated]

    items = []
    for f in targets:
        has_orig = bool(f.original_text and f.original_text.strip())
        has_repl = bool(f.replacement and f.replacement.strip())
        if has_orig and has_repl:
            action = "replace"
            action_label = "替换原文"
        elif not has_orig and has_repl:
            action = "insert"
            action_label = "新增条款"
        else:
            action = "manual"
            action_label = "需人工处理"
        items.append(
            {
                "finding_id": f.id,
                "rule_name": f.rule_name,
                "severity": f.severity,
                "severity_label": f.to_dict()["severity_label"],
                "action": action,
                "action_label": action_label,
                "location": f.location,
                "before": f.original_text,
                "after": f.replacement,
                "legal_basis": f.legal_basis,
                "suggestion": f.suggestion,
            }
        )

    counts = {
        "replace": sum(1 for i in items if i["action"] == "replace"),
        "insert": sum(1 for i in items if i["action"] == "insert"),
        "manual": sum(1 for i in items if i["action"] == "manual"),
    }
    return {"ok": True, "total": len(items), "counts": counts, "items": items}


@router.get("/download")
async def download(filename: str):
    """下载修订后的文件或导出的报告。"""
    safe = Path(filename).name
    target = EXPORT_DIR / safe
    if not target.exists():
        raise HTTPException(404, f"文件不存在：{safe}")
    return FileResponse(
        str(target),
        filename=safe,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if safe.endswith(".docx")
        else "application/octet-stream",
    )


@router.get("/exports")
async def list_exports() -> dict:
    """列出所有已生成的导出文件。"""
    items = []
    for p in sorted(EXPORT_DIR.iterdir(), key=lambda x: -x.stat().st_mtime):
        if not p.is_file():
            continue
        items.append(
            {
                "filename": p.name,
                "size": p.stat().st_size,
                "created_at": p.stat().st_mtime,
            }
        )
    return {"files": items, "count": len(items)}


@router.delete("/exports/{filename}")
async def delete_export(filename: str) -> dict:
    safe = Path(filename).name
    target = EXPORT_DIR / safe
    if not target.exists():
        raise HTTPException(404, "文件不存在")
    target.unlink()
    return {"ok": True}
