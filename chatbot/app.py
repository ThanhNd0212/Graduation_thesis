"""FastAPI backend — serves the Messenger-style web UI and the /chat endpoint.

Run from the project root:
    python -m uvicorn chatbot.app:app --port 8000
then open http://localhost:8000

Loads PhoBERT intent + ViSoBERT NER + product matcher once at import.
Two modes:
  - hybrid   (default): rule-based pipeline + trained models
  - llm_full           : Gemini 2.5 Flash Lite + RAG
"""

from __future__ import annotations

import os
import threading
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parent.parent / '.env')

from product_matcher import ProductMatcher
from .db import init_db, get_orders
from .llm_baseline import LLMBaseline
from .nlu import NLU
from .pipeline import ChatPipeline
from .rag import ProductRAG
from .state import SlotStore
from .logger import TurnLogger

_ROOT    = Path(__file__).resolve().parent.parent
WEB      = Path(__file__).resolve().parent / 'web'
PRODUCTS = _ROOT / 'final_data' / 'products_2010_2026_updated.json'
_API_KEY = os.environ.get('GOOGLE_API_KEY', '')

# ── shared session store (cả 2 mode dùng chung để lịch sử xuyên suốt) ──────
_store = SlotStore(persist_dir=str(_ROOT / 'sessions'))

init_db()

print('Loading models (PhoBERT intent + ViSoBERT NER + matcher)...')
_pipe = ChatPipeline(
    NLU(),
    ProductMatcher(str(PRODUCTS)),
    _store,
    logger=TurnLogger(str(_ROOT / 'logs')),
)
print('Ready.')

# RAG + LLM được khởi tạo lazy khi lần đầu gọi llm_full
# (build embedding cache cho 11K sản phẩm mất vài phút — không block startup)
_rag: 'ProductRAG | None' = None
_llm: 'LLMBaseline | None' = None
_rag_lock = threading.Lock()
_rag_status = {'ready': False, 'building': False, 'error': None}

app = FastAPI(title='LEGO Shop Chatbot')

# ── In-memory metrics accumulator (reset on server restart) ─────────────────
def _empty_mode_stats():
    return {'turns': 0, 'total_latency_ms': 0.0,
            'llm_tokens_in': 0, 'llm_tokens_out': 0}

_metrics: dict = defaultdict(lambda: {
    'hybrid':   _empty_mode_stats(),
    'llm_full': _empty_mode_stats(),
})


def _update_metrics(session_id: str, mode: str, result: dict) -> None:
    m = result.get('metrics', {})
    s = _metrics[session_id][mode]
    s['turns']            += 1
    s['total_latency_ms'] += m.get('latency_ms', 0)
    s['llm_tokens_in']    += m.get('llm_tokens_in', 0)
    s['llm_tokens_out']   += m.get('llm_tokens_out', 0)


class ChatIn(BaseModel):
    session_id: str = 'web'
    message: str
    reply_to_msg_id: str | None = None
    mode: str = 'hybrid'   # 'hybrid' | 'llm_full'


def _ensure_rag() -> 'LLMBaseline':
    """Khởi tạo RAG + LLM lần đầu (thread-safe). Trả về LLMBaseline đã sẵn sàng."""
    global _rag, _llm
    if _rag_status['ready']:
        return _llm
    with _rag_lock:
        if _rag_status['ready']:
            return _llm
        if _rag_status['building']:
            raise HTTPException(503, 'RAG đang khởi tạo embedding lần đầu, vui lòng thử lại sau 1–2 phút.')
        if _rag_status['error']:
            raise HTTPException(500, f'RAG lỗi: {_rag_status["error"]}')
        _rag_status['building'] = True

    def _build():
        global _rag, _llm
        try:
            print('[RAG] Bắt đầu khởi tạo embedding (chạy nền)...')
            r = ProductRAG(str(PRODUCTS), api_key=_API_KEY)
            l = LLMBaseline(r, api_key=_API_KEY, store=_store, logger=_pipe.logger)
            _rag, _llm = r, l
            _rag_status['ready'], _rag_status['building'] = True, False
            print('[RAG] Sẵn sàng.')
        except Exception as e:
            _rag_status['building'], _rag_status['error'] = False, str(e)
            print(f'[RAG] Lỗi: {e}')

    t = threading.Thread(target=_build, daemon=True)
    t.start()
    raise HTTPException(503, 'RAG đang khởi tạo lần đầu (embedding 11K sản phẩm). '
                             'Thử lại sau 1–2 phút. Xem tiến trình trong terminal.')


@app.post('/chat')
def chat(inp: ChatIn):
    if inp.mode == 'llm_full':
        llm = _ensure_rag()
        result = llm.process(inp.session_id, inp.message,
                             reply_to_msg_id=inp.reply_to_msg_id)
    else:
        result = _pipe.process(inp.session_id, inp.message,
                               reply_to_msg_id=inp.reply_to_msg_id)
    _update_metrics(inp.session_id, inp.mode, result)
    return result


@app.post('/reset')
def reset(session_id: str = 'web'):
    _store.reset(session_id)
    _metrics.pop(session_id, None)
    return {'ok': True}


@app.get('/orders')
def orders(limit: int = 100):
    """Danh sách đơn hàng đã hoàn tất (từ DB)."""
    return get_orders(limit=limit)


@app.get('/api/rag-status')
def rag_status():
    return _rag_status


@app.get('/api/metrics')
def api_metrics():
    """Tổng hợp metrics in-memory cho tất cả sessions đang hoạt động."""
    sessions = []
    for sid, modes in _metrics.items():
        for mode, s in modes.items():
            if s['turns'] == 0:
                continue
            avg_lat = round(s['total_latency_ms'] / s['turns'], 1)
            sessions.append({
                'session_id':   sid,
                'mode':         mode,
                'turns':        s['turns'],
                'avg_latency_ms': avg_lat,
                'llm_tokens_in':  s['llm_tokens_in'],
                'llm_tokens_out': s['llm_tokens_out'],
            })
    return {'sessions': sessions}


@app.get('/')
def index():
    return FileResponse(str(WEB / 'index.html'))


@app.get('/metrics')
def metrics_page():
    return FileResponse(str(WEB / 'metrics.html'))


app.mount('/static', StaticFiles(directory=str(WEB)), name='static')
