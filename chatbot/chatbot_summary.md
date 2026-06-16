# Tóm tắt Chatbot LEGO Shop — States, Rules & Reply Cases

---

## 1. Biến trạng thái (SessionState)

| Biến | Kiểu | Ý nghĩa |
|---|---|---|
| `order_stage` | `None \| 'await_info' \| 'await_confirm' \| 'await_payment' \| 'await_pickup' \| 'done'` | Giai đoạn luồng chốt đơn |
| `cart` | list | Sản phẩm đã xác nhận (chỉ vào sau khi confirm) |
| `customer` | dict | Thông tin khách: NAME, PHONE, ADDRESS, CITY |
| `order` | dict | Slots đơn hàng: MAX_BUDGET, MIN_BUDGET, QUANTITY, SHIP_DATE, SHIP_TIME, TYPE, COMPLEXITY, PRODUCT_COLOR (list) |
| `proposals` | dict | msg_id → danh sách ứng viên sản phẩm đề xuất (resolved/unresolved) |
| `pending_gift` | bool | Đang chờ khách trả lời có/không về gói quà |
| `gift_wrap` | bool | Khách đã chọn gói quà |
| `agreed` | bool | Khách đã `agree_order` ít nhất một lần trong session |
| `last_product_id` | str\|None | Sản phẩm đề xuất/xác nhận gần nhất (dùng cho info/image/legit) |
| `last_intent` | str\|None | Intent có nghĩa gần nhất (dùng cho `give_product`) |
| `pending_finalize` | bool | `ask_finalize` đã gửi, chờ khách trả lời chốt hay tìm thêm |
| `pending_cancel` | bool | `ask_cancel_or_browse` đã gửi, chờ khách xác nhận hủy hay tiếp tục |
| `unknown_streak` | int | Số lượt liên tiếp không hiểu được (≥5 → escalate) |
| `payment` | str\|None | `'chuyển khoản'` \| `'COD'` |

### Luồng `order_stage`

```
None
 └─ agree_order (có cart + đủ info)  → await_confirm
 └─ agree_order (có cart + thiếu info) → await_info
 └─ agree_order (giỏ trống)          → [order_empty_cart, về None]

await_info
 └─ info đủ (sau update entities)    → await_confirm
 └─ customer_reject                  → None + cart=[] (order_cancel) ← A.5

await_confirm
 └─ khách đồng ý (affirmative)       → await_payment
 └─ customer_reject                  → None + cart=[] (order_cancel) ← A.5

await_payment
 └─ chọn CK/COD                      → done

await_pickup  (get_product_direct, chưa agreed)
 └─ khách đồng ý                     → done (get_direct_recap)
 └─ customer_reject                  → None + cart=[] (order_cancel) ← A.5

done
 └─ greeting                         → None + cart reset (giữ customer info) ← A.0
```

---

## 2. Hằng số nghiệp vụ

| Mục | Giá trị |
|---|---|
| Phí ship | 20.000đ toàn quốc |
| Giao thường | 2–3 ngày |
| Giao hỏa tốc | Trước 12h → nhận trước 20h cùng ngày; sau 12h → trước 12h hôm sau |
| Gói quà | 15.000đ/sản phẩm |
| Tồn kho mặc định | 100 (mọi sản phẩm) |
| Thanh toán | Chuyển khoản trước / COD |
| Thông tin shop | Số 30 ngõ 20 đường xxx, SĐT 08689xxxxx, tiếp khách 8h–18h |
| Giữ đơn (lấy trực tiếp) | Tối đa 2 ngày từ ngày xác nhận |

---

## 3. Bảng tất cả Reply (action) và điều kiện kích hoạt

### Nhánh A.0 — Reset đơn cũ khi chào mới

| Reply | Điều kiện |
|---|---|
| *(reset cart + order_stage + payment + agreed; giữ customer info)* | `order_stage='done'` **VÀ** intent `greeting` |

### Nhánh A — Chờ xác nhận gói quà

| Reply | Điều kiện |
|---|---|
| `gift_added` + order summary | `pending_gift=True` **VÀ** khách affirmative (agree/ok/đồng ý...) |
| *(bỏ qua — `pending_gift` được reset về False unconditionally trước khi check)* | `pending_gift=True` **VÀ** khách KHÔNG đồng ý |

### Nhánh A.3 — Chờ phản hồi chốt đơn (pending_finalize)

