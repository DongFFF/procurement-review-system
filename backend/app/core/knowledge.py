"""
知识库引擎。

架构：混合检索（Hybrid Retrieval）
  ┌──────────────┐   ┌───────────────┐
  │ 向量语义检索  │ + │ BM25 关键词检索│  → 加权融合 → 重排 → Top-K
  └──────────────┘   └───────────────┘

为什么这样设计（针对采购法规场景）：
  - 法规条文里「第二十条」「财库〔2021〕22号」这类精确串，BM25 命中率远高于向量。
  - 而「以不合理条件限制供应商」这类语义问题，向量检索更擅长。
  - 两者融合，才能既查得准又查得全。

嵌入向量（Embedding）策略：
  - 优先调用用户配置的模型 API 的 /embeddings 接口（若可用）。
  - 不可用时自动降级为本地轻量向量（字符 n-gram 哈希 TF-IDF），
    保证离线也能检索，不阻塞主流程。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .config import INDEX_DIR, KNOWLEDGE_ROOT
from .parser import chunk_text

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class KBChunk:
    """知识库中的一个检索片段。"""

    id: str
    text: str
    doc_title: str = ""
    doc_number: str = ""
    category: str = ""
    source_url: str = ""
    chunk_index: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "doc_title": self.doc_title,
            "doc_number": self.doc_number,
            "category": self.category,
            "source_url": self.source_url,
            "chunk_index": self.chunk_index,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "KBChunk":
        return cls(
            id=d["id"],
            text=d["text"],
            doc_title=d.get("doc_title", ""),
            doc_number=d.get("doc_number", ""),
            category=d.get("category", ""),
            source_url=d.get("source_url", ""),
            chunk_index=d.get("chunk_index", 0),
            meta=d.get("meta", {}),
        )

    def citation(self) -> str:
        """生成引用标签，用于审查结论溯源。"""
        parts = []
        if self.doc_title:
            parts.append(f"《{self.doc_title}》")
        if self.doc_number:
            parts.append(self.doc_number)
        return " ".join(parts) or "知识库"


@dataclass
class KBHit:
    """一次检索命中。"""

    chunk: KBChunk
    score: float
    vec_score: float = 0.0
    bm25_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.chunk.to_dict(),
            "score": round(self.score, 4),
            "vec_score": round(self.vec_score, 4),
            "bm25_score": round(self.bm25_score, 4),
            "citation": self.chunk.citation(),
        }


# ---------------------------------------------------------------------------
# 中文分词（带降级）
# ---------------------------------------------------------------------------

try:
    import jieba  # type: ignore

    jieba.setLogLevel(60)

    def tokenize(text: str) -> list[str]:
        return [t for t in jieba.lcut(text) if t.strip()]

except ImportError:  # pragma: no cover
    def tokenize(text: str) -> list[str]:
        """无 jieba 时的降级：按字符 + 二元组切分。"""
        t = re.sub(r"\s+", "", text)
        return list(t) + [t[i : i + 2] for i in range(len(t) - 1)]


_STOPWORDS = {
    "的", "了", "和", "与", "及", "或", "在", "是", "为", "对", "等", "中",
    "、", "。", "，", "；", "：", "（", "）", "《", "》", "“", "”", " ", "\n",
}


# ---------------------------------------------------------------------------
# 本地轻量嵌入（离线降级方案）
# ---------------------------------------------------------------------------

class LocalHashEmbedder:
    """
    字符 n-gram 哈希 + TF-IDF 加权的轻量向量化。

    不是神经网络嵌入，但对中文法规文本的召回表现可用，
    且完全离线、零依赖、速度快。用于 API 嵌入不可用时兜底。
    """

    dim: int = 1024

    def encode(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        t = re.sub(r"\s+", "", text)
        if not t:
            return vec

        grams: list[str] = []
        for n in (1, 2, 3, 4):
            grams.extend(t[i : i + n] for i in range(len(t) - n + 1))

        if not grams:
            return vec

        counts: dict[str, int] = {}
        for g in grams:
            counts[g] = counts.get(g, 0) + 1

        for g, c in counts.items():
            h = int(hashlib.md5(g.encode("utf-8")).hexdigest()[:8], 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 31) & 1 == 0 else -1.0
            vec[idx] += sign * (1.0 + math.log(c))

        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------

class BM25:
    """经典 BM25 关键词检索。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_tokens: list[list[str]] = []
        self.doc_len: list[int] = []
        self.avg_len: float = 0.0
        self.df: dict[str, int] = {}
        self.n_docs: int = 0

    def fit(self, docs: list[str]) -> None:
        self.doc_tokens = [
            [t for t in tokenize(d) if t not in _STOPWORDS] for d in docs
        ]
        self.doc_len = [len(t) for t in self.doc_tokens]
        self.n_docs = len(docs)
        self.avg_len = (sum(self.doc_len) / self.n_docs) if self.n_docs else 0.0

        df: dict[str, int] = {}
        for toks in self.doc_tokens:
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        self.df = df

    def score(self, query: str, top_k: int = 20) -> list[tuple[int, float]]:
        if not self.n_docs:
            return []
        q_tokens = [t for t in tokenize(query) if t not in _STOPWORDS]
        if not q_tokens:
            return []

        scores = np.zeros(self.n_docs, dtype=np.float32)
        for t in q_tokens:
            df = self.df.get(t, 0)
            if df == 0:
                continue
            idf = math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))
            for i, toks in enumerate(self.doc_tokens):
                tf = toks.count(t)
                if tf == 0:
                    continue
                dl = self.doc_len[i] or 1
                denom = tf + self.k1 * (1 - self.b + self.b * dl / (self.avg_len or 1))
                scores[i] += idf * (tf * (self.k1 + 1)) / denom

        k = min(top_k, self.n_docs)
        idx = np.argpartition(-scores, k - 1)[:k] if k < self.n_docs else np.arange(self.n_docs)
        ranked = sorted(((int(i), float(scores[i])) for i in idx), key=lambda x: -x[1])
        return [(i, s) for i, s in ranked if s > 0]


