# Dialogue Rules

Specification for `pipeline.py`, `reply.py`, `state.py`, and `product_matcher.py`.
All rules listed here are fully implemented.

## 1. Business Constants

| Item | Value |
|---|---|
| Shipping fee | 20.000đ toàn quốc |
| Standard delivery | 2–3 ngày |
| Express delivery | Đặt trước 12h -> nhận trước 20h cùng ngày; đặt sau 12h -> nhận trước 12h hôm sau |
| Gift wrapping | 15.000đ/sản phẩm |
| Default stock | 100 (all products; no inventory column in DB) |
| Payment methods | Chuyển khoản trước / COD |
| Shop address | Số 30 ngõ 20 đường xxx, SĐT 08689xxxxx, tiếp khách 8h–18h |
| Hold period (pickup) | Tối đa 2 ngày từ ngày xác nhận |

## 2. State Mechanisms

### 2.1 Budget parsing

- `k` / `nghìn` / `ngàn` -> x 1.000 (100k = 100.000).
- `tr` / `triệu` -> x 1.000.000 (1tr = 1.000.000).
- If MIN_BUDGET > MAX_BUDGET after update, the two values are swapped automatically.

### 2.2 Context tracking

- `last_product_id`: most recently confirmed or proposed product; used for `ask_product_info`, `ask_legit`, `ask_product_image`, and `give_product`.
- `last_intent`: last meaningful intent (excludes `give_product`, `other`, `agree`); used when `give_product` appears to inherit the previous context.
- `unknown_streak`: count of consecutive turns where no meaningful intent was found; resets to 0 whenever a meaningful intent is processed; triggers escalation at 5.

### 2.3 Entity cleaning and validation

- All entity values are stripped of trailing punctuation.
- PHONE: non-digit characters removed, leaving digits only.
- QUANTITY: rejected if the value contains no digit; rejected if parsed integer <= 0 or > 100.
- PRODUCT_COLOR: only values matching `COLOR_VOCAB` whitelist are accepted (e.g. `xanh dương`, `đỏ`, `vàng`); NER noise is discarded.
- PRODUCT_NAME: filler words (`nhé`, `thôi`, `dạ`, `hay`, `đó`, `này`, etc.) are excluded from the product query string.

### 2.4 Field-aware product matching

Products are matched using a hybrid field-aware approach rather than a single concatenated blob:

- **Fuzzy text score**: applied to the concatenated `name + type + category + brand` fields (token-set ratio). The Vietnamese `type` field values (Xe hơi, Rồng, Máy bay, Tàu, Robot, Lâu đài, Khủng long, etc.) are included so Vietnamese queries match directly.
- **Color boost**: a match between the customer's stated colors and the product's color list adds a score bonus. Colors are not mixed into the fuzzy text score.
- **Budget filter**: products priced above MAX_BUDGET are excluded.

The same scoring logic applies to both `match()` (query has a product name) and `suggest()` (query has attributes only, no name).

## 3. Rules

### R1 — Product suggestion by attributes

When `ask_product_suggestion` arrives with at least one attribute (budget, type, or color), return top-3 products ranked by field-aware score. If no attributes are available, ask the customer for budget and preferences first.

### R2 — Customer information form

Required fields for order finalization: NAME, PHONE, ADDRESS. Missing fields are requested using a structured form. Field values are updated incrementally across turns in `await_info` until complete.

### R3 — Order flow

A complete order requires a non-empty cart plus NAME, PHONE, and ADDRESS. The stages are:

1. `agree_order` with cart + all fields -> `await_confirm` + `order_summary`.
2. Customer confirms -> `await_payment` + payment prompt (chuyển khoản / COD).
3. Customer selects payment method -> `done` + `order_done`.

When `agree_order` arrives with missing fields -> `await_info`; the bot asks for each missing field until the information is complete, then transitions to `await_confirm`.

### R4 — Delivery and shipping queries

- `ask_order_wait_time` -> giao toàn quốc 2–3 ngày.
- `ask_shipping_fee` -> phí ship 20.000đ.
- `immediate_ship` with complete order info -> express delivery terms (đặt trước/sau 12h).
- `immediate_ship` with missing info -> ask for missing fields first.

