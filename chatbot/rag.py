"""RAG (Retrieval-Augmented Generation) cho product catalog.

Embed toàn bộ sản phẩm qua Gemini text-embedding-004, cache ra file.
Mỗi lượt chat: embed query → cosine search → trả top-K sản phẩm phù hợp nhất.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import numpy as np
from google import genai

_EMBED_MODEL   = 'models/gemini-embedding-2'
_BATCH_SIZE    = 50         # số sản phẩm mỗi lần gọi API
_CACHE_EMB     = Path(__file__).resolve().parent / 'rag_embeddings.npy'
_CACHE_IDX     = Path(__file__).resolve().parent / 'rag_index.json'

# Latin tokens too generic to use as keyword signal (match virtually every product)
_LATIN_STOPLIST = {'lego'}


def _product_text(p: dict) -> str:
    """Văn bản đại diện cho 1 sản phẩm (dùng để embed)."""
    colors = ', '.join(p.get('color') or []) or 'không rõ màu'
    return (
        f"{p['name']} — {p.get('brand', '')} — {p.get('category', '')} — "
        f"{p.get('type', '')} — giá {p.get('price', 0):,}đ — "
        f"{p.get('number_pieces', '?')} mảnh — màu {colors}. "
        f"{p.get('description', '')}"
    )


def _product_summary(p: dict) -> str:
    """Tóm tắt sản phẩm cho LLM prompt (ngắn gọn hơn)."""
    colors = ', '.join(p.get('color') or []) or '—'
    return (
        f"• {p['name']} ({p.get('brand', '')}) — {p.get('type', '')} — "
        f"{p.get('number_pieces', '?')} mảnh — màu: {colors} — "
        f"giá: {p.get('price', 0):,}đ"
    )


class ProductRAG:
    def __init__(self, products_path: str, api_key: str):
        self._client = genai.Client(api_key=api_key)

        with open(products_path, encoding='utf-8') as f:
            self._products: list[dict] = json.load(f)

        self._embeddings: np.ndarray = self._load_or_build_cache()

    # ── cache ─────────────────────────────────────────────────────────────────

    def _load_or_build_cache(self) -> np.ndarray:
        if _CACHE_EMB.exists() and _CACHE_IDX.exists():
            cached_ids = json.loads(_CACHE_IDX.read_text(encoding='utf-8'))
            current_ids = [p['product_id'] for p in self._products]
            if cached_ids == current_ids:
                return np.load(str(_CACHE_EMB))

        return self._build_cache()

    def _build_cache(self) -> np.ndarray:
        print(f'[RAG] Embedding {len(self._products)} sản phẩm qua Gemini...')
        texts = [_product_text(p) for p in self._products]
        all_vecs: list[list[float]] = []

        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i: i + _BATCH_SIZE]
            result = self._client.models.embed_content(
                model=_EMBED_MODEL,
                contents=batch,
            )
            all_vecs.extend(e.values for e in result.embeddings)
            time.sleep(0.2)  # tránh rate limit

        matrix = np.array(all_vecs, dtype=np.float32)
        norms  = np.linalg.norm(matrix, axis=1, keepdims=True)
        matrix = matrix / np.maximum(norms, 1e-9)  # normalise

        np.save(str(_CACHE_EMB), matrix)
        _CACHE_IDX.write_text(
            json.dumps([p['product_id'] for p in self._products], ensure_ascii=False),
            encoding='utf-8',
        )
        print(f'[RAG] Đã cache embedding ({matrix.shape}).')
        return matrix

    # ── retrieval ─────────────────────────────────────────────────────────────

    def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        """Hybrid retrieval: keyword match (Latin brand/model names) + semantic cosine.

        Keyword hits (sorted by semantic score) come first so specific brands like
        'Porsche', 'Ferrari', 'Technic' always surface even when the conversational
        query is phrased in a way that dilutes the embedding signal.
        """
        # 1) Semantic scores for all products
        result = self._client.models.embed_content(
            model=_EMBED_MODEL,
            contents=query,
        )
        q_vec = np.array(result.embeddings[0].values, dtype=np.float32)
        q_vec /= max(np.linalg.norm(q_vec), 1e-9)
        scores = self._embeddings @ q_vec          # cosine similarity (đã normalize)

        # 2) Keyword match: extract Latin tokens 4+ chars not in stop list
        latin_tokens = [
            t.lower() for t in re.findall(r'[a-zA-Z][a-zA-Z0-9\-]{3,}', query)
            if t.lower() not in _LATIN_STOPLIST
        ]
        keyword_idx: set[int] = set()
        if latin_tokens:
            for i, p in enumerate(self._products):
                text = f"{p.get('name', '')} {p.get('brand', '')} {p.get('category', '')}".lower()
                if any(tok in text for tok in latin_tokens):
                    keyword_idx.add(i)

        # 3) Merge: keyword hits (ranked by semantic score) → remaining semantic results
        kw_sorted   = sorted(keyword_idx, key=lambda i: scores[i], reverse=True)
        semantic_rest = [int(i) for i in np.argsort(scores)[::-1] if i not in keyword_idx]
        combined = kw_sorted + semantic_rest
        return [self._products[i] for i in combined[:top_k]]

    def format_context(self, products: list[dict]) -> str:
        """Format danh sách sản phẩm thành đoạn text cho LLM prompt."""
        return '\n'.join(_product_summary(p) for p in products)