# ---------------------------------------------------------------------------
# 知识库主体
# ---------------------------------------------------------------------------

class KnowledgeBase:
    """知识库：索引 + 混合检索。"""

    INDEX_FILE = INDEX_DIR / "kb_index.json"
    VEC_FILE = INDEX_DIR / "kb_vectors.npy"

    def __init__(self) -> None:
        self.chunks: list[KBChunk] = []
        self.bm25 = BM25()
        self.vectors: np.ndarray | None = None
        self.embedder = LocalHashEmbedder()
        self.embed_mode: str = "local"       # local | api
        self.loaded_at: float = 0.0
        self.documents: dict[str, dict[str, Any]] = {}  # doc_id -> 元信息

    # -- 状态 --------------------------------------------------------------
    @property
    def size(self) -> int:
        return len(self.chunks)

    def stats(self) -> dict[str, Any]:
        cats: dict[str, int] = {}
        for c in self.chunks:
            cats[c.category or "未分类"] = cats.get(c.category or "未分类", 0) + 1
        return {
            "chunk_count": len(self.chunks),
            "document_count": len(self.documents),
            "categories": cats,
            "embed_mode": self.embed_mode,
            "loaded_at": self.loaded_at,
            "index_file": str(self.INDEX_FILE),
        }

    # -- 构建索引 ----------------------------------------------------------
    def build(self, docs: list[dict[str, Any]], embedder_fn=None) -> dict[str, Any]:
        """
        用文档列表重建索引。

        docs 每项结构：
          {
            "doc_id": str,
            "title": str, "doc_number": str, "category": str,
            "source_url": str, "text": str,
            "meta": {...}
          }
        """
        chunks: list[KBChunk] = []
        documents: dict[str, dict[str, Any]] = {}

        for d in docs:
            doc_id = str(d.get("doc_id") or d.get("title") or f"doc_{len(documents)}")
            text = (d.get("text") or "").strip()
            if not text:
                continue

            documents[doc_id] = {
                "doc_id": doc_id,
                "title": d.get("title", ""),
                "doc_number": d.get("doc_number", ""),
                "category": d.get("category", ""),
                "source_url": d.get("source_url", ""),
                "char_count": len(text),
                "meta": d.get("meta", {}),
            }

            for i, piece in enumerate(chunk_text(text, size=900, overlap=120)):
                chunks.append(
                    KBChunk(
                        id=f"{doc_id}#{i}",
                        text=piece,
                        doc_title=d.get("title", ""),
                        doc_number=d.get("doc_number", ""),
                        category=d.get("category", ""),
                        source_url=d.get("source_url", ""),
                        chunk_index=i,
                        meta={"doc_id": doc_id, **(d.get("meta") or {})},
                    )
                )

        self.chunks = chunks
        self.documents = documents

        # 关键：必须先清空旧向量。
        # 否则在「先 load() 旧索引、再 build() 新索引」的场景下，
        # self.vectors 仍是旧的行数，会因形状不匹配导致向量检索静默失效
        # （表现为所有结果得分都等于 bm25 分量，检索质量大幅下降）。
        self.vectors = None
        self.embed_mode = "local"

        # BM25
        self.bm25.fit([c.text for c in chunks])

        # 向量
        if embedder_fn is not None:
            try:
                vecs = embedder_fn([c.text for c in chunks])
                if vecs is not None and len(vecs) == len(chunks):
                    arr = np.asarray(vecs, dtype=np.float32)
                    norms = np.linalg.norm(arr, axis=1, keepdims=True)
                    norms[norms == 0] = 1.0
                    self.vectors = arr / norms
                    self.embed_mode = "api"
            except Exception:
                self.vectors = None

        if self.vectors is None:
            self.vectors = np.vstack([self.embedder.encode(c.text) for c in chunks]) \
                if chunks else np.zeros((0, self.embedder.dim), dtype=np.float32)
            self.embed_mode = "local"

        self.loaded_at = time.time()
        return self.stats()

    # -- 持久化 ------------------------------------------------------------
    def save(self) -> None:
        payload = {
            "version": 1,
            "saved_at": time.time(),
            "embed_mode": self.embed_mode,
            "documents": self.documents,
            "chunks": [c.to_dict() for c in self.chunks],
        }
        self.INDEX_FILE.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        if self.vectors is not None:
            np.save(self.VEC_FILE, self.vectors)

    def load(self) -> bool:
        if not self.INDEX_FILE.exists():
            return False
        try:
            payload = json.loads(self.INDEX_FILE.read_text(encoding="utf-8"))
            self.chunks = [KBChunk.from_dict(c) for c in payload.get("chunks", [])]
            self.documents = payload.get("documents", {})
            self.embed_mode = payload.get("embed_mode", "local")
            self.bm25.fit([c.text for c in self.chunks])
            if self.VEC_FILE.exists():
                arr = np.load(self.VEC_FILE)
                if arr.shape[0] == len(self.chunks):
                    self.vectors = arr.astype(np.float32)
            if self.vectors is None:
                self.vectors = np.vstack([self.embedder.encode(c.text) for c in self.chunks]) \
                    if self.chunks else np.zeros((0, self.embedder.dim), dtype=np.float32)
            self.loaded_at = payload.get("saved_at", 0.0)
            return True
        except Exception:
            return False

    # -- 检索 --------------------------------------------------------------
    def search(
        self,
        query: str,
        top_k: int = 8,
        *,
        category_filter: Iterable[str] | None = None,
        vec_weight: float = 0.55,
    ) -> list[KBHit]:
        """混合检索：向量 + BM25 加权融合。"""
        if not self.chunks or not query.strip():
            return []

        n = len(self.chunks)
        allow: set[int] | None = None
        if category_filter:
            cats = set(category_filter)
            allow = {i for i, c in enumerate(self.chunks) if c.category in cats}
            if not allow:
                allow = None

        # --- BM25 ---
        bm25_scores = np.zeros(n, dtype=np.float32)
        for i, s in self.bm25.score(query, top_k=max(top_k * 6, 40)):
            bm25_scores[i] = s
        bm25_max = float(bm25_scores.max()) if bm25_scores.size else 0.0
        if bm25_max > 0:
            bm25_norm = bm25_scores / bm25_max
        else:
            bm25_norm = bm25_scores

        # --- 向量 ---
        vec_norm = np.zeros(n, dtype=np.float32)
        if self.vectors is not None and self.vectors.shape[0] == n:
            qv = self.embedder.encode(query)
            sims = self.vectors @ qv
            lo, hi = float(sims.min()), float(sims.max())
            if hi > lo:
                vec_norm = (sims - lo) / (hi - lo)
            else:
                vec_norm = np.clip(sims, 0, 1)

        # --- 融合 ---
        fused = vec_weight * vec_norm + (1 - vec_weight) * bm25_norm

        # 关键词精确命中的小幅加成（文号、条款号）
        q_clean = re.sub(r"\s+", "", query)
        for m in re.finditer(r"[（(]?\d{4}[)）]?\s*\d+\s*号|第[一二三四五六七八九十百\d]+条", q_clean):
            needle = m.group(0)
            for i, c in enumerate(self.chunks):
                if needle in c.text:
                    fused[i] += 0.15

        if allow is not None:
            mask = np.zeros(n, dtype=bool)
            mask[list(allow)] = True
            fused = np.where(mask, fused, -1.0)

        k = min(top_k, n)
        idx = np.argpartition(-fused, k - 1)[:k] if k < n else np.arange(n)
        ranked = sorted(((int(i), float(fused[i])) for i in idx), key=lambda x: -x[1])

        hits: list[KBHit] = []
        for i, sc in ranked:
            if sc <= 0:
                continue
            hits.append(
                KBHit(
                    chunk=self.chunks[i],
                    score=sc,
                    vec_score=float(vec_norm[i]),
                    bm25_score=float(bm25_norm[i]),
                )
            )
        return hits

    def context_for(self, query: str, top_k: int = 8, max_chars: int = 4000) -> tuple[str, list[dict[str, Any]]]:
        """为审查规则拼装「法规依据」上下文，返回 (文本, 引用列表)。"""
        hits = self.search(query, top_k=top_k)
        parts: list[str] = []
        cites: list[dict[str, Any]] = []
        used = 0

        for h in hits:
            piece = f"【{h.chunk.citation()}】\n{h.chunk.text}"
            if used + len(piece) > max_chars and parts:
                break
            parts.append(piece)
            used += len(piece)
            cites.append(
                {
                    "citation": h.chunk.citation(),
                    "doc_title": h.chunk.doc_title,
                    "doc_number": h.chunk.doc_number,
                    "source_url": h.chunk.source_url,
                    "score": round(h.score, 4),
                    "excerpt": h.chunk.text[:200],
                }
            )
        return "\n\n".join(parts), cites


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------

_kb: KnowledgeBase | None = None


def get_kb() -> KnowledgeBase:
    """获取全局知识库实例（懒加载，首次自动从磁盘恢复索引）。"""
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
        _kb.load()
    return _kb
