"""Product confirmation (§3/§4): propose top-3 from a PRODUCT_NAME, and resolve which
candidate the customer picked. This is NOT intent classification — the intent model
already told us it's a product/agree turn; here we only figure out WHICH of the 3.
"""

from __future__ import annotations

import re

# ordinal words → 1-based position
_ORDINAL = {
    'đầu': 1, 'nhất': 1, 'một': 1, 'thứ nhất': 1, 'đầu tiên': 1,
    'hai': 2, 'nhì': 2, 'giữa': 2, 'thứ hai': 2,
    'ba': 3, 'cuối': 3, 'cuối cùng': 3, 'thứ ba': 3,
}
_CHOICE_RE = re.compile(
    r'(số\s*[1-9]|\b[1-9]\b|đầu tiên|đầu|nhất|thứ\s*(nhất|hai|ba|[1-9])|nhì|cuối|'
    r'này|đó|nó|chốt|lấy|chọn|ok|okê|đồng ý)', re.I)


def propose(matcher, product_name: str, budget=None, colors=None, top_k: int = 3):
    """Top-k candidate products for a customer PRODUCT_NAME (budget/colors as signals)."""
    return matcher.match(product_name, top_k=top_k, max_budget=budget, colors=colors or None)


def looks_like_choice(text: str) -> bool:
    return bool(_CHOICE_RE.search(text.lower()))


# Explicit selectors only (number / ordinal) — NOT generic "chốt/ok/này". Used to decide
# whether an unreferenced message is picking from the latest proposal vs finalizing an order.
_EXPLICIT_RE = re.compile(
    r'(số\s*[1-9]|mẫu\s*[1-9]|con\s*[1-9]|\b[1-9]\b|đầu tiên|đầu|nhất|'
    r'thứ\s*(nhất|hai|ba|[1-9])|nhì|giữa|cuối)', re.I)


def has_explicit_choice(text: str) -> bool:
    return bool(_EXPLICIT_RE.search(text.lower()))


def resolve_choice(text: str, candidates: list):
    """Return ('chosen', idx) | ('ambiguous', None) | ('none', None)."""
    low = text.lower()
    n = len(candidates)

    # 1) explicit number: "số 2", "lấy 2", bare "2"
    m = re.search(r'(?:số\s*)?\b([1-9])\b', low)
    if m:
        i = int(m.group(1)) - 1
        if 0 <= i < n:
            return 'chosen', i

    # 2) ordinal words
    for word, pos in _ORDINAL.items():
        if re.search(rf'\b{re.escape(word)}\b', low):
            i = min(pos, n) - 1
            return 'chosen', i

    # 3) match a distinctive token from a candidate name
    for i, c in enumerate(candidates):
        for tok in re.findall(r'[a-z0-9]+', c['name'].lower()):
            if len(tok) >= 3 and re.search(rf'\b{re.escape(tok)}\b', low):
                return 'chosen', i

    # 4) bare "cái này / con đó" with no selector → ambiguous (unless only 1 candidate)
    if re.search(r'\b(này|đó|nó|chốt|lấy|chọn|ok|okê|đồng ý)\b', low):
        return ('chosen', 0) if n == 1 else ('ambiguous', None)

    return 'none', None
