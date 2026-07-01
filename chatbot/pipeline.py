"""Orchestrator (hybrid mode) — NLU + slot-filling + confirmation + order + intent Q&A.

process(session_id, message, reply_to_msg_id) -> response dict.
Rules implemented here: chatbot/dialogue_rules.md.
"""

from __future__ import annotations

import re
import time

from . import confirm, reply as reply_mod
from .state import SlotStore

# product intents that should PROPOSE top-3 from a PRODUCT_NAME
_PROPOSE_INTENTS = {'ask_product_price', 'ask_product_availability', 'ask_product_image',
                    'ask_find_product', 'add_product', 'agree_order', 'ask_product_suggestion'}
# intents that browse-by-attribute (suggest) when there is no product name — NOT agree_order
_SUGGEST_INTENTS = {'ask_product_availability', 'ask_find_product', 'ask_product_suggestion'}
# info questions about a product — answer info, never add to cart
_INFO_INTENTS = {'ask_product_info', 'ask_legit', 'ask_product_image'}
_AFFIRM_RE = re.compile(
    r'\b(ok|okê|oke|đồng ý|đúng( rồi)?|chuẩn|vâng|ừ|uhm|uh|chốt|được|xác nhận|có)\b', re.I)
_MEANINGFUL_SKIP = {'give_product', 'other', 'agree'}   # not used as last_intent
# Vietnamese filler/conjunction words that NER sometimes mis-tags as PRODUCT_NAME
_FILLER_WORDS = {'nhé', 'nha', 'nhen', 'thôi', 'thui', 'dạ', 'vâng', 'ạ', 'hay',
                 'đó', 'này', 'nhỉ', 'ơi', 'với', 'thế', 'vậy', 'mà', 'thì'}


def _affirmative(text, intents) -> bool:
    return 'agree' in intents or 'agree_order' in intents or bool(_AFFIRM_RE.search(text.lower()))


def _payment_choice(text):
    low = text.lower()
    if re.search(r'\b(cod|ship cod|nhận hàng( trả)?|trả sau|thu hộ)\b', low) or re.search(r'\b2\b', low):
        return 'COD'
    if re.search(r'\b(chuyển khoản|ck|bank|qr|trả trước)\b', low) or re.search(r'\b1\b', low):
        return 'chuyển khoản'
    return None


