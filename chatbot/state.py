"""Slot-filling conversation state.

In-memory store today; SlotStore is the abstraction so a Postgres/MySQL backend can be
dropped in later (decision #2). Each session accumulates a "customer order form":
NER labels are saved and updated per-label every turn; PRODUCT_NAME only enters the cart
AFTER confirmation. Every change is appended to `history`.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from product_matcher import parse_budget

# Label groups (13 NER labels). PRODUCT_NAME is special (confirmed cart) — not here.
SINGLE_LABELS = [
    'NAME', 'PHONE', 'ADDRESS', 'CITY',          # customer
    'MAX_BUDGET', 'MIN_BUDGET', 'QUANTITY',      # order (single-valued)
    'SHIP_DATE', 'SHIP_TIME', 'TYPE', 'COMPLEXITY',
]
CUSTOMER_LABELS = {'NAME', 'PHONE', 'ADDRESS', 'CITY'}
MULTI_LABELS    = ['PRODUCT_COLOR']              # accumulate
BUDGET_LABELS   = {'MAX_BUDGET', 'MIN_BUDGET'}


def _now() -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S')


def _clean_entity(label: str, value: str) -> str:
    """R6: NER values often carry trailing punctuation ('Nguyễn Thành,', '0868928485,')."""
    v = str(value).strip().strip(',.;:!?').strip()
    if label == 'PHONE':
        digits = re.sub(r'\D', '', v)
        return digits or v
    return v


# Recognised colours (longest-first) — NER PRODUCT_COLOR spans are noisy ("xanh lá không").
COLOR_VOCAB = ['xanh dương', 'xanh lá cây', 'xanh lá', 'xanh ngọc', 'xanh navy', 'vàng kim',
               'xanh', 'đỏ', 'vàng', 'đen', 'trắng', 'cam', 'tím', 'hồng', 'nâu', 'xám',
               'ghi', 'bạc', 'be']


def _extract_color(span: str):
    s = str(span).lower()
    for c in COLOR_VOCAB:
        if c in s:
            return c
    return None


class SessionState:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.turn = 0
        self._msg_counter = 0
        self.customer: dict = {}                 # label -> {value, raw, turn, updated_at}
        self.order: dict = {                     # single-valued slots
            k: None for k in
            ['MAX_BUDGET', 'MIN_BUDGET', 'QUANTITY', 'SHIP_DATE', 'SHIP_TIME', 'TYPE', 'COMPLEXITY']
        }
        self.order['PRODUCT_COLOR'] = []         # multi
        self.cart: list = []                     # confirmed products [{product_id, name, price, turn}]
        self.proposals: dict = {}                # msg_id -> {candidates, query, turn, resolved}
        self.history: list = []                  # [{turn, label, old, new, at}]
        self.order_stage = None                  # None | 'await_info' | 'await_confirm' | 'await_payment' | 'await_pickup' | 'done'
        self.payment = None                      # 'chuyển khoản' | 'COD'
        self.last_product_id = None              # most-recently confirmed/proposed product
        self.last_intent = None                  # last meaningful intent (for give_product)
        self.unknown_streak = 0                  # consecutive other/empty turns (escalation)
        self.gift_wrap = False                   # gift-package service chosen
        self.pending_gift = False                # waiting for yes/no on gift offer
        self.pending_finalize = False            # ask_finalize was sent, waiting for agree/search-more
        self.pending_cancel = False              # ask_cancel_or_browse was sent, waiting for confirm
        self.agreed = False                      # customer has agreed to order at least once
        self.pending_availability_product = None # product shown as 'available' awaiting buy confirmation
        self.order_done_persisted = False        # True after completed order is written to DB
        self.conversation_history: list[dict] = []  # [{role, content}] dùng cho LLM mode

    # ids
    def next_msg_id(self) -> str:
        self._msg_counter += 1
        return f'm{self._msg_counter}'

    # slot updates
    def _log(self, label, old, new):
        self.history.append({'turn': self.turn, 'label': label,
                             'old': old, 'new': new, 'at': _now()})

    def update_entities(self, entities: dict):
        """Apply this turn's NER entities. PRODUCT_NAME is skipped (goes through confirm.py)."""
        for label, values in entities.items():
            if not values or label == 'PRODUCT_NAME':
                continue
            raw = _clean_entity(label, values[0])
            if label == 'QUANTITY' and not re.search(r'\d', raw):   # ignore "ấy" etc.
                continue
            if label == 'QUANTITY':
                try:
                    qty_int = int(re.sub(r'\D', '', raw))
                    if qty_int <= 0 or qty_int > 100:   # reject impossible quantities (0, negative, >stock)
                        continue
                    raw = str(qty_int)
                except ValueError:
                    continue
            if label in CUSTOMER_LABELS:
                old = self.customer.get(label, {}).get('value')
                self.customer[label] = {'value': raw, 'turn': self.turn, 'updated_at': _now()}
                self._log(label, old, raw)
            elif label in BUDGET_LABELS:
                val = parse_budget(raw)
                old = (self.order.get(label) or {}).get('value')
                self.order[label] = {'value': val, 'raw': raw, 'turn': self.turn}
                self._log(label, old, val)
            elif label in self.order and label != 'PRODUCT_COLOR':   # other single order slots
                old = (self.order.get(label) or {}).get('value')
                self.order[label] = {'value': raw, 'turn': self.turn}
                self._log(label, old, raw)
            elif label == 'PRODUCT_COLOR':
                for c in values:
                    col = _extract_color(c)                  # keep only recognised colours
                    if col and col not in [x.lower() for x in self.order['PRODUCT_COLOR']]:
                        self.order['PRODUCT_COLOR'].append(col)
                        self._log(label, None, col)
        self._fix_budget_order

    def add_confirmed_product(self, product: dict):
        item = {'product_id': product.get('product_id'), 'name': product.get('name'),
                'price': product.get('price'), 'turn': self.turn}
        self.cart.append(item)
        self.last_product_id = product.get('product_id')
        self._log('PRODUCT_NAME', None, item['name'])

    # proposals
    def add_proposal(self, msg_id, candidates, query, origin=None, origin_action=None):
        self.proposals[msg_id] = {'candidates': candidates, 'query': query, 'turn': self.turn,
                                  'resolved': False, 'origin': list(origin or []),
                                  'origin_action': origin_action}
        if candidates and candidates[0].get('product_id'):
            self.last_product_id = candidates[0]['product_id']

    def get_proposal(self, msg_id):
        return self.proposals.get(msg_id)

    def pending_proposal(self):
        """The LATEST proposal, only if still unresolved (older ones are stale once a
        newer list was shown). Old proposals stay reachable explicitly via reply_to."""
        if not self.proposals:
            return None
        mid = list(self.proposals)[-1]
        p = self.proposals[mid]
        return (mid, p) if not p['resolved'] else None

    def resolve_proposal(self, msg_id):
        if msg_id in self.proposals:
            self.proposals[msg_id]['resolved'] = True

    # views
    def _fix_budget_order(self):
        """Swap MIN_BUDGET and MAX_BUDGET if MIN > MAX."""
        lo, hi = self.order.get('MIN_BUDGET'), self.order.get('MAX_BUDGET')
        if lo and hi and lo.get('value') and hi.get('value') and lo['value'] > hi['value']:
            self.order['MIN_BUDGET'], self.order['MAX_BUDGET'] = hi, lo

    def budget(self):
        for k in ('MAX_BUDGET', 'MIN_BUDGET'):   # NER sometimes labels "tầm 300k" as MIN
            b = self.order.get(k)
            if b and b.get('value') is not None:
                return b['value']
        return None

    def colors(self):
        return list(self.order.get('PRODUCT_COLOR', []))

    def type_hint(self):
        t = self.order.get('TYPE')
        return t['value'] if t else None

    def quantity(self):
        q = self.order.get('QUANTITY')
        try:
            return int(re.sub(r'\D', '', q['value'])) if q else 1
        except (TypeError, ValueError):
            return 1

    def missing_for_order(self):
        """R3: required fields still missing to finalize an order."""
        miss = [lbl for lbl in ('NAME', 'PHONE', 'ADDRESS') if lbl not in self.customer]
        if not self.cart:
            miss.append('CART')
        return miss

    def order_total(self):
        return sum((c.get('price') or 0) for c in self.cart) * self.quantity()

    def update_from_llm_extraction(self, data: dict):
        """Apply structured order state extracted by LLM baseline. Merges, never overwrites non-null with null."""
        _STAGE_RANK = {None: 0, 'await_info': 1, 'await_payment': 2, 'done': 3}
        now = _now()

        # Customer fields
        for key, label in [('customer_name', 'NAME'), ('customer_phone', 'PHONE'),
                            ('customer_address', 'ADDRESS')]:
            val = str(data.get(key) or '').strip()
            if not val:
                continue
            if label == 'PHONE':
                val = re.sub(r'\D', '', val) or val
            old = self.customer.get(label, {}).get('value')
            if old != val:
                self.customer[label] = {'value': val, 'turn': self.turn, 'updated_at': now}
                self._log(label, old, val)

        # Cart — add items found in extraction that aren't already tracked
        existing_names = {c['name'].lower() for c in self.cart}
        for item in (data.get('cart') or []):
            name = str(item.get('name') or '').strip()
            if not name or name.lower() in existing_names:
                continue
            price = int(item.get('price') or 0)
            self.cart.append({'product_id': None, 'name': name,
                              'price': price, 'turn': self.turn})
            existing_names.add(name.lower())
            self._log('PRODUCT_NAME', None, name)

        # Order stage — only advance, never go back
        new_stage = data.get('order_stage')
        if new_stage in _STAGE_RANK:
            cur_rank = _STAGE_RANK.get(self.order_stage, 0)
            if _STAGE_RANK[new_stage] > cur_rank:
                self.order_stage = new_stage

        # Payment method
        payment = data.get('payment')
        if payment in ('chuyển khoản', 'COD') and not self.payment:
            self.payment = payment

    def snapshot(self) -> dict:
        return {
            'customer': {k: v['value'] for k, v in self.customer.items()},
            'order': {
                **{k: (v['value'] if isinstance(v, dict) else v)
                   for k, v in self.order.items() if k != 'PRODUCT_COLOR'},
                'PRODUCT_COLOR': self.order['PRODUCT_COLOR'],
            },
            'cart': [c['name'] for c in self.cart],
            'stage': self.order_stage,
            'payment': self.payment,
            'history_len': len(self.history),
        }


class SlotStore:
    """Abstraction over session storage. In-memory now; swap for a DB later (decision #2).
    If persist_dir is set, each session is also mirrored to sessions/<id>.json."""

    def __init__(self, persist_dir: str | None = None):
        self._sessions: dict[str, SessionState] = {}
        self.persist_dir = Path(persist_dir) if persist_dir else None
        if self.persist_dir:
            self.persist_dir.mkdir(parents=True, exist_ok=True)

    def get(self, session_id: str) -> SessionState:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState(session_id)
        return self._sessions[session_id]

    def reset(self, session_id: str):
        self._sessions.pop(session_id, None)

    def persist(self, session: SessionState):
        if self.persist_dir:
            path = self.persist_dir / f'{session.session_id}.json'
            data = {'snapshot': session.snapshot(), 'history': session.history,
                    'cart': session.cart}
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

        if session.order_stage == 'done' and not session.order_done_persisted:
            try:
                from .db import save_order
                save_order(session)
                session.order_done_persisted = True
            except Exception:
                pass  # DB failure không ảnh hưởng chat response
