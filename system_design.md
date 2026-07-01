# Full System Design: Rule-based Chatbot for LEGO Shop

## Overview

Hệ thống được xây dựng theo ba phase. Mục tiêu nghiên cứu cốt lõi là **so sánh chi phí vận hành** giữa hai chế độ chatbot chạy song song trên cùng một giao diện:

```
Customer message
    -> [Phase 1] Intent Classification  ->  32-class multi-label intent labels (PhoBERT)
    -> [Phase 2] Named Entity Recognition  ->  13 entity types (ViSoBERT)
    -> [Phase 3A] Hybrid (Natural Language Understanding + rule-based) Pipeline  ->  rule-based slot-filling + template reply  (chi phí ~0)
      OR
    -> [Phase 3B] Full-LLM Baseline  ->  Gemini 2.5 Flash Lite + RAG  (chi phí API)
```

Kết quả đo được so sánh trực tiếp trên trang `/metrics`: latency, token count, cost/turn.

---

## Phase 1: Intent Classification

Chi tiết trong [approaches.md](approaches.md). Model được chọn: **PhoBERT + Rule-based Normalization + Word Segmentation** (Approach 3).

```
"sốp ơi còn hàng con ferrari k ạ"
    -> normalize()        -> "shop ơi còn hàng con ferrari không ạ"
    -> underthesea        -> "shop ơi còn hàng con ferrari không ạ"
    -> PhoBERT tokenizer  -> token IDs
    -> PhoBERT + sigmoid  -> ["ask_product_availability"]
```

- **32 lớp intent** (multi-label), threshold tuning per-class trên val set
- Test Macro-F1: **0.9022**, Micro-F1: 0.9073
- Checkpoint: `approach3_results/results/intent_model/`

---

## Phase 2: Named Entity Recognition (NER)

### Entity types thực tế (13 nhãn)

| Nhóm | Nhãn | Ví dụ |
|---|---|---|
| Khách hàng | `NAME`, `PHONE`, `ADDRESS`, `CITY` | `Nguyễn Thành`, `0868928485`, `số 30 ngõ 20 Cát Linh`, `Hà Nội` |
| Ngân sách | `MAX_BUDGET`, `MIN_BUDGET` | `900k`, `tầm 500` |
| Sản phẩm | `PRODUCT_NAME`, `TYPE`, `COMPLEXITY`, `PRODUCT_COLOR` | `Porsche 911`, `xe`, `khó`, `đỏ` |
| Đơn hàng | `QUANTITY`, `SHIP_DATE`, `SHIP_TIME` | `2 bộ`, `ngày mai`, `buổi sáng` |

### Model: ViSoBERT fine-tuned (BIO tagging)

Tất cả 13 nhãn đều qua một model token-classification duy nhất — **không dùng regex** cho bất kỳ nhãn nào:

```
"cho mình 2 bộ Porsche tầm 900k giao ngày mai nhé"
    -> ViSoBERT token classification (BIO)
    -> QUANTITY=["2 bộ"], PRODUCT_NAME=["Porsche"], MAX_BUDGET=["900k"], SHIP_DATE=["ngày mai"]
```

- Model: `uitnlp/visobert` fine-tuned
- Test Micro-F1: ~0.70
- Checkpoint: `ner/results/ner_model/`

### Post-processing

**`PRODUCT_NAME` -> product catalog lookup** (`product_matcher.py`):
- `rapidfuzz` fuzzy matching + optional semantic search
- Input: span text từ NER (`"Porsche"`, `"mclaren extreme"`)
- Output: top-3 candidates từ 11.362 sản phẩm trong `final_data/products_2010_2026_updated.json`
- Kết quả được đưa vào pipeline dưới dạng proposal list

**`MAX_BUDGET` / `MIN_BUDGET`** -> `parse_budget()` chuyển `"900k"` -> `900000` (int)

---

## Phase 3A: Hybrid (Natural Language Understanding + rule-based) Pipeline

### Kiến trúc slot-filling

`chatbot/pipeline.py` — 14 nhánh ưu tiên, chạy tuần tự, nhánh đầu tiên set `action` sẽ thắng:

| Nhánh | Điều kiện | Hành động |
|---|---|---|
| **A.0** | `stage=done` + greeting mới | Reset giỏ, giữ thông tin khách |
| **A.0-done** | `stage=done` (mọi intent khác) | Khóa pipeline, nhắc gửi bill |
| **A.1** | `pending_availability_product` + affirm | Tự thêm sản phẩm vào giỏ |
| **A / A.3 / A.4 / A.5** | `pending_gift` / `pending_finalize` / `pending_cancel` / cancel giữa luồng | Xác nhận trước khi thực hiện |
| **B** | `await_payment` / `await_confirm` / `await_info` / `await_pickup` | Tiếp tục luồng đặt hàng |
| **B2** | Info intent (`ask_product_info`, `ask_product_image`, `ask_legit`) | Trả thông tin, KHÔNG thêm giỏ |
| **C** | `customer_reject` + pending proposal | Từ chối đề xuất |
| **D** | Chọn số từ proposal | Confirm (propose) hoặc hỏi thêm (suggest) |
| **E** | Có `product_query` hoặc type/color | Propose top-3 từ matcher |
| **F** | `agree_order` hoặc affirmative + có giỏ + stage=None | Vào luồng chốt đơn |
| **G** | `ask_payment_method` / `ask_product_suggestion` + thuộc tính | Hỏi thanh toán / suggest |
| **H** | Intent Q&A (price, gift, complaint, thanks, ship...) | Trả lời trực tiếp |
| **I** | `provide_cus_inf` + đủ thông tin + stage=None | Proactive hỏi chốt đơn |
| **J** | 5 lượt liên tiếp không xử lý được | Escalate -> nhân viên |