class ChatPipeline:
    def __init__(self, nlu, matcher, store: SlotStore | None = None, logger=None):
        self.nlu = nlu
        self.matcher = matcher
        self.store = store or SlotStore
        if logger is None:
            from .logger import TurnLogger
            logger = TurnLogger
        self.logger = logger

    def _last_product(self, s):
        return self.matcher.get(s.last_product_id) if s.last_product_id else None

    def process(self, session_id: str, message: str, reply_to_msg_id: str | None = None,
                mode: str = 'hybrid') -> dict:
        t0 = time.perf_counter
        s = self.store.get(session_id)
        s.turn += 1

        intents, entities = self.nlu.analyze(message)

        # NER quirk mitigation: a bare "số N" choosing from a proposal sometimes gets
        # mis-tagged as QUANTITY/MIN_BUDGET/MAX_BUDGET (the digit, not a real quantity/budget).
        # Drop such entities when their value exactly matches the selection digit.
        prop_for_filter = None
        if reply_to_msg_id:
            p = s.get_proposal(reply_to_msg_id)
            if p and not p['resolved']:
                prop_for_filter = p
        if prop_for_filter is None:
            pp = s.pending_proposal()
            prop_for_filter = pp[1] if pp else None
        if prop_for_filter and confirm.has_explicit_choice(message):
            m = re.search(r'\b([1-9])\b', message)
            if m:
                digit = m.group(1)
                for lbl in ('QUANTITY', 'MIN_BUDGET', 'MAX_BUDGET'):
                    vals = entities.get(lbl)
                    if vals and re.sub(r'\D', '', str(vals[0])) == digit:
                        entities = {**entities, lbl: []}

        s.update_entities(entities)

        # give_product is ambiguous -> borrow the last meaningful intent
        eff = set(intents)
        if 'give_product' in intents and s.last_intent:
            eff.add(s.last_intent)

        action, payload = None, None
        trace = []                                  # the bot's reasoning, step by step
        product_query = ' '.join(w for w in entities.get('PRODUCT_NAME', [])
                                  if w.strip().lower() not in _FILLER_WORDS)
        if 'agree_order' in intents:
            s.agreed = True
        # A.0) Reset completed order on new greeting
        if s.order_stage == 'done' and 'greeting' in intents:
            s.cart, s.order_stage, s.payment, s.agreed = [], None, None, False
            trace.append("A.0: đơn cũ đã done + khách chào mới -> reset giỏ + order_stage (giữ thông tin khách)")
        # A.0-done) Lock all branches once the order is complete
        if action is None and s.order_stage == 'done':
            action = 'order_done_reminder'
            trace.append("A.0-done: stage=done -> khóa mọi nhánh, nhắc gửi bill / chờ shop liên hệ")
        # A.1) Auto-confirm a product just availability-checked
        if s.pending_availability_product and \
                (_affirmative(message, intents) or 'agree_order' in intents or 'add_product' in intents):
            s.add_confirmed_product(s.pending_availability_product)
            s.pending_availability_product = None
            trace.append("A.1: khách đồng ý mua sản phẩm vừa hỏi tồn kho -> tự thêm vào cart")
        pend = s.pending_proposal()
        trace.append(f"eff_intents={sorted(eff)}")
        trace.append(f"context: stage={s.order_stage}, cart={len(s.cart)}, agreed={s.agreed}, "
                     f"pending_proposal={pend[0] if pend else None}, last_intent={s.last_intent}, "
                     f"product_query={product_query!r}, budget={s.budget}, "
                     f"colors={s.colors}, type={s.type_hint}")

        # A) gift offer pending
        if s.pending_gift:
            s.pending_gift = False
            if _affirmative(message, intents):
                s.gift_wrap = True
                action = 'gift_added'
                trace.append("A: gift offer pending + khách đồng ý -> thêm gói quà (gift_added)")
            else:
                trace.append("A: gift offer pending nhưng khách không đồng ý -> bỏ qua")

        # A.3) finalize prompt pending
        if action is None and s.pending_finalize:
            s.pending_finalize = False
            if _affirmative(message, intents) or 'agree_order' in intents:
                s.order_stage, action = 'await_payment', 'order_payment'
                if 'immediate_ship' in intents:
                    payload = {'immediate_ship': True}
                    trace.append("A.3: pending_finalize + khách đồng ý + hỏi hỏa tốc -> thanh toán + trả lời hỏa tốc luôn")
                else:
                    trace.append("A.3: pending_finalize + khách đồng ý -> thẳng vào thanh toán (bỏ qua confirm lại)")
            elif 'customer_reject' in intents:
                if s.cart:
                    s.pending_cancel, action = True, 'ask_cancel_or_browse'
                    trace.append("A.3: pending_finalize + customer_reject + có giỏ -> hỏi lại ý định (ask_cancel_or_browse)")
                else:
                    trace.append("A.3: pending_finalize + customer_reject + giỏ trống -> tiếp tục tư vấn")

        # A.4) Confirm cart cancellation after re-asking intent
        if action is None and s.pending_cancel:
            s.pending_cancel = False
            if 'customer_reject' in intents:
                s.cart, s.order_stage, s.agreed, action = [], None, False, 'order_cancel'
                trace.append("A.4: khách xác nhận hủy -> order_cancel + reset giỏ + agreed")
            else:
                trace.append("A.4: khách không xác nhận hủy -> giữ giỏ, tiếp tục tư vấn")

        # A.5) Cancel / exit order flow (ask for confirmation before clearing cart)
        if action is None and s.order_stage in ('await_info', 'await_confirm', 'await_pickup') \
                and 'customer_reject' in intents:
            s.pending_cancel, action = True, 'ask_cancel_or_browse'
            trace.append("A.5: khách có dấu hiệu muốn hủy trong luồng chốt đơn -> hỏi lại xác nhận trước khi xóa giỏ")

        # B) order-stage continuation (R3/)
        if action is None and s.order_stage == 'await_payment':
            pay = _payment_choice(message)
            if pay:
                s.payment, s.order_stage, action = pay, 'done', 'order_done'
                trace.append(f"B: đang chờ thanh toán -> khách chọn '{pay}' -> hoàn tất đơn (order_done)")
            else:
                action = 'order_payment'
                trace.append("B: đang chờ thanh toán nhưng chưa nhận ra lựa chọn -> hỏi lại")
        elif action is None and s.order_stage == 'await_confirm':
            if _affirmative(message, intents):
                qty_updated = any(h['label'] == 'QUANTITY' and h['turn'] == s.turn
                                  for h in s.history)
                if qty_updated:
                    action = 'order_summary'
                    trace.append("B: đang chờ xác nhận đơn -> khách đồng ý nhưng đổi QUANTITY -> cập nhật lại tóm tắt đơn")
                else:
                    s.order_stage, action = 'await_payment', 'order_payment'
                    trace.append("B: đang chờ xác nhận đơn -> khách đồng ý -> chuyển sang chọn thanh toán")
        elif action is None and s.order_stage == 'await_info':
            if not s.missing_for_order():
                s.order_stage, action = 'await_confirm', 'order_summary'
                trace.append("B: đang chờ thông tin -> đã đủ -> tóm tắt đơn (order_summary)")
            elif 'provide_cus_inf' in intents or entities:
                action, payload = 'order_need_info', s.missing_for_order()
                trace.append(f"B: đang chờ thông tin -> vẫn thiếu {s.missing_for_order()} -> hỏi tiếp")
        elif action is None and s.order_stage == 'await_pickup':   # await_pickup confirm
            if _affirmative(message, intents):
                s.agreed, s.order_stage, action = True, 'done', 'get_direct_recap'
                trace.append("B: đang chờ xác nhận lấy trực tiếp -> khách đồng ý -> recap pickup")

        # B2) info Q&A about a referenced/last product (image/info/legit)
        if action is None and (_INFO_INTENTS & eff):
            prop = None
            if reply_to_msg_id and s.get_proposal(reply_to_msg_id):
                prop = s.get_proposal(reply_to_msg_id)
            elif s.pending_proposal():
                prop = s.pending_proposal()[1]
            picked = None
            if prop and confirm.has_explicit_choice(message):
                st, idx = confirm.resolve_choice(message, prop['candidates'])
                if st == 'chosen':
                    s.last_product_id = prop['candidates'][idx].get('product_id')
                    picked = prop['candidates'][idx].get('name')
            lp = self._last_product(s)
            info_kind = ('ask_product_info' if 'ask_product_info' in eff else
                         'ask_legit' if 'ask_legit' in eff else 'ask_product_image')
            if 'ask_product_info' in eff:
                action, payload = ('product_info', lp) if lp else ('need_product', None)
            elif 'ask_legit' in eff:
                action, payload = ('legit', lp) if lp else ('need_product', None)
            else:
                action, payload = ('product_image', lp) if lp else ('need_product', None)
            ref = f"chọn '{picked}' từ proposal" if picked else (f"last_product={lp['name']}" if lp else "chưa có sản phẩm")
            trace.append(f"B2: info intent ({info_kind}) -> {ref} -> {action} (KHÔNG thêm cart)")

        # C) customer_reject after a proposal
        if action is None and 'customer_reject' in intents and s.pending_proposal():
            mid, _ = s.pending_proposal()
            s.resolve_proposal(mid)
            action = 'find_reject'
            trace.append("C: khách từ chối (customer_reject) sau đề xuất -> find_reject")

        # D) proposal confirmation
        if action is None:
            target = None
            if reply_to_msg_id:
                prop = s.get_proposal(reply_to_msg_id)
                if prop and not prop['resolved']:
                    target = (reply_to_msg_id, prop)
            if target is None and confirm.has_explicit_choice(message):
                target = s.pending_proposal()
            if target:
                mid, prop = target
                status, idx = confirm.resolve_choice(message, prop['candidates'])
                if status == 'chosen':
                    product = prop['candidates'][idx]
                    s.resolve_proposal(mid)
                    src = f"reply_to {reply_to_msg_id}" if reply_to_msg_id else "pending proposal"
                    if 'ask_product_availability' in prop.get('origin', []) and \
                            not ({'agree_order', 'add_product'} & eff):
                        s.last_product_id = product.get('product_id')
                        s.pending_availability_product = product
                        action, payload = 'availability', product
                        trace.append(f"D: chọn '{product['name']}' ({src}); proposal gốc là availability "
                                     f"-> báo còn hàng (không thêm cart, chờ khách xác nhận mua)")
                    elif 'ask_product_price' in eff and not ({'agree_order', 'add_product'} & eff):
                        # Price query from a proposal: update last_product, do NOT add to cart
                        s.last_product_id = product.get('product_id')
                        lp = self._last_product(s)
                        action, payload = ('product_info', lp) if lp else ('need_product', None)
                        trace.append(f"D: chọn '{product['name']}' ({src}); hỏi giá -> cập nhật last_product, "
                                     f"KHÔNG thêm cart -> product_info")
                    elif prop.get('origin_action') == 'suggest' and \
                            not ({'agree_order', 'add_product'} & eff):
                        s.last_product_id = product.get('product_id')
                        s.pending_availability_product = product
                        action, payload = 'suggest_confirm', product
                        trace.append(f"D: chọn '{product['name']}' ({src}); proposal gốc là suggest "
                                     f"-> hỏi xác nhận thêm vào giỏ (không tự chốt)")
                    else:
                        s.add_confirmed_product(product)
                        s.pending_availability_product = None
                        action, payload = 'confirmed', product
                        trace.append(f"D: chọn '{product['name']}' ({src}) -> thêm vào cart (confirmed)")
                elif status == 'ambiguous':
                    action, payload = 'reask_choice', prop['candidates']
                    trace.append("D: có proposal nhưng chưa rõ chọn mẫu nào -> hỏi lại số")

        # E) new product question -> propose top-3
        if action is None and product_query and (_PROPOSE_INTENTS & eff):
            cands = confirm.propose(self.matcher, product_query, budget=s.budget, colors=s.colors)
            if cands:
                action, payload = 'propose', cands
                trace.append(f"E: hỏi sản phẩm có tên '{product_query}' -> match top-{len(cands)} -> propose")
            else:
                trace.append(f"E: hỏi sản phẩm '{product_query}' nhưng matcher không tìm thấy")
        elif action is None and (s.type_hint or s.colors) and (_SUGGEST_INTENTS & eff):
            cands = self.matcher.suggest(budget=s.budget, colors=s.colors, type_hint=s.type_hint)
            if cands:
                action, payload = 'propose', cands
                trace.append(f"E: không có tên, dùng type/color -> suggest top-{len(cands)} -> propose")

        # F) order finalization on agree_order (R3/R7)
        if action is None and ('agree_order' in intents or
                               (_affirmative(message, intents) and s.cart and s.order_stage is None)):
            miss = s.missing_for_order()
            if 'CART' in miss:
                action = 'order_empty_cart'
                trace.append("F: agree_order nhưng giỏ trống -> báo chưa chọn sản phẩm")
            elif miss:
                s.order_stage, action, payload = 'await_info', 'order_need_info', miss
                trace.append(f"F: agree_order -> vào chốt đơn, còn thiếu {miss} -> hỏi thông tin")
            else:
                s.order_stage, action = 'await_confirm', 'order_summary'
                trace.append("F: agree_order -> đủ thông tin -> tóm tắt đơn xác nhận")

        # G) payment / suggestion
        if action is None and 'ask_payment_method' in intents:
            action = 'order_payment'
            trace.append("G: ask_payment_method -> đưa lựa chọn CK/COD")
        if action is None and 'ask_product_suggestion' in intents and \
                (s.budget or s.colors or s.type_hint):
            cands = self.matcher.suggest(budget=s.budget, colors=s.colors, type_hint=s.type_hint)
            if cands:
                action, payload = 'suggest', cands
                trace.append(f"G: ask_product_suggestion + có thuộc tính -> suggest top-{len(cands)}")

        # H) intent Q&A about the order / shop
        if action is None:
            if 'ask_final_price' in intents and s.cart:
                action = 'final_price'
                trace.append("H: ask_final_price + có cart -> tính tổng tiền")
            elif 'ask_gift_package' in intents:
                s.pending_gift, action = True, 'gift_offer'
                trace.append("H: ask_gift_package -> mời gói quà 15k/sp")
            elif eff & {'product_complaint', 'complain_shipping_issue'}:
                action = 'complaint'
                trace.append("H: khiếu nại -> câu xin lỗi + chuyển nhân viên")
            elif 'buy_thanks' in intents:
                action = 'buy_thanks'
                trace.append("H: buy_thanks -> cảm ơn khách")
            elif 'immediate_ship' in intents:
                action = 'immediate_ship' if (s.cart and not s.missing_for_order()) else 'immediate_ship_need'
                trace.append(f"H: immediate_ship -> {action}")
            elif 'get_product_direct' in eff:
                if s.cart and s.agreed:
                    s.order_stage, action = 'done', 'get_direct_recap'
                    trace.append("H: get_product_direct + đã agree + có cart -> recap lấy trực tiếp")
                elif s.cart:
                    s.order_stage, action = 'await_pickup', 'get_direct_ask'
                    trace.append("H: get_product_direct, chưa agree -> hỏi đã chốt chưa (await_pickup)")
                else:
                    action = 'shop_info'
                    trace.append("H: get_product_direct, giỏ trống -> chỉ trả info shop")
            elif 'ask_shop_info' in intents:
                action = 'shop_info'
                trace.append("H: ask_shop_info -> trả info shop")

        # I) proactive finalize prompt
        if action is None and 'provide_cus_inf' in intents and s.cart and not s.missing_for_order() \
                and s.order_stage is None:
            s.pending_finalize, action = True, 'ask_finalize'
            trace.append("I: đủ thông tin + có cart + stage=None -> chủ động hỏi chốt đơn hay tìm thêm (pending_finalize=True)")

        # J) escalation: 3 consecutive turns the bot couldn't handle
        if action is None and (not intents or intents == {'other'}):
            s.unknown_streak += 1
            trace.append(f"J: không xử lý được (unknown_streak={s.unknown_streak})")
            if s.unknown_streak >= 5:
                action = 'escalate'
                trace.append("J: 5 lượt liên tiếp không hiểu -> chuyển nhân viên (escalate)")
        else:
            s.unknown_streak = 0

        if action is None:
            trace.append("-> không nhánh nào khớp -> fallback hỏi lại")

        # assign bot msg id; register proposals/suggestions
        msg_id = s.next_msg_id
        if action in ('propose', 'suggest'):
            s.add_proposal(msg_id, payload, product_query or '<suggest>', origin=list(intents), origin_action=action)
            s.pending_availability_product = None

        # update last meaningful intent (for next turn's give_product)
        for it in intents:
            if it not in _MEANINGFUL_SKIP:
                s.last_intent = it
                break

        text = reply_mod.render(s, intents, entities, action, payload)
        s.conversation_history.append({'role': 'user',      'content': message})
        s.conversation_history.append({'role': 'assistant', 'content': text})
        self.store.persist(s)
        latency = round((time.perf_counter - t0) * 1000, 1)

        slot_updates = [{'label': h['label'], 'old': h['old'], 'new': h['new']}
                        for h in s.history if h['turn'] == s.turn]
        self.logger.log({
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'session_id': session_id, 'turn': s.turn, 'msg_id': msg_id,
            'input': message, 'reply_to': reply_to_msg_id,
            'intents': intents, 'entities': entities, 'slot_updates': slot_updates,
            'trace': trace, 'action': action, 'reply': text,
            'slots': s.snapshot, 'latency_ms': latency,
        })

        resp = {
            'msg_id': msg_id, 'reply': text, 'intents': intents, 'entities': entities,
            'slots': s.snapshot, 'metrics': {'latency_ms': latency},
        }
        if action in ('propose', 'reask_choice', 'suggest'):
            resp['proposal'] = {'msg_id': msg_id, 'candidates': payload}
        return resp
