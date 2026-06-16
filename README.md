# Chatbot Tư Vấn Bán Hàng LEGO — Đồ Án Tốt Nghiệp

Hệ thống chatbot tiếng Việt cho cửa hàng LEGO online, hoạt động theo **hai chế độ song song** để phục vụ mục tiêu nghiên cứu chính của đồ án: **so sánh chi phí vận hành** giữa một hệ thống hybrid (Natural Language Understanding + rule-based) và một chatbot full-LLM (Gemini).

---

## Mục tiêu nghiên cứu

| Câu hỏi nghiên cứu | Cách đo |
|---|---|
| Model NLP nhỏ (PhoBERT intent + ViSoBERT NER) + pipeline rule-based có thể xử lý tốt hội thoại bán hàng không? | Kiểm tra thực tế qua log |
| Chi phí (token, latency, API cost) giữa hybrid (Natural Language Understanding + rule-based) vs full-LLM chênh lệch bao nhiêu? | Trang `/metrics` so sánh trực tiếp |
| Chuẩn hóa văn bản (teen-code → chuẩn) có giúp PhoBERT vượt ViSoBERT trên dữ liệu chat không? | Bảng kết quả `approaches.md` |

---

## Kiến trúc tổng thể

```
Người dùng
    │  HTTP POST /chat  (mode: hybrid | llm_full)
    ▼
FastAPI (chatbot/app.py)
    ├── [hybrid (Natural Language Understanding + rule-based)]   NLU → Pipeline → Reply template
    │              PhoBERT intent + ViSoBERT NER
    │              Slot-filling state machine (14 nhánh A–J)
    │              SQLite (lưu đơn hoàn tất)
    │
    └── [llm_full] RAG (Gemini Embedding) → Gemini 2.5 Flash Lite → Reply
                   11.362 sản phẩm, cache numpy, lazy init background thread
```

---

## Thành phần hệ thống

### Phase 1 — Intent Classification (so sánh 4 approaches)

| # | Approach | Model | Tiền xử lý | Test Macro-F1 |
|---|---|---|---|---|
| 1 | SVM + TF-IDF | LinearSVC | Không | — |
| 2 | ViSoBERT | `uitnlp/visobert` | Không | — |
| **3** | **PhoBERT + Normalize + Segment** | `vinai/phobert-base` | Teen-code → chuẩn + underthesea segment | **0.9022 ✅ (được chọn)** |
| 4 | XLM-RoBERTa | `facebook/xlm-roberta-base` | Không | — |

**Notebook model cuối cùng:** `approach3_results/approach3_phobert_normalized_after_trained.ipynb`

Kết quả test (approach 3):

| Metric | Giá trị |
|---|---|
| Macro-F1 | **0.9022** |
| Micro-F1 | **0.9073** |
| Hamming Loss | 0.0065 |
| Subset Accuracy | 0.8533 |

Kỹ thuật chính trong notebook cuối:
- **Rule-based normalization**: teen-code dict (`k`→không, `đc`→được...) + bảo vệ tên thương hiệu Latin
- **Word segmentation**: `underthesea` (`hà nội` → `hà_nội`) để khớp register PhoBERT được pre-train
- **Layer freezing**: đóng băng embedding + 4 encoder layer đầu để chống overfit
- **Augmentation**: duplicate multi-label samples + compositional synthesis (ghép 2 câu đơn nhãn) + random token masking 15%
- **Per-class threshold tuning**: tìm ngưỡng tối ưu trên val set cho từng lớp trong 32 lớp intent

> **Lưu ý inference:** `chatbot/nlu.py` dùng normalize() nhưng **không** chạy underthesea (để tránh dependency nặng khi serve). Accuracy thực tế có thể thấp hơn một chút so với kết quả test trong notebook.

- **32 lớp intent** (multi-label, sigmoid)
- Tập dữ liệu: chat thực tế cửa hàng LEGO, tiếng Việt không chính thức
- Thách thức: teen-code (`k`=không, `đc`=được), code-switching (lego/ferrari), câu cực ngắn

### Phase 2 — NER (Named Entity Recognition)

| Model | Kết quả |
|---|---|
| PhoBERT NER | Micro-F1 ~0.61 |
| **ViSoBERT NER v2** | **Micro-F1 ~0.70** (được chọn) |

