# Luật hội thoại chatbot LEGO shop

Spec hành vi cho `pipeline.py` / `reply.py` / `state.py` / `product_matcher.py`.
✅ đã code · 🔶 đã chốt, **chưa code** · từ feedback `chat_history.md` (session 1 + 2).

---

## 1. Hằng số nghiệp vụ
| Mục | Giá trị |
|---|---|
| Phí ship | **20k** toàn quốc |
| Giao thường | **2–3 ngày** toàn quốc |
| Giao **hỏa tốc** | Đặt **trước 12h** → nhận **trước 20h cùng ngày**; đặt sau 12h → nhận **trước 12h hôm sau** |
| **Gói quà** | **15k / sản phẩm** |
| **Tồn kho** mặc định | **100** cho mọi sản phẩm (DB chưa có cột số lượng — tạm) |
| Thanh toán | **Chuyển khoản trước** / **COD** |
| **Thông tin shop** | "số 30 ngõ 20 đường xxx, sđt 08689xxxxx, tiếp khách trực tiếp 8h–18h" |
| Giữ đơn (lấy trực tiếp) | tối đa **2 ngày** kể từ ngày xác nhận |

---

## 2. Cơ chế nền (background mechanisms)

### 2.1 Parse ngân sách (góp ý m2, m3 — session 2) 🔶
- `k` / `nghìn` / `ngàn` → **×1.000** (100k = 100.000).
- `tr` / `triệu` / `m` → **×1.000.000** (1tr / 1 tr = 1.000.000). *(Hiện `1tr` đang ra 1.000 — SAI, cần sửa `parse_budget`.)*
- Nếu **MIN_BUDGET > MAX_BUDGET** → **tự động đảo** hai giá trị.

### 2.2 Theo dõi ngữ cảnh (cần thêm vào state) 🔶
- `last_product`: **sản phẩm match gần nhất** (đề xuất được chọn / hỏi tới) — dùng cho `ask_product_info`, `ask_legit`, `ask_product_image`, `give_product`.
- `last_intent`: intent có nghĩa **gần nhất** — khi gặp `give_product` (mơ hồ) thì tra `last_intent` để trả lời đúng ngữ cảnh.
- `unknown_streak`: đếm số lượt liên tiếp intent `other`/rỗng → **3 lần** thì escalate (mục 4.x).

### 2.3 Làm sạch entity (R6 ✅) + tồn kho
- Strip dấu câu thừa; PHONE → chỉ chữ số.
- Tồn kho: `stock(product) = 100` mặc định (>0 → còn hàng).

### 2.4 Khớp sản phẩm — **field-aware (đã chốt)** 🔶
**KHÔNG** nối tất cả thành 1 string rồi so blob-vs-blob (loãng điểm, mất trọng số, color/budget không thể fuzzy). Dùng **lai field-aware**:
- **Fuzzy** trên phần CHỮ gộp: **`name + type + category + brand`** (token_set_ratio).
  - `name`/`category`/`brand` tiếng Anh; `type` tiếng Việt (27 giá trị: Xe hơi, Rồng, Máy bay, Tàu, Robot, Lâu đài, Khủng long...). Cho `type` vào vùng fuzzy để query thuần Việt ("rồng") khớp được.
- **color** (tiếng Việt) → **boost** nếu khớp tập `color` của sản phẩm (KHÔNG nhét vào string fuzzy).
- **budget** (và sau này **tồn kho**) → **lọc số** (≤ max).
- Công thức điểm = `fuzzy(name+type+category+brand) + boost(color, brand) − loại theo budget/stock`.
- Áp dụng cho **cả `match()`** (có tên sản phẩm) **lẫn `suggest()`** (không tên, lọc theo thuộc tính).

---

## 3. Luật đã triển khai (R1–R7) ✅
| Luật | Tóm tắt | Nơi code |
|---|---|---|
| R1 | `ask_product_suggestion` + ≥1 thuộc tính → gợi ý ngay (giá sát budget, lọc type/color) | `matcher.suggest()` + pipeline F |
| R2 | Form xin thông tin khách (Tên/Sđt/Địa chỉ) | `reply.CUSTOMER_FORM` |
| R3 | Luồng chốt đơn: thiếu→hỏi · đủ→tóm tắt xác nhận · thanh toán · cảm ơn | `order_stage` A→D, `_order_summary` |
| R4 | `ask_order_wait_time`→2–3 ngày · `ask_shipping_fee`→20k | `reply` intent-driven |
| R5 | `ask_payment_method` → CK/COD | `PAYMENT_PROMPT` |
| R6 | Làm sạch entity | `state._clean_entity()` |
| R7 | `agree_order` không proposal → luồng chốt đơn (chỉ resolve proposal khi có lựa chọn tường minh) | pipeline B/D |

