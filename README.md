# LEGO Sales Chatbot - Bachelor Thesis

A Vietnamese-language chatbot for an online LEGO store, running in two parallel modes to support the thesis research goal: **comparing operational cost** between a hybrid NLU + rule-based system and a full-LLM chatbot (Gemini).

## Research Questions

| Question | Measurement |
|---|---|
| Can small NLP models (PhoBERT intent + ViSoBERT NER) handle Vietnamese sales conversations reliably? | Verified through conversation logs |
| How large is the cost gap (tokens, latency, API cost) between hybrid NLU and full-LLM? | Live comparison on `/metrics` page |
| Does teen-code normalization help PhoBERT outperform ViSoBERT on informal chat data? | Results table in `approaches.md` |

## System Architecture

```
User
  | HTTP POST /chat  (mode: hybrid | llm_full)
  v
FastAPI  (chatbot/app.py)
  |-- [hybrid]   NLU -> Pipeline -> Template reply
  |              PhoBERT intent (32 classes, multi-label)
  |              ViSoBERT NER (13 entity types)
  |              Slot-filling state machine (branches A-J)
  |              SQLite (completed orders)
  |
  +-- [llm_full] RAG (Gemini Embedding) -> Gemini 2.5 Flash Lite -> Reply
                 11,362 products, numpy cache, lazy background init
```

## Repository Structure

```
GraduateProject/
|-- approach1/                   SVM + TF-IDF notebooks and results
|-- approach2/                   ViSoBERT intent classification notebook
|-- approach3/                   PhoBERT + normalization training notebook
|-- approach3_results/           Trained notebook + exported results
|   +-- results/
|       |-- intent_model/        PhoBERT intent checkpoint (download from Colab)
|       |-- tokenizer/           Tokenizer files
|       |-- mlb.joblib           MultiLabelBinarizer (32 classes)
|       |-- metrics.json         Per-class thresholds
|       +-- prediction_log.jsonl Test set predictions
|-- approach4/                   XLM-RoBERTa notebook
|-- ner/
|   |-- ner_visobertv2.ipynb     NER training notebook (selected model)
|   |-- ner_phobert.ipynb        NER baseline
|   |-- augment_ner_data.py      NER data augmentation
|   |-- budget_ner.py            Budget entity annotation helper
|   |-- info_annotate_ner.py     Customer info annotation helper
|   |-- ner_predict.py           NER inference script
|   +-- results/                 Training curves and evaluation reports
|-- ner_data/                    NER training data (JSON)
|-- final_data/                  Intent training splits + product catalog
|   +-- products_2010_2026_updated.json   11,362 LEGO products
|-- data_preparation/            Data split and preprocessing notebook
|-- chatbot/
|   |-- app.py                   FastAPI server, lazy RAG init, metrics endpoint
|   |-- pipeline.py              Hybrid NLU pipeline (branches A-J)
|   |-- state.py                 SessionState + SlotStore
|   |-- reply.py                 Template-based reply generation
|   |-- nlu.py                   PhoBERT intent + ViSoBERT NER loader
|   |-- confirm.py               Proposal resolution (choice 1/2/3)
|   |-- rag.py                   ProductRAG (embedding cache + hybrid retrieval)
|   |-- llm_baseline.py          LLMBaseline (Gemini + RAG + state extraction)
|   |-- db.py                    SQLite - save completed orders
|   |-- logger.py                TurnLogger -> logs/chat_YYYYMMDD.jsonl
|   |-- cli.py                   CLI test client
|   |-- chatbot_summary.md       State machine reference
|   |-- dialogue_rules.md        Full behaviour specification
|   +-- web/
|       |-- index.html           Chat UI (Messenger-style)
|       |-- chat.js              Mode toggle, reply bubbles, proposal chips
|       |-- style.css            Styling
|       +-- metrics.html         Latency/token/cost comparison + order list
|-- product_matcher.py           Fuzzy + field-aware product matching (rapidfuzz)
|-- product_ner.py               Product NER utilities
|-- check_ner_data.py            NER data validation script
|-- approaches.md                Detailed notes on all 4 intent classification approaches
|-- system_design.md             System design document
|-- requirements.txt
+-- .env                         GOOGLE_API_KEY=... (not committed)
```