> `pending_finalize` luôn được reset về `False` ở dòng đầu block — unconditional, bất kể intent nào.

| Reply | Điều kiện |
|---|---|
| **`order_payment`** | `pending_finalize=True` **VÀ** affirmative / `agree_order` → `order_stage='await_payment'` |
| **`ask_cancel_or_browse`** (hỏi lại ý định) | `pending_finalize=True` **VÀ** `customer_reject` **VÀ** cart không trống → `pending_cancel=True` |
| *(tiếp tục tư vấn)* | `pending_finalize=True` **VÀ** `customer_reject` **VÀ** cart trống |

### Nhánh A.4 — Xác nhận hủy giỏ

| Reply | Điều kiện |
|---|---|
| **`order_cancel`** + reset cart + agreed | `pending_cancel=True` **VÀ** `customer_reject` → xác nhận hủy hẳn |
| *(giữ giỏ, tiếp tục tư vấn)* | `pending_cancel=True` **VÀ** bất kỳ intent nào khác → khách muốn tìm thêm |

### Nhánh A.5 — Hủy đơn trong luồng chốt

| Reply | Điều kiện |
|---|---|
| **`order_cancel`** ("shop đã hủy đơn...") | `order_stage ∈ {await_info, await_confirm, await_pickup}` **VÀ** `customer_reject` → `order_stage=None`, `cart=[]`, **`agreed=False`** |

---

### Nhánh B — Tiếp nối order_stage

| Reply | Điều kiện |
|---|---|
| **`order_done`** (CK: QR+bill / COD: xác nhận) | `order_stage='await_payment'` **VÀ** phát hiện lựa chọn CK hoặc COD |
| **`order_payment`** (hỏi lại CK/COD) | `order_stage='await_payment'` **VÀ** không nhận ra lựa chọn |
| **`order_payment`** (chuyển sang chọn thanh toán) | `order_stage='await_confirm'` **VÀ** khách affirmative |
| **`order_summary`** (tóm tắt đơn) | `order_stage='await_info'` **VÀ** `missing_for_order()` rỗng (đủ thông tin) |
| **`order_need_info`** (hỏi thêm thông tin thiếu) | `order_stage='await_info'` **VÀ** vẫn thiếu thông tin **VÀ** (intent `provide_cus_inf` HOẶC có entities) |
| **`get_direct_recap`** (recap lấy trực tiếp) | `order_stage='await_pickup'` **VÀ** khách affirmative |

---

### Nhánh B2 — Hỏi thông tin/ảnh/xác thực sản phẩm (INFO)

> Ưu tiên cao hơn confirm/cart — KHÔNG thêm sản phẩm vào giỏ.

| Reply | Điều kiện |
|---|---|
| **`product_info`** (tên, hãng, dòng, số mảnh, giá) | Intent `ask_product_info` **VÀ** `last_product` tồn tại |
| **`legit`** (xác nhận hãng chính hãng) | Intent `ask_legit` **VÀ** `last_product` tồn tại |
| **`product_image`** (`[ảnh <tên>]`) | Intent `ask_product_image` **VÀ** `last_product` tồn tại |
| **`need_product`** (hỏi tên mẫu) | Bất kỳ intent INFO (`ask_product_info`/`ask_legit`/`ask_product_image`) **VÀ** chưa có `last_product` |
| *(tự động cập nhật `last_product_id`)* | Khách reply_to một msg đề xuất **VÀ** có lựa chọn tường minh (số thứ tự) → resolve chọn mẫu đó trước khi trả info |

---

### Nhánh C — Khách từ chối sau đề xuất

| Reply | Điều kiện |
|---|---|
| **`find_reject`** ("shop không có SP bạn tìm...") | Intent `customer_reject` **VÀ** đang có pending_proposal chưa resolve |

---

### Nhánh D — Xác nhận chọn sản phẩm từ đề xuất

| Reply | Điều kiện |
|---|---|
| **`availability`** ("còn hàng, bạn có muốn đặt không?") | Có chọn rõ (số thứ tự) từ proposal **VÀ** proposal gốc có origin `ask_product_availability` **VÀ** KHÔNG có intent `agree_order`/`add_product` |
| **`product_info`** (thông tin/giá) | Có chọn rõ từ proposal **VÀ** intent `ask_product_price` **VÀ** KHÔNG có `agree_order`/`add_product` → cập nhật `last_product`, **KHÔNG** thêm cart |
| **`confirmed`** (chốt + hiển thị form nếu thiếu thông tin) | Có chọn rõ (số thứ tự) từ proposal **VÀ** intent là mua hàng (`add_product`/`agree_order`) hoặc không phải info/price query → sản phẩm vào cart |
| **`reask_choice`** (hỏi lại số mấy) | Có proposal nhưng lựa chọn không rõ ràng (ambiguous) |

