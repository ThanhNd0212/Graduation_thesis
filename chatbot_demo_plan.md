# Kế hoạch Demo Chatbot — Messenger-style + NER Slot-Filling

> Tài liệu để **bàn & chốt phương án** trước khi code. 🟢 Đề xuất · ✅ Đã chốt · ❓ Cần bạn chốt.
> Mở rộng Phase 3 trong [system_design.md](system_design.md), cập nhật theo thực tế đã build.
>
> **Changelog**
> - v2: thêm §3 **xác nhận sản phẩm top-3** trước slot-filling; §4 **quoted reply** kiểu Messenger.
> - v3: chốt quyết định #2–#5; thêm §8 **Khung so sánh chi phí LLM vs hệ thống của bạn** (mục tiêu chính của đồ án).

---

## 1. Mục tiêu demo

1. **Giao diện chat kiểu Messenger** — nhắn trực tiếp, thấy bot phản hồi ngay.
2. **Xác nhận sản phẩm trước khi chốt**: từ PRODUCT_NAME (NER) → bot đề xuất **top-3** → khách **xác nhận** → *mới* vào hồ sơ đơn.
3. **Trả lời 1 tin nhắn cụ thể** (quoted reply) như Messenger — hỏi nhiều sản phẩm rồi reply đúng tin cũ để chốt.
4. **Lưu & cập nhật NER theo từng label** xuyên suốt hội thoại (phiếu đặt hàng hoàn thiện dần).
5. **🎯 Mục đích đồ án (từ trả lời #3):** **so sánh chi phí** giữa (A) hội thoại **full-LLM** và (B) **hệ thống của bạn** (rule-based + model nhỏ + LLM chỉ vài trường hợp). → kiến trúc phải hỗ trợ **2 chế độ** và **đo cost/latency/token** (xem §8).

---

## 2. Kiến trúc tổng thể

```
┌─────────────────────────────┐         ┌──────────────────────────────────────────────┐
│  FRONTEND (giả lập Messenger)│         │  BACKEND (FastAPI, load model 1 lần)            │
│  - khung chat trái            │  HTTP   │  /chat {session_id, message, reply_to_msg_id,  │
│  - bấm bong bóng → quoted     │ ─────▶  │         mode: "hybrid" | "llm_full"}           │
│    reply                      │ ◀─────  │   ── mode=hybrid — Natural Language Understanding + rule-based (hệ thống của bạn) ──  │
│  - panel "Hồ sơ khách" phải   │  JSON   │     1 Intent(PhoBERT) 2 NER(ViSoBERT)          │
│    (slots realtime)           │         │     3 resolve confirm  4 Matcher top-3         │
│  - đồng hồ cost/latency       │         │     5 update slots     6 reply template        │
└─────────────────────────────┘         │   ── mode=llm_full (baseline) ──               │
                                          │     1 LLM call (catalog+context) → reply       │
                                          │   + metrics logger (token, $, ms)              │
                                          │   STATE STORE theo session_id                  │
                                          └──────────────────────────────────────────────┘
```

**Payload**
```jsonc
// request
{ "session_id":"abc", "message":"chốt con này nhé", "reply_to_msg_id":"m12", "mode":"hybrid" }
// response
{ "msg_id":"m18", "reply":"...", "intents":[...], "entities":{...},
  "proposal": {"candidates":[...top3...]} | null,
  "slots": {...}, "metrics": {"latency_ms":42, "llm_tokens":0, "cost_vnd":0} }
```

---

## 3. Luồng xác nhận sản phẩm  ⭐

NER **không** đưa thẳng PRODUCT_NAME vào đơn — phải qua xác nhận:
```
Khách: "còn con ferrari không shop?"
  → NER PRODUCT_NAME="ferrari" → Matcher → TOP-3
  → Bot (msg m12): "Dạ ý bạn là mẫu nào ạ:
       1) Ferrari F40 — 1.378.000đ   2) Ferrari SF-24 F1 — 726.000đ   3) Ferrari 458 Italia — 130.000đ"
  → lưu pending proposal { m12 → [3 candidates] }   (CHƯA vào slot)
Khách xác nhận: (a) "số 1"/"cái F40"  (b) reply m12 + "chốt con này" (§4)  (c) nút chọn
  → product đã chốt MỚI add vào order.PRODUCT_NAME (kèm product_id)
```
**State thêm:** `proposals: { msg_id → {candidates[], query, turn} }` (lưu mọi đề xuất để reply lại sau).
**Resolve:** có `reply_to_msg_id` → tìm trong proposal đó; không có → proposal gần nhất còn treo. Nếu khách nói "chốt cái này" mà proposal còn 3 lựa chọn → bot hỏi lại "mẫu nào 1/2/3 ạ?".

❓ **Chốt 3a**: top-3 dạng **danh sách đánh số** 🟢 hay **3 bong bóng + nút bấm**?
❓ **Chốt 3b**: nếu 1 ứng viên điểm rất cao → 🟢 vẫn hỏi 1 câu xác nhận (an toàn) hay tự chốt?

---

## 4. "Trả lời tin nhắn" kiểu Messenger (quoted reply)  ⭐

**Tình huống:** khách hỏi sp A (m2), sp B (m6)... rồi **reply vào m2** "chốt sp này" → backend nhờ `reply_to_msg_id=m2` biết đang chốt sp A, không nhầm B.

**Streamlit/Gradio làm được không? → KHÔNG tự nhiên:**
| Frontend | Reply-vào-tin-cụ-thể | Giống Messenger | Công sức |
|---|---|---|---|
| **HTML/CSS/JS tự viết** 🟢 | ✅ bấm bong bóng → trích dẫn → gửi kèm `reply_to_msg_id` | ✅ | ~1 ngày |
| Gradio `gr.Chatbot` | ❌ list tuyến tính | ❌ | giả lập bằng dropdown (xấu) |
| Streamlit `st.chat_message` | ❌ tương tự | ❌ | giả lập bằng selectbox "trả lời tin #N" |

→ Vì quoted reply là **cốt lõi** cho luồng xác nhận đa lượt, **HTML/JS tự viết là lựa chọn đúng**. Streamlit/Gradio chỉ hợp nếu chấp nhận chọn-tin-qua-dropdown.

❓ **Chốt #1 (quan trọng, bạn chưa trả lời)**: HTML/JS tự viết 🟢 *(quoted reply thật)* hay Streamlit/Gradio (reply qua dropdown)?

---

## 5. Slot-Filling — "Hồ sơ khách" theo session

```python
{
  "customer": { "NAME":{...}, "PHONE":{...}, "ADDRESS":{...}, "CITY":{...} },   # ghi đè khi có mới
  "order": {
     "PRODUCT_NAME": [ {raw, matched:{product_id,...}, confirmed:true, turn} ],  # ✅ tích luỹ nhiều món, chỉ vào sau xác nhận §3
     "MAX_BUDGET":{...}, "MIN_BUDGET":null, "PRODUCT_COLOR":["đỏ"],
     "TYPE":{...}, "COMPLEXITY":null, "QUANTITY":{...}, "SHIP_DATE":{...}, "SHIP_TIME":null
  },
  "proposals": { "m12": {candidates, turn} },     # đề xuất đang treo (§3/§4)
  "history": [ ... log mọi cập nhật slot ... ]     # "lưu lại & cập nhật từng label"
}
```
**Luật cập nhật:**
- **Đơn trị (ghi đè):** NAME, PHONE, ADDRESS, CITY, MAX/MIN_BUDGET, QUANTITY, SHIP_DATE, SHIP_TIME, TYPE, COMPLEXITY — giá trị mới thay cũ, cũ đẩy vào `history`.
- **Đa trị (tích luỹ):** PRODUCT_NAME *(chỉ sau xác nhận)*, PRODUCT_COLOR — thêm vào danh sách. ✅ **giỏ hàng nhiều món**.
- Chỉ cập nhật khi NER trích được label đó (non-empty). Panel UI nhấp nháy ô vừa đổi.

✅ **Lưu state (#2):** **in-memory trước** để test nhanh; thiết kế `state.py` có lớp lưu trừu tượng để **sau cắm Postgres/MySQL** không phải sửa pipeline.

---

## 6. Luồng xử lý 1 tin nhắn — hybrid (Natural Language Understanding + rule-based)
```
{message, session_id, reply_to_msg_id?}
  → Intent(PhoBERT, text normalize) → intents[]
  → NER(ViSoBERT, text gốc) → entities{}
  → cập nhật slots customer/order (trừ PRODUCT_NAME) §5
  → nếu xác nhận (agree_order / reply_to / "số N"): resolve §3 → add product đã chốt
  → elif hỏi sản phẩm & có PRODUCT_NAME mới: Matcher → top-3 → tạo proposal
  → reply generator (ưu tiên multi-intent: provide_cus_inf > agree_order > ask_* > social)
  → trả {msg_id, reply, intents, entities, proposal?, slots, metrics}
```

---

## 7. Sinh câu trả lời (reply generator)
✅ **Hướng (#3):** **template-first** (zero-cost, tối ưu chi phí cho cửa hàng) + **LLM chỉ cho vài trường hợp khó**:
- LLM fallback khi: intent=`other`, confidence thấp, hoặc không khớp template nào.
- Mỗi lần gọi LLM đều **ghi token/cost** để phục vụ so sánh §8.
- Slot-aware: thiếu slot bắt buộc thì hỏi tiếp (đã chốt sp nhưng chưa có ADDRESS → "cho mình địa chỉ giao hàng ạ").

---

## 8. Khung so sánh chi phí  🎯 (mục tiêu đồ án — từ #3)

Cùng 1 interface `/chat`, 2 chế độ để chạy **cùng một kịch bản hội thoại** rồi đối chiếu:

| | **A. full-LLM (baseline)** | **B. Hệ thống của bạn (hybrid — Natural Language Understanding + rule-based)** |
|---|---|---|
| Mỗi tin nhắn | 1 lần gọi LLM (kèm catalog/context) | template + model nhỏ; LLM chỉ khi cần (§7) |
| Cost/tin | cao (token in+out) | ~0 (đa số), thỉnh thoảng 1 LLM |
| Latency | giây | ms |

**Metrics logger** (ghi theo session & tổng hợp): số tin, số lần gọi LLM, token in/out, **$ ước tính**, latency trung bình → xuất bảng/biểu đồ so sánh = **kết quả chính của đồ án**.

❓ **Chốt 8a**: provider LLM cho mode A & fallback? (dự án đã có **Gemini** — `google-generativeai` — dùng luôn cho nhất quán; hay muốn so nhiều provider?)
❓ **Chốt 8b**: làm mode `llm_full` **ngay từ đầu** (để có số so sánh sớm) hay làm sau khi xong hybrid (Natural Language Understanding + rule-based)?

---

## 9. Tech stack
| Lớp | 🟢 Đề xuất | Trạng thái |
|---|---|---|
| Backend | **FastAPI** (phục vụ cả API lẫn web) | |
| Frontend | **HTML/CSS/JS tự viết** (quoted reply + Messenger look + panel slot) | ❓ #1 chờ chốt |
| State | **in-memory trước**, trừu tượng hoá để cắm Postgres/MySQL sau | ✅ #2 |
| Reply | template-first + LLM fallback có đo cost | ✅ #3 |
| LLM | Gemini (đã có trong stack) — cho mode A & fallback | ❓ #8a |
| Model | PhoBERT (intent) + ViSoBERT (NER) trong tiến trình FastAPI | |

---

## 10. Cấu trúc thư mục đề xuất
```
chatbot/
  pipeline.py     # orchestrator hybrid (Natural Language Understanding + rule-based): intent+NER+matcher+confirm+state+reply
  llm_baseline.py # mode A: full-LLM + đo token/cost
  state.py        # SlotStore (lớp lưu trừu tượng: memory→DB) + proposals + history
  confirm.py      # đề xuất top-3 & resolve xác nhận (§3/§4)
  reply.py        # template theo intent (+ slot-aware) + LLM fallback
  metrics.py      # logger cost/latency/token (§8)
  app.py          # FastAPI: /chat, phục vụ web/
  web/ index.html, style.css, chat.js   # UI Messenger + quoted reply + panel slot
  sessions/       # lịch sử slot + log hội thoại mỗi session
product_matcher.py  # đã có
```

## 11. Lộ trình build  ✅ (#5: có thể song song)
- **Nhánh A (lõi NLU):** state.py → confirm.py → pipeline.py → reply.py (test CLI: gõ nhiều tin, thấy slot điền dần + xác nhận top-3).
- **Nhánh B (giao diện):** app.py (FastAPI) → web/ (UI Messenger + quoted reply).
- **Nhánh C (so sánh):** metrics.py → llm_baseline.py.
→ A và B làm song song được; C ghép sau khi A chạy.

---

## 12. Bảng trạng thái quyết định — ✅ ĐÃ CHỐT TOÀN BỘ
| # | Vấn đề | Quyết định |
|---|---|---|
| 1 | Frontend | ✅ **HTML/CSS/JS tự viết** (quoted reply thật + Messenger look) |
| 2 | Lưu state | ✅ in-memory trước, trừu tượng để cắm Postgres/MySQL sau |
| 3 | Câu trả lời | ✅ template-first + LLM vài trường hợp; phục vụ so sánh cost |
| 4 | Giỏ hàng | ✅ tích luỹ nhiều sản phẩm |
| 5 | Thứ tự build | ✅ song song (NLU ‖ UI), ghép cost sau |
| 3a | Hiển thị top-3 | ✅ **danh sách đánh số** |
| 3b | 1 ứng viên điểm cao | ✅ **vẫn hỏi xác nhận** (an toàn) |
| 8a | Provider LLM | ✅ **Gemini** (`google-generativeai`, đã có) |
| 8b | Mode full-LLM | ✅ **làm sau cùng** (Nhánh C) |

> 🔒 **CHỐT** — bắt đầu code theo §11. Mode `llm_full` + metrics để cuối (Nhánh C).
