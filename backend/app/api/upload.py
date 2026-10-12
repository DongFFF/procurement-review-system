"""文件上传与解析接口。"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..core.config import UPLOAD_DIR
from ..core.parser import SUPPORTED_EXT, ParseError, parse_document

router = APIRouter()

# 已上传文件的元信息（进程内缓存，重启后可从磁盘恢复）
_UPLOADS: dict[str, dict] = {}


@router.get("/formats")
async def formats() -> dict:
    """返回支持的文件格式。"""
    return {
        "supported": SUPPORTED_EXT,
        "max_size_mb": 50,
        "hint": "支持 Word（.docx）、PDF、纯文本。若为扫描件 PDF 请先 OCR 或改用 Word。",
    }


@router.post("")
async def upload(file: UploadFile = File(...)) -> dict:
    """上传采购需求文件并解析。"""
    if not file.filename:
        raise HTTPException(400, "缺少文件名")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED_EXT:
        raise HTTPException(
            400,
            f"不支持的文件格式：{suffix or '未知'}。"
            f"当前支持：{'、'.join(SUPPORTED_EXT)}",
        )

    content = await file.read()
    if not content:
        raise HTTPException(400, "文件内容为空")
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(400, "文件超过 50MB 限制")

    file_id = uuid.uuid4().hex[:12]
    safe_name = f"{file_id}{suffix}"
    dest = UPLOAD_DIR / safe_name
    dest.write_bytes(content)

    try:
        doc = parse_document(dest)
    except ParseError as e:
        try:
            dest.unlink()
        except Exception:
            pass
        raise HTTPException(400, str(e)) from e

    _UPLOADS[file_id] = {
        "file_id": file_id,
        "original_name": file.filename,
        "stored_path": str(dest),
        "uploaded_at": time.time(),
        "size": len(content),
    }

    result = doc.to_dict()
    result["file_id"] = file_id
    result["original_name"] = file.filename
    result["size"] = len(content)

    # 附加解析提示
    if doc.meta.get("warning"):
        result["warning"] = doc.meta["warning"]

    return result


@router.get("/{file_id}")
async def get_upload(file_id: str) -> dict:
    """获取已上传文件的信息与解析结果。"""
    meta = _UPLOADS.get(file_id)
    if meta is None:
        # 从磁盘恢复
        matches = list(UPLOAD_DIR.glob(f"{file_id}.*"))
        if not matches:
            raise HTTPException(404, f"未找到文件：{file_id}")
        p = matches[0]
        meta = {
            "file_id": file_id,
            "original_name": p.name,
            "stored_path": str(p),
            "uploaded_at": p.stat().st_mtime,
            "size": p.stat().st_size,
        }
        _UPLOADS[file_id] = meta

    try:
        doc = parse_document(meta["stored_path"])
    except ParseError as e:
        raise HTTPException(400, str(e)) from e

    result = doc.to_dict()
    result.update({"file_id": file_id, "original_name": meta["original_name"]})
    return result


@router.get("/{file_id}/download")
async def download(file_id: str):
    """下载原始上传文件。"""
    meta = _UPLOADS.get(file_id)
    if meta is None:
        matches = list(UPLOAD_DIR.glob(f"{file_id}.*"))
        if not matches:
            raise HTTPException(404, "文件不存在")
        path = matches[0]
        name = path.name
    else:
        path = Path(meta["stored_path"])
        name = meta["original_name"]

    if not path.exists():
        raise HTTPException(404, "文件已不存在")
    return FileResponse(str(path), filename=name)


@router.get("")
async def list_uploads() -> dict:
    """列出本次会话已上传的文件。"""
    items = []
    for fid, m in _UPLOADS.items():
        items.append(
            {
                "file_id": fid,
                "original_name": m["original_name"],
                "size": m["size"],
                "uploaded_at": m["uploaded_at"],
            }
        )
    items.sort(key=lambda x: -x["uploaded_at"])
    return {"files": items, "count": len(items)}