> Nguồn proposal: (1) `reply_to_msg_id` trỏ đúng msg đề xuất, hoặc (2) pending_proposal gần nhất + `has_explicit_choice(message)`.

---

### Nhánh E — Tìm kiếm sản phẩm mới

| Reply | Điều kiện |
|---|---|
| **`propose`** (top-3 SP theo tên) | Có `PRODUCT_NAME` trong entities **VÀ** intent thuộc `_PROPOSE_INTENTS` (`ask_product_price`, `ask_product_availability`, `ask_product_image`, `ask_find_product`, `add_product`, `agree_order`) |
| **`propose`** (top-3 SP theo thuộc tính) | KHÔNG có tên SP **VÀ** có type/color **VÀ** intent thuộc `_SUGGEST_INTENTS` (`ask_product_availability`, `ask_find_product`, `ask_product_suggestion`) |

---

### Nhánh F — agree_order (R3/R7)

| Reply | Điều kiện |
|---|---|
| **`order_empty_cart`** ("chưa chọn SP nào") | Intent `agree_order` **VÀ** cart trống |
| **`order_need_info`** (hỏi thông tin thiếu) | Intent `agree_order` **VÀ** có cart **VÀ** thiếu NAME/PHONE/ADDRESS → `order_stage='await_info'` |
| **`order_summary`** (tóm tắt xác nhận) | Intent `agree_order` **VÀ** có cart **VÀ** đủ thông tin → `order_stage='await_confirm'` |

---

### Nhánh G — Thanh toán / gợi ý

| Reply | Điều kiện |
|---|---|
| **`order_payment`** (CK/COD) | Intent `ask_payment_method` (không có action trước) |
| **`suggest`** (gợi ý theo thuộc tính) | Intent `ask_product_suggestion` **VÀ** đã có budget/màu/type **VÀ** chưa có action |

---

### Nhánh H — Hỏi đáp về đơn hàng / shop

| Reply | Điều kiện |
|---|---|
| **`final_price`** (tổng = hàng + ship + gói quà) | Intent `ask_final_price` **VÀ** cart không trống |
| **`gift_offer`** (giới thiệu gói quà 15k/SP) | Intent `ask_gift_package` → đặt `pending_gift=True` |
| **`complaint`** (xin lỗi + chuyển nhân viên) | Intent `product_complaint` HOẶC `complain_shipping_issue` |
| **`buy_thanks`** ("cảm ơn bạn tin tưởng shop") | Intent `buy_thanks` |
| **`immediate_ship`** (thông tin giao hỏa tốc) | Intent `immediate_ship` **VÀ** có cart **VÀ** đủ thông tin khách |
| **`immediate_ship_need`** (hỏi thông tin thiếu để giao hỏa tốc) | Intent `immediate_ship` **VÀ** (giỏ trống HOẶC thiếu thông tin khách) |
| **`get_direct_recap`** (recap lấy trực tiếp, giữ đơn 2 ngày) | Intent `get_product_direct` (từ `give_product` HOẶC trực tiếp) **VÀ** `state.agreed=True` **VÀ** có cart → `order_stage='done'` |
| **`get_direct_ask`** (hỏi đã muốn lấy trực tiếp chưa?) | Intent `get_product_direct` **VÀ** chưa `agreed` **VÀ** có cart → `order_stage='await_pickup'` |
| **`shop_info`** (địa chỉ, SĐT, giờ mở cửa) | Intent `ask_shop_info` **HOẶC** intent `get_product_direct` với giỏ trống |

---

### Nhánh I — Chủ động chốt đơn (§6)

| Reply | Điều kiện |
|---|---|
| **`ask_finalize`** (hiển thị order summary inline + hỏi chốt hay tìm thêm) | Intent `provide_cus_inf` **VÀ** cart không trống **VÀ** `missing_for_order()` rỗng **VÀ** `order_stage is None` → `pending_finalize=True` |

---

### Nhánh J — Escalation (§7)

