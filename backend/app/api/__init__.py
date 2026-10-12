"""API 路由汇总。"""

from fastapi import APIRouter

from . import config_api, kb, review, rewrite, upload

router = APIRouter()

router.include_router(config_api.router, prefix="/config", tags=["配置与模型"])
router.include_router(kb.router, prefix="/kb", tags=["知识库"])
router.include_router(upload.router, prefix="/upload", tags=["文件上传"])
router.include_router(review.router, prefix="/review", tags=["审查"])
router.include_router(rewrite.router, prefix="/rewrite", tags=["自动修订"])
