# Chatbot State Machine Reference

Documents session state, business constants, order stage transitions, and all dialogue actions.
Applies to both Hybrid mode (`pipeline.py` / `reply.py`) and LLM mode (`llm_baseline.py`).

## 1. Session State Variables

| Variable | Type | Description |
|---|---|---|
| `order_stage` | `None \| 'await_info' \| 'await_confirm' \| 'await_payment' \| 'await_pickup' \| 'done'` | Current stage of the order flow |
| `cart` | list | Confirmed products (added only after explicit selection or LLM extraction) |
| `customer` | dict | Customer slots: NAME, PHONE, ADDRESS, CITY |
| `order` | dict | Order slots: MAX_BUDGET, MIN_BUDGET, QUANTITY, SHIP_DATE, SHIP_TIME, TYPE, COMPLEXITY, PRODUCT_COLOR (list) |
| `proposals` | dict | msg_id to candidate list with resolved/unresolved status |
| `pending_gift` | bool | Waiting for customer yes/no on gift wrapping offer |
| `gift_wrap` | bool | Customer selected gift wrapping |
| `agreed` | bool | Customer has issued `agree_order` at least once in this session |
| `last_product_id` | str\|None | Most recently proposed or confirmed product (used for info/image/legit queries) |
| `last_intent` | str\|None | Last meaningful intent (used for `give_product` context inheritance) |
| `pending_finalize` | bool | `ask_finalize` was sent; waiting for customer to confirm order or keep browsing |
| `pending_cancel` | bool | `ask_cancel_or_browse` was sent; waiting for customer to confirm cancellation |
| `pending_availability_product` | dict\|None | Product shown as in-stock, awaiting purchase confirmation |
| `unknown_streak` | int | Consecutive turns with no recognized intent; triggers escalation at 5 |
| `payment` | str\|None | `'chuyển khoản'` \| `'COD'` |
| `order_done_persisted` | bool | True after the completed order has been written to the database |
| `conversation_history` | list | `[{role, content}]` maintained for LLM mode context |

### Order Stage Transitions

```
None
 |-- agree_order, cart not empty, info complete    --> await_confirm
 |-- agree_order, cart not empty, info missing     --> await_info
 |-- agree_order, cart empty                       --> [order_empty_cart, stay None]

await_info
 |-- all required fields received                  --> await_confirm
 |-- customer_reject                               --> None + cart cleared (order_cancel)

await_confirm
 |-- customer affirms                              --> await_payment
 |-- customer_reject                               --> None + cart cleared (order_cancel)

await_payment
 |-- 'chuyển khoản' or 'COD' selected             --> done

await_pickup  (get_product_direct path)
 |-- customer affirms                              --> done (get_direct_recap)
 |-- customer_reject                               --> None + cart cleared (order_cancel)

done
 |-- greeting                                      --> None + cart reset (customer info kept)
```

## 2. Business Constants

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

## 3. Dialogue Branches (Hybrid Mode)

### Branch A.0 - Reset completed order on new greeting

| Trigger | Effect |
|---|---|
| `order_stage='done'` AND intent `greeting` | Reset cart, order_stage, payment, agreed; keep customer info |

### Branch A.0-done - Lock all branches once order is complete

| Reply | Trigger |
|---|---|
| `order_done_reminder` | `action is None` AND `order_stage='done'` — blocks all branches below |

### Branch A.1 - Auto-confirm a stock-checked product

| Effect | Trigger |
|---|---|
| Add `pending_availability_product` to cart | `pending_availability_product` set AND customer affirms / `agree_order` / `add_product` |

### Branch A - Gift offer pending

| Reply | Trigger |
|---|---|
| `gift_added` + order summary | `pending_gift=True` AND customer affirms |
| (skipped, pending_gift reset) | `pending_gift=True` AND customer does not affirm |

### Branch A.3 - Finalize prompt pending

| Reply | Trigger |
|---|---|
| `order_payment` | `pending_finalize=True` AND customer affirms -> `order_stage='await_payment'` |
| `ask_cancel_or_browse` | `pending_finalize=True` AND `customer_reject` AND cart not empty -> `pending_cancel=True` |
| (continue browsing) | `pending_finalize=True` AND `customer_reject` AND cart empty |

### Branch A.4 - Confirm cart cancellation

