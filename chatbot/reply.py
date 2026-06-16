"""Template-based reply generation (decision #3: template-first, zero-cost).

render(state, intents, entities, action, payload) — pipeline sets `action` (+ payload);
reply formats. Business rules & constants: chatbot/dialogue_rules.md.
"""

from __future__ import annotations

# ── business constants (dialogue_rules.md §1) ─────────────────────────────────
SHIP_FEE = 20000
GIFT_FEE = 15000
DELIVERY = '2–3 ngày'
SHOP_INFO = ('Dạ địa chỉ shop là số 30 ngõ 20 đường xxx, sđt 08689xxxxx. '
             'Thời gian tiếp khách trực tiếp 8h–18h ạ.')
CUSTOMER_FORM = ('Dạ bạn cho shop xin thông tin cơ bản của bạn nhen:\n'
                 'Tên:\nSđt:\nĐịa chỉ (theo đơn vị hành chính cũ):')
PAYMENT_PROMPT = ('Dạ bạn muốn thanh toán hình thức nào ạ:\n'
                  '(1) Chuyển khoản trước\n(2) COD (nhận hàng trả tiền)')
COMPLAINT = ('Rất xin lỗi vì bạn đã có trải nghiệm không tốt, hệ thống AI đã ghi nhận '
             'vấn đề của bạn và nhân viên sẽ hỗ trợ bạn sớm nhất có thể ạ.')
BUY_THANKS = 'Cảm ơn bạn đã tin tưởng shop ạ, giúp được bạn là vinh dự của shop!'
ESCALATE = ('Dường như hệ thống chat AI đang trả lời không hiệu quả, đang rẽ hướng bạn đến '
            'nhân viên chăm sóc khách hàng. Vui lòng đợi phản hồi từ shop, xin lỗi và cảm ơn '
            'vì bạn đã thông cảm cho sự bất tiện này ạ.')
IMMEDIATE_SHIP = ('Đơn của bạn đã được ghi nhận và đợi người bán xác nhận. Đối với đơn giao '
                  'hỏa tốc: đặt trước 12h nhận trước 20h cùng ngày; đặt sau đó nhận trước 12h '
                  'ngày hôm sau ạ.')
ORDER_CANCEL = 'Dạ shop đã hủy đơn cho bạn. Bạn cần tư vấn thêm hay tìm mẫu khác không ạ?'


def _money(v):
    try:
        return f'{int(v):,}'.replace(',', '.') + 'đ'
    except (TypeError, ValueError):
        return '?'


def _list(cands):
    return '\n'.join(f'  {i+1}) {c["name"]} — {_money(c["price"])}' for i, c in enumerate(cands))


def _cust(state, label):
    return state.customer.get(label, {}).get('value', '?')


def _gift_total(state):
    return GIFT_FEE * len(state.cart) if state.gift_wrap else 0


def _final_total(state):
    return state.order_total() + SHIP_FEE + _gift_total(state)


def _order_summary(state) -> str:
    lines = ['Dạ shop xác nhận lại đơn của mình nha ạ:', 'Sản phẩm:']
    lines += [f'  - {c["name"]} ({_money(c["price"])})' for c in state.cart]
    lines += [
        f'Số lượng: {state.quantity()}',
        f'Khách: {_cust(state, "NAME")} — {_cust(state, "PHONE")}',
        f'Địa chỉ: {_cust(state, "ADDRESS")}',
        f'Giao hàng: toàn quốc {DELIVERY}',
        f'Phí ship: {_money(SHIP_FEE)}',
    ]
    if state.gift_wrap:
        lines.append(f'Gói quà: {_money(_gift_total(state))}')
    lines += [f'Tổng cộng: {_money(_final_total(state))}',
              '\nBạn xác nhận giúp shop đơn này nhé ạ?']
    return '\n'.join(lines)


