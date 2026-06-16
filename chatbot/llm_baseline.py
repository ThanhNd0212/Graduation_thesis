"""Full-LLM chatbot baseline — Gemini 2.5 Flash Lite + RAG trên product catalog.

Mỗi lượt chat:
  1. Retrieve top-5 sản phẩm liên quan qua RAG (Gemini embedding)
  2. Build prompt: system + product context + lịch sử hội thoại + tin mới
  3. Gọi Gemini generate → reply
  4. Trả về cùng format với pipeline hybrid để so sánh dễ

Không làm slot-filling hay intent classification — thuần LLM.
"""
from __future__ import annotations

import time
from google import genai
from google.genai import types

from .logger import TurnLogger
from .rag import ProductRAG
from .state import SlotStore

_GEN_MODEL = 'gemini-2.5-flash-lite'
_HISTORY_WINDOW = 10  # số lượt hội thoại gần nhất gửi lên (user + assistant)

_SYSTEM_PROMPT = """\
Bạn là chatbot tư vấn bán hàng LEGO của một shop online Việt Nam. \
Trả lời bằng tiếng Việt, thân thiện, ngắn gọn và tự nhiên như nhân viên bán hàng thật.

Thông tin shop:
- Địa chỉ: số 30 ngõ 20 đường xxx, sđt 08689xxxxx, tiếp khách 8h–18h
- Phí ship: 20,000đ toàn quốc, giao 2–3 ngày
- Giao hỏa tốc: đặt trước 12h → nhận trước 20h cùng ngày; đặt sau 12h → nhận trước 12h hôm sau
- Gói quà: 15,000đ/sản phẩm
- Thanh toán: chuyển khoản trước hoặc COD (nhận hàng trả tiền)
- Tồn kho: mặc định còn hàng với mọi sản phẩm trong danh mục

Khi khách cung cấp tên, số điện thoại, địa chỉ để đặt hàng, hãy xác nhận lại và hỏi \
phương thức thanh toán. Khi đã đủ thông tin (tên, sđt, địa chỉ, sản phẩm, thanh toán), \
xác nhận đơn hàng và báo đơn đã được tiếp nhận.\
"""


class LLMBaseline:
    def __init__(self, rag: ProductRAG, api_key: str, store: SlotStore,
                 logger: TurnLogger | None = None):
        self._rag    = rag
        self._client = genai.Client(api_key=api_key)
        self._store  = store
        self._logger = logger

    def process(self, session_id: str, message: str,
                reply_to_msg_id: str | None = None) -> dict:
        t0 = time.perf_counter()
        s = self._store.get(session_id)
        s.turn += 1
        msg_id = s.next_msg_id()

        # 1) RAG: retrieve relevant products
        products     = self._rag.retrieve(message, top_k=5)
        product_ctx  = self._rag.format_context(products)

        # 2) Build conversation contents for Gemini
        contents = self._build_contents(s, message, product_ctx)

        # 3) Generate
        response = self._client.models.generate_content(
            model=_GEN_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_PROMPT,
                temperature=0.7,
                max_output_tokens=512,
            ),
        )
        reply_text = response.text or ''

        # 4) Token / cost tracking
        usage      = response.usage_metadata
        tokens_in  = getattr(usage, 'prompt_token_count', 0) or 0
        tokens_out = getattr(usage, 'candidates_token_count', 0) or 0
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)

        # 5) Append to conversation history
        s.conversation_history.append({'role': 'user',      'content': message})
        s.conversation_history.append({'role': 'assistant', 'content': reply_text})

        self._store.persist(s)

        # 6) Log turn
        if self._logger:
            rag_names = [p['name'] for p in products]
            self._logger.log({
                'timestamp':  time.strftime('%Y-%m-%dT%H:%M:%S'),
                'session_id': session_id,
                'turn':       s.turn,
                'msg_id':     msg_id,
                'input':      message,
                'reply_to':   reply_to_msg_id,
                'intents':    [],
                'entities':   {},
                'trace': [
                    f'RAG top-{len(products)}: {", ".join(rag_names)}',
                    f'tokens in/out: {tokens_in}/{tokens_out}',
                ],
                'action': 'llm_generate',
                'reply':  reply_text,
                'latency_ms':     latency_ms,
                'llm_tokens_in':  tokens_in,
                'llm_tokens_out': tokens_out,
                'mode': 'llm_full',
            })

        return {
            'msg_id':   msg_id,
            'reply':    reply_text,
            'intents':  [],
            'entities': {},
            'slots':    s.snapshot(),
            'metrics': {
                'latency_ms': latency_ms,
                'llm_tokens_in':  tokens_in,
                'llm_tokens_out': tokens_out,
                'mode': 'llm_full',
            },
        }

    def _build_contents(self, s, message: str, product_ctx: str) -> list:
        """Xây dựng danh sách Content cho Gemini (lịch sử + tin mới)."""
        contents = []

        # Chèn context sản phẩm như 1 lượt "model" phía đầu
        contents.append(types.Content(
            role='user',
            parts=[types.Part(text=f'[Thông tin sản phẩm liên quan từ catalog]\n{product_ctx}')],
        ))
        contents.append(types.Content(
            role='model',
            parts=[types.Part(text='Đã nắm thông tin sản phẩm, sẵn sàng tư vấn.')],
        ))

        # Lịch sử hội thoại (giới hạn window)
        history = s.conversation_history[-(2 * _HISTORY_WINDOW):]
        for turn in history:
            role = 'user' if turn['role'] == 'user' else 'model'
            contents.append(types.Content(
                role=role,
                parts=[types.Part(text=turn['content'])],
            ))

        # Tin nhắn hiện tại
        contents.append(types.Content(
            role='user',
            parts=[types.Part(text=message)],
        ))
        return contents