**Cần bổ sung cho R3 (góp ý m21):** tổng tiền = Σ(giá × số lượng) **+ phí ship** (+ gói quà nếu có).

---

## 4. Luật theo intent (session 2) — 🔶 chưa code

### 4.1 `ask_product_availability` (góp ý m5)
1. Đề xuất **top-3** sản phẩm khớp thông tin đã ghi nhận (tên + màu + budget...).
2. Khách xác nhận chọn 1 → kiểm tra **tồn kho** (mặc định 100):
   - `>0` → "Dạ mẫu này bên shop **còn hàng** ạ."
   - `=0` → gợi ý **top-3 sản phẩm liên quan còn hàng**.

### 4.2 `ask_find_product` (góp ý m6)
- Có mô tả → đề xuất **top-3** khớp nhất. Không có mô tả → hỏi thêm.
- Sau đề xuất, nếu khách `customer_reject` → "Dạ shop không có sản phẩm bạn tìm. Bạn có muốn tìm sản phẩm khác không ạ?"

### 4.3 `ask_product_info` (góp ý m16)
- Trả thông tin **`last_product`**: tên (PRODUCT_NAME), brand, category, number_pieces, price.
- Chưa có sản phẩm nào → hỏi lại tên mẫu.

### 4.4 `ask_legit` (góp ý m17)
- Trả **brand** của `last_product`. Chưa có → hỏi lại.

### 4.5 `ask_product_image` (góp ý m18)
- Trả ảnh `last_product` — DB chưa có ảnh → mô phỏng in `"[ảnh + <tên sản phẩm>]"`. Chưa có sản phẩm → hỏi lại.

### 4.6 `give_product` (góp ý m15, m19)
- Intent mơ hồ → tra **`last_intent`** gần nhất để trả lời đúng ngữ cảnh.
- Nếu message khớp sản phẩm đã có trong cart → cung cấp ảnh mẫu khớp nhất trong cart + hỏi "bạn có muốn xem ảnh sản phẩm khác không ạ?".

### 4.7 `ask_final_price` (góp ý m21)
- Trả **tổng = Σ(giá sản phẩm × số lượng) + phí ship** (+ gói quà nếu có).

### 4.8 `ask_gift_package` (góp ý m22)
- "Dạ shop có dịch vụ gói quà, phí **15k/sản phẩm**. Bạn có muốn dùng dịch vụ gói quà không ạ?"
- Khách đồng ý → **cộng phí gói quà vào hóa đơn**, cập nhật lại hóa đơn cuối.

### 4.9 `immediate_ship` (góp ý m20)
- Nhắc lại thông tin khách + sản phẩm + số lượng đã chốt. Thiếu → hỏi đến khi đủ.
- Đủ → "Đơn của bạn đã được ghi nhận và đợi người bán xác nhận. Đối với đơn giao hỏa tốc: Đặt trước 12h, nhận trước 20h cùng ngày; đặt sau đó, nhận trước 12h ngày hôm sau."

### 4.10 `ask_shop_info` / `get_product_direct` (góp ý m23)
- Cả hai → trả **thông tin shop** (mục 1).
- `get_product_direct` thêm:
  - Đã có `agree_order` trước (khách đã xác nhận đặt) → nhắc lại đơn (tên SP, số lượng, **hình thức: lấy trực tiếp**) + "shop giữ đơn cho bạn tối đa **2 ngày** kể từ ngày xác nhận. Mong gặp bạn sớm!"
  - Chưa `agree_order` → hỏi khách đã muốn chốt đặt chưa. Nếu có (`agree_order`) → làm như trên. Nếu chưa → "Shop còn có thể giải đáp thắc mắc nào khác của bạn nhỉ?"

### 4.11 `product_complaint` / `complain_shipping_issue` (đã ghi)
- "Rất xin lỗi vì bạn đã có trải nghiệm không tốt, hệ thống AI đã ghi nhận vấn đề của bạn và nhân viên sẽ hỗ trợ bạn sớm nhất có thể."