- **13 nhãn thực thể:** `NAME`, `PHONE`, `ADDRESS`, `CITY`, `PRODUCT_NAME`, `MAX_BUDGET`, `MIN_BUDGET`, `QUANTITY`, `SHIP_DATE`, `SHIP_TIME`, `TYPE`, `COMPLEXITY`, `PRODUCT_COLOR`

### Phase 3 — Chatbot Hybrid (Natural Language Understanding + rule-based)

**Pipeline slot-filling** (`chatbot/pipeline.py`) — 14 nhánh ưu tiên (A.0 → J):

| Nhánh | Điều kiện | Hành động |
|---|---|---|
| A.0 | stage=done + chào mới | Reset giỏ hàng |
| A.0-done | stage=done | Khóa pipeline, nhắc gửi bill |
| A.1 | pending_availability + affirm | Tự thêm sản phẩm vào giỏ |
| A.3–A.5 | pending_finalize / pending_cancel / cancel giữa luồng | Xác nhận trước khi hủy |
| B | await_payment / await_confirm / await_info / await_pickup | Tiếp tục luồng đặt hàng |
| D | Chọn số từ proposal | Xác nhận / hỏi thêm (nếu từ suggest) |
| E | Có tên sản phẩm / type/color | Propose top-3 |
| F | agree_order hoặc affirmative + có giỏ | Vào luồng chốt đơn |
| G | ask_payment_method / ask_product_suggestion + có thuộc tính | Suggest / hỏi thanh toán |
| J | 5 lượt không hiểu liên tiếp | Escalate → nhân viên |

**State machine order_stage:** `None → await_info → await_confirm → await_payment → done`

### Phase 3 — Chatbot Full-LLM

- Model: **Gemini 2.5 Flash Lite** (`gemini-2.5-flash-lite`)
- RAG: **Gemini Embedding** (`models/gemini-embedding-2`) — embed 11.362 sản phẩm, cosine search
- **Hybrid retrieval:** keyword match (tên thương hiệu Latin: Porsche, Ferrari, Technic...) ưu tiên trước semantic search
- Lazy init: lần đầu chạy build cache ~2–5 phút, server không bị block

---

## Cấu trúc thư mục

```
GraduateProject/
├── approach1/              SVM + TF-IDF notebooks
├── approach2/              ViSoBERT intent notebook
├── approach3/              PhoBERT + normalize notebook (training)
├── approach3_results/      Checkpoint đã train (intent_model/, mlb.joblib)
├── approach4/              XLM-RoBERTa notebook
├── ner/
│   ├── ner_visobertv2.ipynb    NER training (được chọn)
│   ├── ner_phobert.ipynb       NER baseline
│   └── results/ner_model/      Checkpoint NER đã train
├── final_data/
│   └── products_2010_2026_updated.json   11.362 sản phẩm LEGO
├── chatbot/
│   ├── app.py              FastAPI server + lazy RAG init + metrics
│   ├── pipeline.py         Hybrid (Natural Language Understanding + rule-based) pipeline (nhánh A–J)
│   ├── state.py            SessionState + SlotStore
│   ├── reply.py            Template reply
│   ├── nlu.py              PhoBERT intent + ViSoBERT NER loader
│   ├── confirm.py          Proposal resolution (số 1/2/3...)
│   ├── rag.py              ProductRAG (embed cache + hybrid retrieval)
│   ├── llm_baseline.py     LLMBaseline (Gemini + RAG + logging)
│   ├── db.py               SQLite — lưu đơn hoàn tất
│   ├── logger.py           TurnLogger → logs/chat_YYYYMMDD.jsonl + .log
│   ├── dialogue_rules.md   Spec hành vi đầy đủ (14 mục)
│   └── web/
│       ├── index.html      Giao diện chat Messenger-style
│       ├── chat.js         Mode toggle, reply bubble, proposal chips
│       ├── style.css       Styling
│       └── metrics.html    Trang so sánh latency/token/cost + đơn hàng
├── product_matcher.py      Fuzzy + semantic product matching (rapidfuzz)
├── requirements.txt
├── .env                    GOOGLE_API_KEY=...
└── logs/                   chat_YYYYMMDD.jsonl (auto-generated)
```

---

## Cài đặt và chạy

### 1. Yêu cầu

- Python 3.10+
- GPU khuyến nghị (cho PhoBERT + ViSoBERT inference; CPU được nhưng chậm hơn)
- Google API Key (cho Gemini — chỉ cần nếu dùng chế độ Full LLM)

### 2. Cài thư viện