| Reply | Điều kiện |
|---|---|
| **`escalate`** (chuyển nhân viên CSKH) | `unknown_streak >= 5` (5 lượt liên tiếp không có intent hoặc chỉ có intent `other`) |

> `unknown_streak` reset về 0 mỗi lượt bot xử lý được intent có nghĩa.

---

### Replies theo intent (không cần action từ pipeline)

| Reply | Điều kiện |
|---|---|
| `"Dạ đơn giao trong 2–3 ngày ạ."` | Intent `ask_order_wait_time` (không có action trước) |
| `"Dạ phí ship 20.000đ toàn quốc ạ."` | Intent `ask_shipping_fee` (không có action trước) |
| PAYMENT_PROMPT | Intent `ask_payment_method` (không có action trước) |
| `"Dạ shop đã ghi nhận thông tin ạ."` | Intent `provide_cus_inf` (không có action trước) |
| `"Dạ shop nghe ạ, bạn cần tư vấn mẫu LEGO nào ạ?"` | Intent `greeting` |
| `"Dạ cảm ơn bạn, hẹn gặp lại ạ!"` | Intent `Goodbye` |
| `"Bạn cho shop xin tầm giá và sở thích..."` | Intent `ask_product_suggestion` HOẶC `provide_budget` (không có thuộc tính nào để suggest) |
| `"Bạn cho shop xin tên mẫu bạn quan tâm..."` | Intent `ask_product_price` / `ask_product_availability` / `ask_product_image` / `ask_find_product` (không có product_query, không có action trước) |

---

### Fallback (không có nhánh nào khớp)

| Reply | Điều kiện |
|---|---|
| `"Dạ shop chưa rõ ý bạn lắm, bạn nói rõ hơn giúp shop ạ?"` | Tất cả nhánh A–J và intent-driven đều không khớp |

---

## 4. Intent → nhóm xử lý

| Intent | Nhánh chính |
|---|---|
| `agree` / `agree_order` | D (resolve proposal) → F (chốt đơn) |
| `add_product` | E (propose) → D (confirm) |
| `ask_product_price` | E (propose) → intent-default |
| `ask_product_availability` | E (propose) / D (availability) |
| `ask_product_image` | B2 (image) → E (propose) |
| `ask_product_info` | B2 (info) |
| `ask_legit` | B2 (legit) |
| `ask_find_product` | E (propose/suggest) → C (reject) |
| `ask_product_suggestion` | G (suggest) / E (suggest) / intent-default |
| `provide_budget` | intent-default (hỏi thêm) |
| `ask_payment_method` | G (payment) → intent-default |
| `ask_order_wait_time` | intent-default |
| `ask_shipping_fee` | intent-default |
| `ask_final_price` | H (final_price) |
| `ask_gift_package` | H (gift_offer) |
| `immediate_ship` | H (immediate_ship / immediate_ship_need) |
| `get_product_direct` | H (get_direct_recap / get_direct_ask / shop_info) |
| `ask_shop_info` | H (shop_info) |
| `product_complaint` / `complain_shipping_issue` | H (complaint) |
| `buy_thanks` | H (buy_thanks) |
| `customer_reject` | C (find_reject) |
| `give_product` | mượn `last_intent` → xử lý theo intent đó |
| `provide_cus_inf` | B (update info) → I (ask_finalize) → intent-default |
| `greeting` | intent-default |
| `Goodbye` | intent-default |
| `other` / rỗng | J (đếm streak → escalate) |

---

## 5. Luật phụ quan trọng

- **give_product (§4.6)**: mơ hồ → mượn `last_intent` gần nhất để xử lý, KHÔNG dùng `give_product` làm `last_intent`.
- **Budget swap (§2.1)**: nếu MIN_BUDGET > MAX_BUDGET → tự đảo.
- **PRODUCT_COLOR**: chỉ nhận màu trong từ điển (`COLOR_VOCAB`), bỏ NER noise.
- **QUANTITY**: chỉ nhận nếu có chữ số, bỏ "ấy" hay các từ trống.
- **Info intent ưu tiên chốt đơn**: B2 chạy trước D → `ask_product_image`/`ask_product_info`/`ask_legit` KHÔNG thêm vào cart dù đang có pending proposal.
- **`agreed` flag**: bật một lần vĩnh viễn khi khách `agree_order` bất cứ lúc nào.
- **Tổng đơn** = Σ(giá SP) + phí ship 20k (+ 15k/SP nếu gói quà).