### 4.12 `buy_thanks` (góp ý m24)
- "Cảm ơn bạn đã tin tưởng shop ạ, giúp được bạn là vinh dự của shop."

---

## 5. Luồng thanh toán chi tiết (góp ý m13) 🔶
- **Chuyển khoản**: gửi mã QR (tạm in `"[qr chuyển khoản]"`) → yêu cầu khách **chụp bill gửi lại chat** → báo "đơn hàng đã được xác nhận, shop chuyển yêu cầu tới người bán đợi chấp thuận".
- **COD**: báo "đơn hàng đã được xác nhận".

## 6. Chủ động chốt đơn (góp ý m10) 🔶
- Khi **cart không trống** và khách vừa cung cấp **đủ** thông tin cá nhân (Tên/Sđt/Địa chỉ) → hỏi: "Bạn đã muốn chốt đặt hàng chưa, hay muốn tìm thêm sản phẩm khác ạ?"

## 7. Escalation khi không hiểu (góp ý m26) ✅
- **5 lượt liên tiếp** intent `other`/rỗng → "Dường như hệ thống chat AI đang trả lời không hiệu quả, đang rẽ hướng bạn đến nhân viên chăm sóc khách hàng. Vui lòng đợi phản hồi từ shop, xin lỗi và cảm ơn vì bạn đã thông cảm cho sự bất tiện này."
- Ngưỡng tăng từ 3 → 5 để tránh escalate quá sớm khi NLU gán sai intent cho câu chào ("alo", "shop ơi").

---

## 8. Hạn chế dữ liệu đã biết
- ~~TYPE tiếng Việt không khớp catalog~~ → **ĐÃ GIẢI QUYẾT**: field `type` là tiếng Việt, khớp trực tiếp (xem 2.4). Chỉ còn hạn chế khi mô tả thuần Việt **không ứng với type nào** (vd tên riêng tiếng Việt lạ) → name (Anh) lẫn type đều không khớp.
- DB **chưa có**: số lượng tồn kho, ảnh sản phẩm → dùng giá trị mô phỏng (tồn 100, "[ảnh ...]").
- NER hay tách mảnh entity (PRODUCT_COLOR "màu"/"xanh lá không", QUANTITY "ấy") — cần lọc giá trị hợp lệ (màu trong từ điển màu; QUANTITY chỉ nhận số).

---

## 9. Thứ tự triển khai đề xuất
1. **Cơ chế nền** (2.1 budget tr/k + đảo min/max; 2.2 last_product/last_intent/unknown_streak) — nền cho nhiều luật.
2. **Trả lời tĩnh theo intent**: 4.11, 4.12, 4.4, 4.3, 4.5, 4.7, 4.8, 4.10, escalation (7).
3. **Luồng có nhánh**: 4.1 availability+stock, 4.2 find+reject, 4.6 give_product, 4.9 immediate_ship, 5 payment chi tiết, 6 chủ động chốt.

---

## 10. ✅ Trạng thái triển khai (đã code & test — stub + model thật)
| Mục | Trạng thái | Nơi code |
|---|---|---|
| 2.1 budget tr/k + đảo min/max | ✅ | `parse_budget` (product_matcher); `state._fix_budget_order` |
| 2.2 last_product / last_intent / unknown_streak | ✅ | `state` (+ `matcher.get`); pipeline cập nhật mỗi lượt |
| 2.3 lọc color (từ điển) + quantity (chỉ số) | ✅ | `state._extract_color`, guard QUANTITY |
| 2.4 matcher field-aware (name+type fuzzy, color/budget filter) | ✅ | `product_matcher` `_primary`(name+type) + `suggest()` dùng type |
| 4.1 availability → propose → tồn kho (mặc định còn) | ✅ | pipeline D (origin=availability → action `availability`) |
| 4.2 find + customer_reject | ✅ | pipeline C `find_reject` |
| 4.3/4.4/4.5 info / legit / image (last_product) | ✅ | pipeline H + reply |
| 4.6 give_product → mượn last_intent | ✅ | pipeline `eff` intents |
| 4.7 final_price = hàng + ship (+gói quà) | ✅ | reply `_final_total` |
| 4.8 gift_package (offer → cộng phí → cập nhật hoá đơn) | ✅ | pipeline A/H + `gift_wrap` |
| 4.9 immediate_ship (đủ→text hỏa tốc; thiếu→hỏi) | ✅ | pipeline H |
| 4.10 shop_info / get_product_direct (recap pickup) | ✅ | pipeline H |
| 4.11 complaint / complain_shipping_issue | ✅ | reply `COMPLAINT` |
| 4.12 buy_thanks | ✅ | reply `BUY_THANKS` |
| 5 payment chi tiết (CK→QR+bill / COD) | ✅ | reply `order_done` theo `payment` |
| 6 chủ động hỏi chốt (đủ info + cart) | ✅ | pipeline I `ask_finalize` |
| 7 escalation 3 lượt other/null | ✅ | pipeline J `unknown_streak` |