```bash
pip install -r requirements.txt
pip install google-genai>=2.7.0
```

### 3. Cấu hình API Key

Tạo file `.env` tại thư mục gốc:

```
GOOGLE_API_KEY=your_api_key_here
```

### 4. Chuẩn bị model checkpoints

Chạy hai notebook sau trên **Google Colab** (cần GPU) để tạo checkpoint, sau đó tải về:

| Notebook | Output (tải về máy) |
|---|---|
| `approach3_results/approach3_phobert_normalized_after_trained.ipynb` | `approach3_results/results/intent_model/` + `mlb.joblib` + `metrics.json` |
| `ner/ner_visobertv2.ipynb` | `ner/results/ner_model/` |

Cấu trúc sau khi tải checkpoint về:

```
approach3_results/results/
    intent_model/       ← PhoBERT intent checkpoint (config + pytorch_model.bin)
    tokenizer/          ← tokenizer files
    mlb.joblib          ← MultiLabelBinarizer (32 classes)
    metrics.json        ← per-class thresholds
ner/results/
    ner_model/          ← ViSoBERT NER checkpoint
```

### 5. Chạy server

```bash
python -m uvicorn chatbot.app:app --port 8000
```

Mở trình duyệt: [http://localhost:8000](http://localhost:8000)

Trang metrics: [http://localhost:8000/metrics](http://localhost:8000/metrics)

---

## Tính năng giao diện

| Tính năng | Mô tả |
|---|---|
| **Mode toggle** | Chuyển đổi Hybrid (Natural Language Understanding + rule-based) ↔ Full LLM ngay trên giao diện chat |
| **Proposal chips** | Click chọn sản phẩm từ danh sách gợi ý |
| **Reply-to** | Trả lời tin nhắn cụ thể (giải quyết ambiguity) |
| **Slot panel** | Hiển thị state thực tế: giỏ hàng, thông tin khách, giai đoạn đơn |
| **Trang /metrics** | So sánh live: latency TB, tổng token, chi phí ước tính (VNĐ), đơn hàng DB |

---

## Database

SQLite (`chatbot.db`) — chỉ lưu **đơn đã hoàn tất** (`order_stage = 'done'`):

```sql
customers  (id, name, phone, address, city, created_at)
orders     (id, customer_id, session_id, payment_method, gift_wrap,
            quantity, subtotal, final_total, delivery_type, created_at)
order_items(id, order_id, product_id, product_name, price)
```

Xem qua API: `GET /orders` hoặc trang `/metrics`.

---

## Logging

Mỗi lượt hội thoại được ghi vào `logs/chat_YYYYMMDD.jsonl` và `logs/chat_YYYYMMDD.log`:

```json
{
  "timestamp": "2026-06-16T16:37:44",
  "session_id": "web",
  "turn": 23,
  "input": "cho mình 2 bộ đó đi",
  "intents": ["agree_order"],
  "entities": {"QUANTITY": ["2 bộ"]},
  "trace": ["B: await_confirm + QUANTITY mới → cập nhật lại tóm tắt đơn"],
  "action": "order_summary",
  "reply": "Dạ shop xác nhận lại đơn...",
  "latency_ms": 171.4
}
```

Chế độ LLM ghi thêm: `rag_products`, `llm_tokens_in`, `llm_tokens_out`.

---

## Hằng số nghiệp vụ

| Mục | Giá trị |
|---|---|
| Phí ship | 20.000đ toàn quốc |
| Gói quà | 15.000đ / sản phẩm |
| Giao thường | 2–3 ngày |
| Giao hỏa tốc | Đặt trước 12h → nhận trước 20h; đặt sau 12h → nhận trước 12h hôm sau |
| Thanh toán | Chuyển khoản trước / COD |
| Tồn kho mặc định | Còn hàng (100 đơn vị) cho mọi sản phẩm |

---

## Kết quả so sánh (mục tiêu đo)

| Chỉ số | Hybrid (NLU + rule-based) | Full LLM |
|---|---|---|
| Latency trung bình | ~150–300ms | ~1.5–4s |
| LLM token / lượt | 0 | ~500–1000 in + ~100–300 out |
| Chi phí ước tính / 1000 lượt | ~0đ | ~6.000–15.000đ |
| Độ chính xác luồng đặt hàng | Theo rule (kiểm soát được) | Theo LLM (khó dự đoán) |

*Số liệu thực tế cập nhật tại trang `/metrics` sau khi chạy demo.*
