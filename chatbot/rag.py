"""RAG (Retrieval-Augmented Generation) cho product catalog.

Embed toàn bộ sản phẩm qua Gemini text-embedding-004, cache ra file.
Mỗi lượt chat: embed query -> cosine search -> trả top-K sản phẩm phù hợp nhất.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import numpy as np
from google import genai

_EMBED_MODEL       = 'models/gemini-embedding-2'
_EMBED_SLEEP       = 0.04       # ~25 RPS, safe for the free tier
_CACHE_EMB         = Path(__file__).resolve.parent / 'rag_embeddings.npy'
_CACHE_IDX         = Path(__file__).resolve.parent / 'rag_index.json'
_CHECKPOINT_EMB    = Path(__file__).resolve.parent / 'rag_checkpoint.npy'
_CHECKPOINT_META   = Path(__file__).resolve.parent / 'rag_checkpoint_meta.json'

# Latin tokens too generic to use as keyword signal (match virtually every product)
_LATIN_STOPLIST = {'lego'}

# Transient Gemini API errors — all should be retried
_TRANSIENT_ERRORS = (
    '429', 'RESOURCE_EXHAUSTED',
    '503', 'UNAVAILABLE',
    '500', 'INTERNAL',
    'disconnected', 'connection', 'timeout', 'reset', 'RemoteProtocol',
)


def _product_text(p: dict) -> str:
    """Full text representation of one product used for embedding."""
    colors = ', '.join(p.get('color') or []) or 'không rõ màu'
    return (
        f"{p['name']} — {p.get('brand', '')} — {p.get('category', '')} — "
        f"{p.get('type', '')} — giá {p.get('price', 0):,}đ — "
        f"{p.get('number_pieces', '?')} mảnh — màu {colors}. "
        f"{p.get('description', '')}"
    )


def _product_summary(p: dict) -> str:
    """Compact product summary for inclusion in the LLM prompt."""
    colors = ', '.join(p.get('color') or []) or '—'
    return (
        f"- {p['name']} ({p.get('brand', '')}) — {p.get('type', '')} — "
        f"{p.get('number_pieces', '?')} mảnh — màu: {colors} — "
        f"giá: {p.get('price', 0):,}đ"
    )


def _is_transient(err: str) -> bool:
    return any(code in err for code in _TRANSIENT_ERRORS)


class ProductRAG:
    def __init__(self, products_path: str, api_key: str):
        self._client = genai.Client(api_key=api_key)

        with open(products_path, encoding='utf-8') as f:
            self._products: list[dict] = json.load(f)

        self._embeddings: np.ndarray = self._load_or_build_cache

    # helpers
    def _products_hash(self) -> str:
        ids = json.dumps([p['product_id'] for p in self._products])
        return hashlib.md5(ids.encode).hexdigest

    def _save_checkpoint(self, vecs: list[list[float]], progress: int) -> None:
        if not vecs:
            return
        np.save(str(_CHECKPOINT_EMB), np.array(vecs, dtype=np.float32))
        _CHECKPOINT_META.write_text(
            json.dumps({'hash': self._products_hash, 'progress': progress}),
            encoding='utf-8',
        )

    def _load_checkpoint(self) -> tuple[list[list[float]], int]:
        """Return (vecs, start_idx). start_idx=0 if no valid checkpoint exists."""
        if not (_CHECKPOINT_EMB.exists and _CHECKPOINT_META.exists):
            return [], 0
        try:
            meta = json.loads(_CHECKPOINT_META.read_text(encoding='utf-8'))
            if meta.get('hash') != self._products_hash:
                return [], 0
            vecs = np.load(str(_CHECKPOINT_EMB)).tolist
            progress = meta['progress']
            print(f'[RAG] Tiếp tục từ sản phẩm {progress}/{len(self._products)}...')
            return vecs, progress
        except Exception:
            return [], 0

    def _clear_checkpoint(self) -> None:
        for p in (_CHECKPOINT_EMB, _CHECKPOINT_META):
            try:
                p.unlink
            except FileNotFoundError:
                pass

    # cache
    def _load_or_build_cache(self) -> np.ndarray:
        if _CACHE_EMB.exists and _CACHE_IDX.exists:
            cached_ids = json.loads(_CACHE_IDX.read_text(encoding='utf-8'))
            current_ids = [p['product_id'] for p in self._products]
            if cached_ids == current_ids:
                emb = np.load(str(_CACHE_EMB))
                if len(emb) == len(self._products):
                    return emb
                print(f'[RAG] Cache bị lệch ({len(emb)} embeddings vs {len(self._products)} sản phẩm), rebuild...')

        return self._build_cache

    def _build_cache(self) -> np.ndarray:
        texts = [_product_text(p) for p in self._products]
        total = len(texts)

        all_vecs, start_idx = self._load_checkpoint
        print(f'[RAG] Embedding {total} sản phẩm qua Gemini (bắt đầu từ {start_idx})...')

        for idx in range(start_idx, total):
            text = texts[idx]
            for attempt in range(6):
                try:
                    result = self._client.models.embed_content(
                        model=_EMBED_MODEL,
                        contents=text,
                    )
                    all_vecs.append(list(result.embeddings[0].values))
                    time.sleep(_EMBED_SLEEP)
                    break
                except Exception as e:
                    err = str(e)
                    if _is_transient(err):
                        wait = min(10 * (2 ** attempt), 120)
                        print(f'[RAG] Lỗi tạm thời tại sp {idx} ({err[:60]}), chờ {wait}s... ({attempt + 1}/6)')
                        time.sleep(wait)
                        if attempt == 5:
                            self._save_checkpoint(all_vecs, idx)
                            raise RuntimeError(
                                f'[RAG] Không thể embed sản phẩm {idx} sau 6 lần thử. Checkpoint đã lưu tại {idx}.'
                            )
                    else:
                        self._save_checkpoint(all_vecs, idx)
                        raise

            if (idx + 1) % 500 == 0:
                print(f'[RAG] Đã embed {idx + 1}/{total} sản phẩm...')
                self._save_checkpoint(all_vecs, idx + 1)

        if len(all_vecs) != total:
            raise RuntimeError(
                f'[RAG] Build không hoàn chỉnh: {len(all_vecs)} embeddings cho {total} sản phẩm'
            )

        matrix = np.array(all_vecs, dtype=np.float32)
        norms  = np.linalg.norm(matrix, axis=1, keepdims=True)
        matrix = matrix / np.maximum(norms, 1e-9)

        np.save(str(_CACHE_EMB), matrix)
        _CACHE_IDX.write_text(
            json.dumps([p['product_id'] for p in self._products], ensure_ascii=False),
            encoding='utf-8',
        )
        self._clear_checkpoint
        print(f'[RAG] Đã cache embedding ({matrix.shape}).')
        return matrix

    # retrieval
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
        scores = self._embeddings @ q_vec          # cosine similarity (embeddings are pre-normalized)

        # 2) Keyword match: extract Latin tokens 4+ chars not in stop list
        latin_tokens = [
            t.lower() for t in re.findall(r'[a-zA-Z][a-zA-Z0-9\-]{3,}', query)
            if t.lower() not in _LATIN_STOPLIST
        ]
        keyword_idx: set[int] = set
        if latin_tokens:
            for i, p in enumerate(self._products):
                text = f"{p.get('name', '')} {p.get('brand', '')} {p.get('category', '')}".lower()
                if any(tok in text for tok in latin_tokens):
                    keyword_idx.add(i)

        # 3) Merge: keyword hits (ranked by semantic score) -> remaining semantic results
        kw_sorted     = sorted(keyword_idx, key=lambda i: scores[i], reverse=True)
        semantic_rest = [int(i) for i in np.argsort(scores)[::-1] if i not in keyword_idx]
        combined = kw_sorted + semantic_rest
        return [self._products[i] for i in combined[:top_k]]

    def format_context(self, products: list[dict]) -> str:
        """Format the product list as a text block for the LLM prompt."""
        return '\n'.join(_product_summary(p) for p in products)