### R5 — Payment method

`ask_payment_method` -> present both options (chuyển khoản / COD). COD confirmation closes the order immediately. Bank transfer shows a QR code placeholder and asks the customer to send a payment screenshot.

### R6 — Entity cleaning

See Section 2.3.

### R7 — agree_order without pending proposal

`agree_order` when no unresolved proposal exists enters the order flow (R3), not a product search. The `agreed` flag is set permanently and used to differentiate pickup order confirmation from browsing.

## 4. Intent Handlers

### ask_product_availability

1. Propose top-3 matching products.
2. Customer selects one -> report stock status (default: còn hàng) and ask whether to order.
3. Customer affirms -> product is added to cart automatically (Branch A.1).

### ask_find_product

- With a product name or type -> propose top-3 matching products.
- Without any description -> ask for more details.
- Customer rejects the proposal -> `find_reject`.

### ask_product_info

Return name, brand, category (dòng), number of pieces, and price for `last_product`. If no product is tracked, ask for the product name.

### ask_legit

Return the brand of `last_product` to confirm authenticity. If no product is tracked, ask for the product name.

### ask_product_image

Return a simulated image reference for `last_product` (DB has no image URLs — reply is `[ảnh <tên sản phẩm>]`). If no product is tracked, ask for the product name. Handled in Branch B2 so it never triggers an add-to-cart.

### give_product

Ambiguous intent — borrows the most recent `last_intent` to determine the correct response. `give_product` is never stored as `last_intent`.

### ask_final_price

Total = sum(giá sản phẩm) x số lượng + 20.000đ ship + (15.000đ/SP nếu gói quà).

### ask_gift_package

Introduce gift wrapping at 15.000đ/sản phẩm. Set `pending_gift=True` and wait for customer yes/no. On confirmation, add the charge to the order total.

### immediate_ship

If cart is not empty and all customer info is available, provide express delivery terms. Otherwise ask for the missing information first.

### ask_shop_info / get_product_direct

`ask_shop_info` -> return address, phone number, and walk-in hours.

`get_product_direct`:
- Cart not empty AND `agreed=True` -> confirm pickup order, set `order_stage='done'` (`get_direct_recap`; shop holds the order 2 days).
- Cart not empty AND not yet agreed -> ask whether to finalize for pickup (`order_stage='await_pickup'`).
- Cart empty -> fall back to shop info only.

### product_complaint / complain_shipping_issue

Return apology and notify the customer that a human agent will follow up.

### buy_thanks

Return a purchase thank-you message.

## 5. Payment Flow

- **Chuyển khoản**: display QR code placeholder -> ask customer to send a payment screenshot. Order stage: `done`; waiting for shop confirmation.
- **COD**: confirm order immediately. Order stage: `done`.

Both paths trigger `save_order()` which writes the order to SQLite exactly once (guarded by `order_done_persisted` on the session).

## 6. Proactive Order Finalization

When the customer provides personal information (`provide_cus_inf`) and the cart is non-empty, all required fields are satisfied, and no order stage is active, the bot proactively asks whether to finalize or keep browsing (`ask_finalize`). The reply includes an inline order summary to avoid a redundant confirmation step. Sets `pending_finalize=True`.

## 7. Escalation

Five consecutive turns with no recognized intent (or only `other`) trigger the escalation reply, which informs the customer that a human agent will respond. The streak counter resets whenever the bot processes any meaningful intent.

## 8. Known Limitations

- Product type queries using Vietnamese text that does not match any `type` field value (e.g. unusual proper names) will not match any product via the type channel; the name fuzzy-match may still find something.
- The database has no stock quantity column; all products are assumed in stock.
- The database has no product image URLs; image replies are simulated as `[ảnh <tên sản phẩm>]`.
- NER occasionally mis-tags the digit in a selection message (e.g. "số 2") as QUANTITY or budget. A heuristic filter in the pipeline removes these when an explicit proposal choice is detected in the same turn.