## Phase 1 - Intent Classification

Four approaches were compared for Vietnamese intent classification:

| # | Approach | Model | Preprocessing | Test Macro-F1 |
|---|---|---|---|---|
| 1 | SVM + TF-IDF | LinearSVC | None | - |
| 2 | ViSoBERT | `uitnlp/visobert` | None | - |
| **3** | **PhoBERT + Normalize + Segment** | `vinai/phobert-base` | Teen-code normalization + underthesea word segmentation | **0.9022 (selected)** |
| 4 | XLM-RoBERTa | `facebook/xlm-roberta-base` | None | - |

Final model notebook: `approach3_results/approach3_phobert_normalized_after_trained.ipynb`

Test results (Approach 3):

| Metric | Value |
|---|---|
| Macro-F1 | 0.9022 |
| Micro-F1 | 0.9073 |
| Hamming Loss | 0.0065 |
| Subset Accuracy | 0.8533 |

Key techniques:
- **Rule-based normalization**: teen-code dictionary (`k` -> `không`, `đc` -> `được`) with Latin brand name protection
- **Word segmentation**: underthesea (`hà nội` -> `hà_nội`) to match PhoBERT pre-training register
- **Layer freezing**: freeze embeddings + first 4 encoder layers to prevent overfitting
- **Augmentation**: multi-label duplication + compositional synthesis + random token masking (15%)
- **Per-class threshold tuning**: optimal sigmoid threshold per class found on the validation set

> **Inference note**: `chatbot/nlu.py` applies normalization but does NOT run underthesea (to avoid a heavy dependency at serve time). Actual inference accuracy may be slightly lower than the notebook test results.

## Phase 2 - NER (Named Entity Recognition)

| Model | Result |
|---|---|
| PhoBERT NER | Micro-F1 ~0.61 |
| **ViSoBERT NER v2** | **Micro-F1 ~0.70 (selected)** |

13 entity types: `NAME`, `PHONE`, `ADDRESS`, `CITY`, `PRODUCT_NAME`, `MAX_BUDGET`, `MIN_BUDGET`, `QUANTITY`, `SHIP_DATE`, `SHIP_TIME`, `TYPE`, `COMPLEXITY`, `PRODUCT_COLOR`

## Phase 3 - Hybrid Chatbot

Slot-filling pipeline (`chatbot/pipeline.py`) with priority branches A-J:

| Branch | Trigger | Action |
|---|---|---|
| A.0 | stage=done + new greeting | Reset cart, keep customer info |
| A.0-done | stage=done | Lock all branches, prompt for payment confirmation |
| A.1 | pending_availability + affirm | Auto-add product to cart |
| A.3-A.5 | pending_finalize / pending_cancel / cancel mid-flow | Confirm before clearing cart |
| B | await_payment / await_confirm / await_info / await_pickup | Continue order flow |
| B2 | ask_product_info / ask_legit / ask_product_image | Return product info, never add to cart |
| C | customer_reject + unresolved proposal | find_reject |
| D | Explicit choice from proposal | Confirm / suggest_confirm / availability |
| E | Product name or type/color present | Propose top-3 |
| F | agree_order or affirmative + non-empty cart | Enter order flow |
| G | ask_payment_method / ask_product_suggestion + attributes | Suggest or ask payment |
| H | Remaining intent Q&A | final_price, gift_offer, immediate_ship, shop_info, etc. |
| I | provide_cus_inf + cart full + all info complete | Proactive finalize prompt |
| J | 5 consecutive unrecognized turns | Escalate to human agent |

Order stage transitions: `None -> await_info -> await_confirm -> await_payment -> done`

See `chatbot/chatbot_summary.md` and `chatbot/dialogue_rules.md` for the full specification.