def render(state, intents, entities, action, payload) -> str:
    intents = set(intents or [])

    # ── 1) dialogue-action replies ─────────────────────────────────────────────
    if action == 'propose':
        return 'Dạ ý bạn là mẫu nào ạ:\n' + _list(payload) + '\nBạn chọn giúp shop số mấy ạ?'
    if action == 'suggest':
        return ('Dạ tầm đó shop gợi ý mình vài mẫu nha ạ:\n' + _list(payload) +
                '\nBạn ưng mẫu nào, hay cho shop biết thêm sở thích (dòng/màu) để gợi ý sát hơn ạ?')
    if action == 'reask_choice':
        return f'Dạ trong các mẫu trên bạn chốt mẫu số mấy ạ (1–{len(payload)})?'
    if action == 'confirmed':
        msg = f'Dạ shop chốt "{payload["name"]}" ({_money(payload["price"])}) cho bạn nha ạ.'
        if [m for m in ('NAME', 'PHONE', 'ADDRESS') if m not in state.customer]:
            msg += '\n' + CUSTOMER_FORM
        return msg
    if action == 'suggest_confirm':        # §4.1b — show brief info after suggest selection
        p = payload
        extra = ''
        if p.get('category'):
            extra += f' — {p["category"]}'
        if p.get('number_pieces'):
            extra += f' — {p["number_pieces"]} mảnh'
        return (f'Dạ mẫu "{p["name"]}" ({_money(p["price"])}){extra}.\n'
                f'Bạn có muốn thêm mẫu này vào giỏ hàng không ạ?')
    if action == 'availability':           # §4.1 — stock report (default 100 > 0)
        return f'Dạ mẫu "{payload["name"]}" bên shop **còn hàng** ạ. Bạn có muốn đặt mẫu này không ạ?'
    if action == 'find_reject':            # §4.2
        return 'Dạ shop không có sản phẩm bạn tìm. Bạn có muốn tìm sản phẩm khác không ạ?'
    if action == 'need_product':
        return 'Dạ bạn cho shop xin tên mẫu (hoặc mô tả) bạn quan tâm với ạ?'
    if action == 'product_info':           # §4.3
        p = payload
        return (f'Dạ thông tin mẫu này ạ:\nTên: {p.get("name")}\nHãng: {p.get("brand")}\n'
                f'Dòng: {p.get("category")}\nSố mảnh: {p.get("number_pieces")}\n'
                f'Giá: {_money(p.get("price"))}')
    if action == 'legit':                  # §4.4
        return f'Dạ mẫu "{payload["name"]}" là hàng chính hãng **{payload.get("brand")}** ạ.'
    if action == 'product_image':          # §4.5 (DB chưa có ảnh → mô phỏng)
        return f'[ảnh {payload["name"]}]\nDạ ảnh mẫu "{payload["name"]}" đây ạ. Bạn xem giúp shop nhé!'
    if action == 'final_price':            # §4.7
        return (f'Dạ tổng đơn của mình: tiền hàng {_money(state.order_total())} + ship '
                f'{_money(SHIP_FEE)}' + (f' + gói quà {_money(_gift_total(state))}' if state.gift_wrap else '') +
                f' = **{_money(_final_total(state))}** ạ.')
    if action == 'gift_offer':             # §4.8
        return (f'Dạ shop có dịch vụ gói quà, phí {_money(GIFT_FEE)}/sản phẩm. '
                'Bạn có muốn dùng dịch vụ gói quà không ạ?')
    if action == 'gift_added':
        return ('Dạ shop đã thêm dịch vụ gói quà vào đơn ạ.\n' + _order_summary(state))
    if action == 'immediate_ship':         # §4.9
        return IMMEDIATE_SHIP
    if action == 'immediate_ship_need':
        return 'Dạ để giao hỏa tốc, ' + CUSTOMER_FORM[3:]
    if action == 'ask_finalize':           # §6 — show summary inline to avoid double-confirm
        summary = _order_summary(state).replace('\nBạn xác nhận giúp shop đơn này nhé ạ?', '')
        return summary + '\nBạn muốn chốt đặt hàng luôn hay tìm thêm sản phẩm khác ạ?'
    if action == 'ask_cancel_or_browse':
        return 'Dạ bạn có muốn tiếp tục xem thêm sản phẩm không, hay hủy toàn bộ giỏ hàng ạ?'
    if action == 'order_cancel':
        return ORDER_CANCEL
    if action == 'complaint':              # §4.11
        return COMPLAINT
    if action == 'buy_thanks':             # §4.12
        return BUY_THANKS
    if action == 'shop_info':              # §4.10 (ask_shop_info)
        return SHOP_INFO
    if action == 'get_direct_recap':       # §4.10 (đã xác nhận đặt)
        items = ', '.join(c['name'] for c in state.cart)
        return (SHOP_INFO + f'\nĐơn của bạn: {items} (số lượng {state.quantity()}), '
                'hình thức **lấy trực tiếp**. Shop giữ đơn cho bạn tối đa 2 ngày kể từ ngày '
                'xác nhận. Mong gặp bạn sớm ạ!')
    if action == 'get_direct_ask':         # §4.10 (chưa xác nhận đặt)
        return SHOP_INFO + '\nBạn đã muốn chốt đặt hàng để lấy trực tiếp chưa ạ?'
    if action == 'order_empty_cart':
        return 'Dạ mình chưa chọn sản phẩm nào ạ. Bạn cho shop biết mẫu muốn đặt nhé?'
    if action == 'order_need_info':
        return 'Dạ để shop lên đơn cho mình, ' + CUSTOMER_FORM[3:]
    if action == 'order_summary':
        return _order_summary(state)
    if action == 'order_payment':
        prefix = (IMMEDIATE_SHIP + '\n\n') if (isinstance(payload, dict) and payload.get('immediate_ship')) else ''
        return prefix + PAYMENT_PROMPT
    if action == 'order_done_reminder':    # §5b — guard khi stage=done, khóa mọi intent khác
        if state.payment == 'chuyển khoản':
            return ('Dạ đơn của bạn đã được tiếp nhận rồi ạ!\n'
                    'Shop đang chờ bill chuyển khoản từ bạn — bạn chụp và gửi bill vào đây giúp shop nha ạ.')
        return ('Dạ đơn của bạn đã được xác nhận rồi ạ. '
                'Shop sẽ liên hệ và xử lý sớm nhất có thể nha ạ!')
    if action == 'order_done':             # §5 — khác nhau theo hình thức
        if state.payment == 'chuyển khoản':
            return ('[qr chuyển khoản]\nDạ bạn chuyển khoản rồi chụp bill gửi lại chat giúp shop '
                    'nhé. Sau khi nhận bill, đơn của bạn được xác nhận và shop chuyển yêu cầu tới '
                    'người bán đợi duyệt ạ. Cảm ơn bạn đã mua hàng! 🎉')
        return ('Dạ đơn COD của bạn đã được xác nhận, giao trong ' + DELIVERY +
                '. Cảm ơn bạn đã mua hàng ạ! 🎉')
    if action == 'escalate':               # §7
        return ESCALATE

    # ── 2) intent-driven defaults ──────────────────────────────────────────────
    if 'ask_order_wait_time' in intents:
        return f'Dạ đơn của mình giao toàn quốc trong {DELIVERY} ạ.'
    if 'ask_shipping_fee' in intents:
        return f'Dạ phí ship bên shop là {_money(SHIP_FEE)} toàn quốc ạ.'
    if 'ask_payment_method' in intents:
        return PAYMENT_PROMPT
    if 'provide_cus_inf' in intents:
        return 'Dạ shop đã ghi nhận thông tin của bạn ạ.'
    if 'greeting' in intents:
        return 'Dạ shop nghe ạ, bạn cần tư vấn mẫu LEGO nào ạ?'
    if 'Goodbye' in intents:
        return 'Dạ cảm ơn bạn, hẹn gặp lại ạ!'
    if intents & {'ask_product_suggestion', 'provide_budget'}:
        return ('Dạ bạn cho shop xin tầm giá và sở thích (dòng/màu, tặng ai) '
                'để shop gợi ý sát nhất ạ?')
    if intents & {'ask_product_price', 'ask_product_availability', 'ask_product_image',
                  'ask_find_product'}:
        return 'Dạ bạn cho shop xin tên mẫu (hoặc mô tả) bạn quan tâm với ạ?'

    # ── 3) fallback (LLM hook for hard cases — Nhánh C) ─────────────────────────
    return 'Dạ shop chưa rõ ý bạn lắm, bạn nói rõ hơn giúp shop ạ?'