| Reply | Trigger |
|---|---|
| `order_cancel` + reset cart + agreed | `pending_cancel=True` AND `customer_reject` |
| (keep cart, continue) | `pending_cancel=True` AND any other intent |

### Branch A.5 - Cancel during order flow

| Reply | Trigger |
|---|---|
| `ask_cancel_or_browse` -> `pending_cancel=True` | `order_stage` in {await_info, await_confirm, await_pickup} AND `customer_reject` |

### Branch B - Order stage continuation

| Reply | Trigger |
|---|---|
| `order_done` (QR or COD confirmation) | `order_stage='await_payment'` AND payment method recognized |
| `order_payment` | `order_stage='await_payment'` AND payment method not recognized |
| `order_payment` | `order_stage='await_confirm'` AND customer affirms (no QUANTITY change this turn) |
| `order_summary` | `order_stage='await_confirm'` AND customer affirms AND QUANTITY updated this turn |
| `order_summary` | `order_stage='await_info'` AND `missing_for_order()` is empty |
| `order_need_info` | `order_stage='await_info'` AND info still missing AND intent `provide_cus_inf` or entities present |
| `get_direct_recap` | `order_stage='await_pickup'` AND customer affirms |

### Branch B2 - Product info / image / legitimacy

Runs before proposal confirmation — never adds products to cart.

| Reply | Trigger |
|---|---|
| `product_info` | Intent `ask_product_info` AND `last_product` exists |
| `legit` | Intent `ask_legit` AND `last_product` exists |
| `product_image` | Intent `ask_product_image` AND `last_product` exists |
| `need_product` | Any info intent AND no `last_product` |

### Branch C - Reject after proposal

| Reply | Trigger |
|---|---|
| `find_reject` | Intent `customer_reject` AND pending unresolved proposal |

### Branch D - Proposal confirmation

| Reply | Trigger |
|---|---|
| `availability` | Explicit choice AND proposal origin is `ask_product_availability` AND no `agree_order`/`add_product` |
| `product_info` | Explicit choice AND intent `ask_product_price` AND no `agree_order`/`add_product`; updates `last_product`, does NOT add to cart |
| `suggest_confirm` | Explicit choice AND proposal origin is `suggest` AND no `agree_order`/`add_product` |
| `confirmed` | Explicit choice AND any purchase intent -> product added to cart |
| `reask_choice` | Proposal present but choice is ambiguous |

### Branch E - New product search

| Reply | Trigger |
|---|---|
| `propose` (top-3 by name) | `PRODUCT_NAME` in entities AND intent in `_PROPOSE_INTENTS` |
| `propose` (top-3 by attributes) | No product name AND type/color available AND intent in `_SUGGEST_INTENTS` |

### Branch F - agree_order / affirm with cart

| Reply | Trigger |
|---|---|
| `order_empty_cart` | Intent `agree_order` AND cart empty |
| `order_need_info` | Intent `agree_order` AND cart not empty AND info missing -> `order_stage='await_info'` |
| `order_summary` | Intent `agree_order` AND cart not empty AND info complete -> `order_stage='await_confirm'` |

### Branch G - Payment / suggestion

| Reply | Trigger |
|---|---|
| `order_payment` | Intent `ask_payment_method` |
| `suggest` | Intent `ask_product_suggestion` AND budget/color/type available |

### Branch H - Intent Q&A

| Reply | Trigger |
|---|---|
| `final_price` | Intent `ask_final_price` AND cart not empty |
| `gift_offer` | Intent `ask_gift_package` -> sets `pending_gift=True` |
| `complaint` | Intent `product_complaint` or `complain_shipping_issue` |
| `buy_thanks` | Intent `buy_thanks` |
| `immediate_ship` | Intent `immediate_ship` AND cart not empty AND info complete |
| `immediate_ship_need` | Intent `immediate_ship` AND cart empty or info missing |
| `get_direct_recap` + `order_stage='done'` | Intent `get_product_direct` AND `state.agreed=True` AND cart not empty |
| `get_direct_ask` + `order_stage='await_pickup'` | Intent `get_product_direct` AND not agreed AND cart not empty |
| `shop_info` | Intent `ask_shop_info` or `get_product_direct` with empty cart |

### Branch I - Proactive finalize prompt