## Phase 3 - Full-LLM Chatbot

- Model: Gemini 2.5 Flash Lite (`gemini-2.5-flash-lite`)
- RAG: Gemini Embedding (`models/gemini-embedding-2`) - embeds 11,362 products, cosine search
- Hybrid retrieval: Latin brand/model keyword match (Porsche, Ferrari, Technic) takes priority over semantic search
- Per-turn: 2 Gemini calls (reply generation + structured state extraction in JSON mode)
- Lazy init: first run builds the embedding cache (~2-5 min); server does not block during this time

## Setup

### Requirements

- Python 3.10+
- GPU recommended for PhoBERT + ViSoBERT inference (CPU works but is slower)
- Google API Key (only required for Full-LLM mode)

### Install dependencies

```bash
pip install -r requirements.txt
pip install google-genai>=2.7.0
```

### Configure API key

Create a `.env` file in the project root:

```
GOOGLE_API_KEY=your_api_key_here
```

### Download model checkpoints

Run the following notebooks on Google Colab (GPU required), then download the output folders:

| Notebook | Output to download |
|---|---|
| `approach3_results/approach3_phobert_normalized_after_trained.ipynb` | `approach3_results/results/intent_model/` + `mlb.joblib` + `metrics.json` |
| `ner/ner_visobertv2.ipynb` | `ner/results/ner_model/` |

Expected local structure after download:

```
approach3_results/results/
    intent_model/       PhoBERT intent checkpoint
    tokenizer/          tokenizer files (already in repo)
    mlb.joblib          MultiLabelBinarizer (32 classes)
    metrics.json        per-class thresholds

ner/results/
    ner_model/          ViSoBERT NER checkpoint
```

### Run the server

```bash
python -m uvicorn chatbot.app:app --port 8000
```

Open in browser: http://localhost:8000

Metrics page: http://localhost:8000/metrics

## Interface Features

| Feature | Description |
|---|---|
| Mode toggle | Switch between Hybrid NLU and Full-LLM within the chat UI |
| Proposal chips | Click to select a product from the suggestion list |
| Reply-to | Quote a specific bot message to resolve proposal ambiguity |
| Slot panel | Live view of session state: cart, customer info, order stage |
| /metrics page | Live comparison: average latency, total tokens, estimated cost (VND), completed orders |

## Database

SQLite (`chatbot.db`) - stores only completed orders (`order_stage = 'done'`):

```sql
customers   (id, name, phone, address, city, created_at)
orders      (id, customer_id, session_id, payment_method, gift_wrap,
             quantity, subtotal, final_total, delivery_type, created_at)
order_items (id, order_id, product_id, product_name, price)
```

View via API: `GET /orders` or the `/metrics` page.

## Logging

Each conversation turn is written to `logs/chat_YYYYMMDD.jsonl` and `logs/chat_YYYYMMDD.log`:

```json
{
  "timestamp": "2026-06-16T16:37:44",
  "session_id": "web",
  "turn": 23,
  "input": "cho mình 2 bộ đó đi",
  "intents": ["agree_order"],
  "entities": {"QUANTITY": ["2 bộ"]},
  "trace": ["B: await_confirm + QUANTITY mới -> cập nhật lại tóm tắt đơn"],
  "action": "order_summary",
  "reply": "Dạ shop xác nhận lại đơn...",
  "latency_ms": 171.4
}
```

LLM mode also logs: `rag_products`, `llm_tokens_in`, `llm_tokens_out`.

## Business Constants

| Item | Value |
|---|---|
| Shipping fee | 20.000đ toàn quốc |
| Gift wrapping | 15.000đ / sản phẩm |
| Standard delivery | 2-3 ngày |
| Express delivery | Đặt trước 12h -> nhận trước 20h cùng ngày; đặt sau 12h -> nhận trước 12h hôm sau |
| Payment | Chuyển khoản trước / COD |
| Default stock | Còn hàng (100 đơn vị) cho mọi sản phẩm |