**Ghi chú khi code:**
- Khi intent hỏi sản phẩm mà **không có PRODUCT_NAME** (vd "rồng" bị gán TYPE) → fallback **suggest theo type/color** rồi mới propose (pipeline E).
- `last_product` ưu tiên món vừa chốt (cart) / vừa đề xuất gần nhất (set trong `add_confirmed_product` & `add_proposal`).
- Tồn kho mặc định **còn hàng** (chưa có cột số lượng trong DB).
- Hạn chế còn lại: mô tả thuần Việt không ứng type nào (vd tên riêng VN) vẫn không match.

## 12. Bugfix sau phân tích kẽ hở (2026-06-16) ✅

| Bug | Mô tả | Fix |
|---|---|---|
| D-price-to-cart | `ask_product_price` + chọn số từ proposal → sản phẩm tự thêm vào cart | Branch D: thêm nhánh `ask_product_price` → `product_info`, KHÔNG thêm cart |
| no-cancel-path | Không có đường thoát khỏi `await_info`/`await_confirm`/`await_pickup` | Nhánh A.5: `customer_reject` trong order_stage → `order_cancel` + reset |
| ghost-order | Sau `done`, khách chào lại → đơn cũ tích lũy | Nhánh A.0: `greeting` + `order_stage='done'` → reset giỏ + stage, giữ customer info |
| double-confirm | `ask_finalize` → khách đồng ý → `order_summary` → khách đồng ý lại | `ask_finalize` hiển thị summary inline; A.3 nhận `agree` → thẳng vào `await_payment` |
| qty-validation | QUANTITY nhận 0, âm, >100 | `state.update_entities`: reject qty ≤ 0 hoặc > 100 |
| pending-gift-reset | (FALSE POSITIVE) | Code đã có `pending_gift = False` unconditional ở line 81 — không cần fix |
| escalate-too-fast | 3 lượt `other` → escalate quá sớm với câu chào ngắn | Ngưỡng tăng 3 → 5 |

## 11. Tinh chỉnh sau test session 3 ✅
- **Info intent ưu tiên hơn chốt đơn** (m3, m6): `ask_product_image`/`ask_product_info`/`ask_legit` + "mẫu N" → **resolve mẫu N rồi TRẢ INFO/ẢNH**, KHÔNG thêm vào cart, KHÔNG báo "còn hàng". (pipeline B2, trước nhánh confirm).
- **`agree_order` không browse-suggest** (m7, m10): chỉ propose khi có tên sản phẩm; `agree_order` không tên → đi luồng chốt đơn (không liệt kê lại list). (`_SUGGEST_INTENTS` bỏ agree_order).
- **`get_product_direct`** (m9, m13, m14): nếu **đã từng `agree_order`** (`state.agreed`) + có cart → **recap lấy trực tiếp** ("giữ đơn 2 ngày..."); chưa agreed → hỏi "đã muốn chốt lấy trực tiếp chưa?" + đặt `order_stage='await_pickup'`; lượt sau khách đồng ý → recap (hết vòng lặp hỏi lại).
- Thêm `state.agreed` (bật khi gặp `agree_order` bất kỳ lúc nào).

## 13. Bugfix sau phân tích log thực (2026-06-16) ✅

Nguồn: `logs/chat_20260616.jsonl`, 2 session, 39 lượt hội thoại.