### State machine `order_stage`

```
None  ->  await_info  ->  await_confirm  ->  await_payment  ->  done
                                   ↑                  ↑
                           (await_pickup)    (lấy trực tiếp -> done)
```

### Session state chính (`state.py`)

| Field | Mô tả |
|---|---|
| `cart` | Danh sách sản phẩm đã xác nhận |
| `customer` | `NAME`, `PHONE`, `ADDRESS`, `CITY` |
| `order` | `QUANTITY`, `MAX_BUDGET`, `SHIP_DATE`... |
| `proposals` | Dict `msg_id -> {candidates, origin_action, resolved}` |
| `pending_*` | Cờ chờ xác nhận: `gift`, `finalize`, `cancel`, `availability_product` |
| `agreed` | Khách đã `agree_order` ít nhất 1 lần |
| `payment` | `'chuyển khoản'` \| `'COD'` \| `None` |

### Database

SQLite (`chatbot.db`) — chỉ ghi khi `order_stage = 'done'`:
```
customers  (name, phone, address, city, created_at)
orders     (customer_id, payment_method, gift_wrap, quantity, subtotal, final_total, delivery_type)
order_items(order_id, product_id, product_name, price)
```

---

## Phase 3B: Full-LLM Baseline (so sánh chi phí)

Chạy song song với hybrid (Natural Language Understanding + rule-based), dùng cùng session store, toggle bằng nút trên UI.

### Pipeline

```
User message
    -> RAG: embed query (Gemini gemini-embedding-2) -> cosine search 11K products
           hybrid retrieval: keyword match (Latin brand names) first, then semantic fill
    -> Build prompt: system_instruction + product context + conversation history (10 turns)
    -> Gemini 2.5 Flash Lite generate_content()
    -> Reply + log (tokens in/out, latency, RAG products retrieved)
```

### RAG chi tiết

- **Embed toàn bộ catalog** lần đầu (~2–5 phút với 11.362 sản phẩm), cache ra `.npy`
- **Hybrid retrieval**: trích Latin token >=4 ký tự từ query (porsche, ferrari, technic...) -> keyword match trước -> fill còn lại bằng cosine search. Đảm bảo brand name cụ thể luôn xuất hiện.
- **Lazy init**: server không bị block khi khởi động; lần đầu gọi llm_full trả 503, build trong background thread.

### Model

- `gemini-2.5-flash-lite` (generation)
- `models/gemini-embedding-2` (embedding)
- SDK: `google-genai >= 2.7.0`

---

## Metrics & Logging

### TurnLogger (`chatbot/logger.py`)

Mỗi lượt ghi `logs/chat_YYYYMMDD.jsonl` + `.log`:

```json
{
  "timestamp": "2026-06-16T16:37:44",
  "session_id": "web", "turn": 23,
  "input": "cho mình 2 bộ đó đi",
  "intents": ["agree_order"], "entities": {"QUANTITY": ["2 bộ"]},
  "trace": ["B: await_confirm + QUANTITY mới -> cập nhật lại tóm tắt đơn"],
  "action": "order_summary", "reply": "...", "latency_ms": 171.4
}
```

LLM mode ghi thêm: `rag_products`, `llm_tokens_in`, `llm_tokens_out`.

### Trang `/metrics`

So sánh live giữa 2 chế độ:

| Chỉ số | Hybrid (NLU + rule-based) | Full LLM |
|---|---|---|
| Latency trung bình | ~150–300ms | ~1.5–4s |
| LLM tokens / lượt | 0 | ~500–1000 in + ~100–300 out |
| Chi phí / 1000 lượt | ~0đ | ~6.000–15.000đ |

---

## Infrastructure

| Thành phần | Công nghệ |
|---|---|
| Backend API | FastAPI + uvicorn |
| Frontend | Messenger-style HTML/JS/CSS (không framework) |
| Session store | In-memory + JSON persist (`sessions/`) |
| Database | SQLite (WAL mode, raw sqlite3) |
| Model serving | `torch` + `transformers` (CPU hoặc GPU) |
| LLM | `google-genai >= 2.7.0` |
| Product matching | `rapidfuzz` + numpy cosine |
