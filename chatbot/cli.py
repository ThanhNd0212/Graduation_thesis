"""Interactive CLI demo of the hybrid pipeline (Nhánh A test harness).

Run from the project root:
    python -m chatbot.cli

Usage in the prompt:
    <text>                normal message
    @m3 chốt con này      quoted reply to bot message m3 (Messenger-style, )
    /slots                dump the current customer order form
    /reset                start a new session
    /quit
"""

from __future__ import annotations

import re

from product_matcher import ProductMatcher
from .nlu import NLU
from .pipeline import ChatPipeline
from .state import SlotStore

PRODUCTS = 'final_data/products_2010_2026_updated.json'
SESSION = 'cli'


def _print_slots(snap):
    cust = {k: v for k, v in snap['customer'].items()}
    order = {k: v for k, v in snap['order'].items() if v}
    print('   ┌ Hồ sơ khách ')
    print(f'   │ customer: {cust or "—"}')
    print(f'   │ order   : {order or "—"}')
    print(f'   │ cart    : {snap["cart"] or "—"}')
    print('   └')


def main:
    print('Đang nạp model (PhoBERT intent + ViSoBERT NER + matcher)...')
    nlu = NLU
    matcher = ProductMatcher(PRODUCTS, method='lexical')
    pipe = ChatPipeline(nlu, matcher, SlotStore(persist_dir='sessions'))
    print('Sẵn sàng. Gõ tin nhắn (hoặc /quit).\n')

    while True:
        try:
            line = input('Bạn> ').strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line == '/quit':
            break
        if line == '/reset':
            pipe.store.reset(SESSION)
            print('(đã tạo phiên mới)\n')
            continue
        if line == '/slots':
            _print_slots(pipe.store.get(SESSION).snapshot)
            continue

        reply_to = None
        m = re.match(r'@(m\d+)\s+(.*)', line)
        if m:
            reply_to, line = m.group(1), m.group(2)

        r = pipe.process(SESSION, line, reply_to_msg_id=reply_to)
        print(f'\nBot[{r["msg_id"]}]: {r["reply"]}')
        print(f'   intents={r["intents"]}  entities={r["entities"]}  '
              f'({r["metrics"]["latency_ms"]}ms)')
        if 'proposal' in r:
            print(f'   (đề xuất ở {r["proposal"]["msg_id"]} — reply bằng: '
                  f'@{r["proposal"]["msg_id"]} <lựa chọn>)')
        _print_slots(r['slots'])
        print

    print('Tạm biệt!')


if __name__ == '__main__':
    main