| Bug | Root cause xác nhận | Fix |
|---|---|---|
| **Ghost recap** (`gift_added` với giỏ trống) | Branch D `availability` không thêm cart; không có path nào cho khách xác nhận mua sau đó | Thêm `state.pending_availability_product`; đầu mỗi lượt: nếu affirmative/agree_order/add_product → `add_confirmed_product()` tự động (A.1) |
| **A.5 xóa cart tức thì** khi `customer_reject` giữa luồng | NLU có thể nhầm câu xác nhận ("mình vẫn lấy trực tiếp") thành `customer_reject`; A.5 xóa cart ngay không xác nhận | A.5: set `pending_cancel=True` + hỏi (`ask_cancel_or_browse`) trước, A.4 mới thực sự xóa nếu khách xác nhận |
| **Bỏ qua câu hỏi hỏa tốc** khi đang `pending_finalize` | A.3 match `_affirmative()` (khớp "chốt") → nhảy thẳng `order_payment`, bỏ qua `immediate_ship` đồng hành | A.3: khi affirmative + `immediate_ship` → gán `payload={'immediate_ship':True}`; `reply.order_payment` ghép `IMMEDIATE_SHIP` vào đầu |
| **Spam gợi ý cũ** khi khách hỏi tên sản phẩm cụ thể | `ask_product_suggestion` không có trong `_PROPOSE_INTENTS` → Branch E bỏ qua product_query, dùng attribute cũ | Thêm `'ask_product_suggestion'` vào `_PROPOSE_INTENTS` |
| **Nhầm "số N" chọn mẫu → QUANTITY/MIN_BUDGET** | NER gán digit của lựa chọn thành entity số lượng/ngân sách | Heuristic filter trước `update_entities()`: khi đang resolve proposal + `has_explicit_choice()` + entity trùng digit chọn → bỏ entity đó |
| **Từ đệm ("thôi/thui/nhen/hay") → PRODUCT_NAME** | NER bắt filler word thành tên SP → E tìm sản phẩm "thôi" | Stoplist `_FILLER_WORDS`; lọc khi build `product_query` |

**Không cần fix:**
- `PRODUCT_COLOR = ["hay"]` — `_extract_color()` đã lọc đúng qua `COLOR_VOCAB` whitelist (false positive, đã đúng từ đầu).

---

## 14. Bugfix Round 4 — log 2026-06-16 16:31 (✅ đã code)

Nguồn: `logs/chat_20260616.jsonl`, session từ 16:31, 23 lượt (m6–m28).

| # | Bug | Root cause | Fix (pipeline branch) |
|---|---|---|---|
| **1** | Vòng lặp vô tận sau `order_done` (m25–m28): stage=done nhưng `ask_payment_method` → branch G kích hoạt lại `order_payment` | Không có guard chặn pipeline khi stage=done | **A.0-done**: ngay sau A.0, nếu `stage == 'done'` → `action = 'order_done_reminder'`; tất cả branch dưới bị bỏ qua. Reply phân biệt: chuyển khoản → nhắc gửi bill; COD → thông báo chờ shop |
| **2** | "cho mình 2 bộ đó đi" ở `await_confirm` → nhảy thẳng sang payment mà không cập nhật lại tóm tắt đơn (m23) | Branch B (await_confirm + affirm) không kiểm tra QUANTITY mới trước khi chuyển stage | **B (await_confirm)**: kiểm tra `any(h['label']=='QUANTITY' and h['turn']==s.turn for h in s.history)` — nếu có → `action=order_summary` lại (stage vẫn `await_confirm`); chỉ chuyển sang payment khi không có QUANTITY update |
| **3** | "oke" → fallback (m21): intent `agree`, stage=None, cart=1, pending_proposal=None → không branch nào bắt | Branch F chỉ check `'agree_order' in intents`, bỏ sót intent `agree` và regex affirmative | **F**: điều kiện mở rộng thành `'agree_order' in intents OR (_affirmative() AND s.cart AND s.order_stage is None)` → "oke/ok/được/chốt" khi có cart + không đang trong luồng nào = tiến vào flow order |
| **4** | Chọn "số 3" từ danh sách GỢI Ý (suggest) → tự động add cart chưa hỏi xác nhận (m8) | Branch D không phân biệt proposal từ `suggest` vs `propose`; cả 2 đều auto-add cart | **state.py**: `add_proposal()` lưu thêm `origin_action`; **D**: nếu `origin_action=='suggest'` và không có `agree_order`/`add_product` → `action=suggest_confirm` + set `pending_availability_product` (chờ affirm ở A.1). Reply `suggest_confirm` = thông tin ngắn + hỏi "thêm vào giỏ không?". Propose flow (search cụ thể) giữ nguyên auto-add |