| Reply | Trigger |
|---|---|
| `ask_finalize` -> `pending_finalize=True` | Intent `provide_cus_inf` AND cart not empty AND `missing_for_order()` empty AND `order_stage is None` |

### Branch J - Escalation

| Reply | Trigger |
|---|---|
| `escalate` | `unknown_streak >= 5` (five consecutive turns with `other` or no intent) |

### Intent-driven defaults (no pipeline action matched)

| Reply | Intent |
|---|---|
| Delivery time | `ask_order_wait_time` |
| Shipping fee | `ask_shipping_fee` |
| Payment options (chuyển khoản / COD) | `ask_payment_method` |
| Acknowledgement | `provide_cus_inf` |
| Greeting | `greeting` |
| Farewell | `Goodbye` |
| Ask for budget/preferences | `ask_product_suggestion`, `provide_budget` |
| Ask for product name | `ask_product_price`, `ask_product_availability`, `ask_product_image`, `ask_find_product` |

### Fallback

Generic re-ask when no branch A–J or intent default matches.

## 4. Intent to Branch Mapping

| Intent | Primary branch |
|---|---|
| `agree` / `agree_order` | D (resolve proposal) -> F (order flow) |
| `add_product` | E (propose) -> D (confirm) |
| `ask_product_price` | E (propose) -> intent default |
| `ask_product_availability` | E (propose) / D (availability check) |
| `ask_product_image` | B2 (image) -> E (propose) |
| `ask_product_info` | B2 (info) |
| `ask_legit` | B2 (legit) |
| `ask_find_product` | E (propose/suggest) -> C (reject) |
| `ask_product_suggestion` | G (suggest) / E (suggest) / intent default |
| `provide_budget` | intent default |
| `ask_payment_method` | G -> intent default |
| `ask_order_wait_time` | intent default |
| `ask_shipping_fee` | intent default |
| `ask_final_price` | H |
| `ask_gift_package` | H |
| `immediate_ship` | H |
| `get_product_direct` | H |
| `ask_shop_info` | H |
| `product_complaint` / `complain_shipping_issue` | H |
| `buy_thanks` | H |
| `customer_reject` | C -> A.5 |
| `give_product` | borrows `last_intent` -> handled as that intent |
| `provide_cus_inf` | B (update info) -> I (ask_finalize) -> intent default |
| `greeting` | intent default |
| `Goodbye` | intent default |
| `other` / empty | J (streak counter -> escalate) |

## 5. LLM Mode (llm_baseline.py)

The LLM mode uses Gemini 2.5 Flash Lite with RAG over the product catalog. It shares the same `SessionState` and `SlotStore` as Hybrid mode so conversation state is consistent when the user switches modes.

Each turn:

1. RAG retrieves top-5 relevant products via Gemini embedding cosine search.
2. Gemini Call 1: generate a reply using system prompt + product context + conversation history.
3. Gemini Call 2: extract structured order state (JSON mode, temperature=0) from the conversation. Updates SessionState fields: customer name/phone/address, cart items, order_stage, payment method.
4. `SlotStore.persist()` saves state; triggers `save_order()` to SQLite when `order_stage='done'`.

State extraction uses `SessionState.update_from_llm_extraction()` with merge semantics:

- Customer fields update only if the new value is non-null and different from the stored value.
- Cart items are deduplicated by lowercase product name.
- `order_stage` only advances (never retreats) using a rank map: None < await_info < await_payment < done.
- `payment` is set once and never overwritten.

## 6. Key Invariants

- `give_product` borrows `last_intent` from the previous meaningful turn and is never stored as `last_intent` itself.
- If MIN_BUDGET > MAX_BUDGET after an update, the two values are swapped automatically.
- PRODUCT_COLOR: only colours matching `COLOR_VOCAB` are accepted; NER noise is discarded.
- QUANTITY: rejected if the value contains no digit; rejected if parsed integer <= 0 or > 100.
- Branch B2 (info intents) runs before proposal confirmation (D): `ask_product_image`, `ask_product_info`, and `ask_legit` never add to cart even when a proposal is pending.
- `agreed` flag is set permanently the first time `agree_order` appears and is not reset on order cancel.
- Order total = sum(price) x quantity + 20.000đ ship (+ 15.000đ/sản phẩm nếu gói quà).
