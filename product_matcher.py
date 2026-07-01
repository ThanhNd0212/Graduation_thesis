"""Product matcher — map an NER-extracted PRODUCT_NAME to real products.

Bridges the NER pipeline (ViSoBERT) and the product catalogue
(final_data/products_2010_2026_updated.json). Designed to be imported as a
single object and reused across requests.

Three matching methods (pick at construction time):
  - 'lexical'  : rapidfuzz fuzzy string match (default; no model download).
                 Falls back to a pure-Python scorer if rapidfuzz isn't installed.
  - 'semantic' : multilingual sentence-embeddings + cosine (handles Vietnamese
                 descriptive queries like "xe đua F1"). Embeddings are cached to disk.
  - 'hybrid'   : lexical shortlist re-ranked by semantic similarity (best accuracy).

The .match call also accepts the OTHER NER entities as signals:
  - max_budget / min_budget  -> hard price filter (parse with parse_budget)
  - colors                   -> soft boost when a product colour matches
  - type_hint                -> soft boost when the product 'type' matches

Example
-------
    from product_matcher import ProductMatcher, parse_budget

    pm = ProductMatcher('final_data/products_2010_2026_updated.json')   # lexical
    pm.match('mẹc f1', top_k=3, max_budget=parse_budget('1m'))
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

# Optional deps (graceful)
try:
    from rapidfuzz import fuzz, process
    _HAS_RAPIDFUZZ = True
except ImportError:                      # pragma: no cover
    _HAS_RAPIDFUZZ = False

# Informal / Vietnamese aliases -> canonical English terms used in product names.
ALIAS = {
    'mẹc': 'mercedes', 'méc': 'mercedes', 'merc': 'mercedes', 'benz': 'mercedes',
    'lambo': 'lamborghini', 'ferari': 'ferrari', 'pót-sờ': 'porsche', 'pooc': 'porsche',
    'bu-ga-ti': 'bugatti', 'mc laren': 'mclaren',
}
# Noise words to drop from a customer query before matching.
STOPWORDS = {
    'lego', 'bộ', 'con', 'cái', 'chiếc', 'set', 'mô', 'hình', 'mô hình',
    'về', 'shop', 'sốp', 'cho', 'mình', 'em', 'mua', 'đặt', 'này', 'kia',
}

_SEP_RE = re.compile(r'[_:\-/]')
_WS_RE  = re.compile(r'\s+')


def _normalize(text: str) -> str:
    return _WS_RE.sub(' ', _SEP_RE.sub(' ', str(text).lower())).strip()


def parse_budget(text) -> int | None:
    """Turn an NER budget string into VND, e.g. '300k'->300000, '1m'->1000000,
    '2-300k'->300000 (takes the upper bound), '150 k'->150000. Returns None if unparseable."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(text)
    s = str(text).lower().replace(' ', '')
    nums = re.findall(r'(\d+(?:[.,]\d+)?)\s*(triệu|trieu|tr|nghìn|nghin|ngàn|ngan|k|m)?', s)
    vals = []
    for num, unit in nums:
        if not num:
            continue
        v = float(num.replace(',', '.'))
        if unit in ('k', 'nghìn', 'nghin', 'ngàn', 'ngan'):
            v *= 1_000
        elif unit in ('tr', 'triệu', 'trieu', 'm'):    # 1tr / 1 triệu = 1.000.000
            v *= 1_000_000
        elif v < 1000:           # bare "300" in budget context usually means nghìn
            v *= 1_000
        vals.append(int(v))
    return max(vals) if vals else None


class ProductMatcher:
    def __init__(self, products_path, method: str = 'lexical',
                 embed_model: str = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'):
        assert method in ('lexical', 'semantic', 'hybrid')
        self.method  = method
        self.path    = Path(products_path)
        self.products = json.loads(self.path.read_text(encoding='utf-8'))

        # Field-aware matching (dialogue_rules.md ):
        #   PRIMARY fuzzy target = name (English) + type (Vietnamese, the only VN field)
        #     -> lets Vietnamese descriptive queries ("rồng", "xe") match via `type`
        #        ("Rồng", "Xe hơi") while keeping English product-name precision.
        #   SECONDARY boost tokens = category (English themes) + brand.
        #   color = boost (set membership on `color`), budget/stock = numeric filter.
        # NOTE: all-LEGO today. When other brands/non-LEGO arrive, drop 'lego' from STOPWORDS.
        self._primary = [_normalize(f"{p.get('name', '')} {p.get('type', '')}") for p in self.products]
        self._sec     = [_normalize(f"{p.get('category', '')} {p.get('brand', '')}") for p in self.products]
        self._primary_tokens = [set(s.split) for s in self._primary]
        self._sec_tokens     = [set(s.split) for s in self._sec]

        self._embed_model_name = embed_model
        self._embedder = None
        self._embeddings = None
        if method in ('semantic', 'hybrid'):
            self._load_embeddings

    # query cleanup
    @staticmethod
    def _expand_query(query: str) -> str:
        toks = []
        for t in _normalize(query).split:
            if t in STOPWORDS:
                continue
            toks.append(ALIAS.get(t, t))
        return ' '.join(toks).strip()

    # lexical scoring
    def _lexical_scores(self, q: str, shortlist=None):
        """Return list of (index, score 0-100). Fuzzy on PRIMARY (name + type),
        plus a small bonus when a query token hits SECONDARY (category + brand)."""
        idxs = list(range(len(self._primary))) if shortlist is None else list(shortlist)
        qt = set(q.split)
        if _HAS_RAPIDFUZZ:
            choices = {i: self._primary[i] for i in idxs}
            res = process.extract(q, choices, scorer=fuzz.token_set_ratio, limit=len(choices))
            scored = []
            for _, s, i in res:
                if qt & self._sec_tokens[i]:          # category/brand theme bonus
                    s = min(100, s + 5)
                scored.append((i, s))
            return scored
        # pure-Python fallback: PRIMARY-token coverage + difflib + weak secondary bonus
        from difflib import SequenceMatcher
        out = []
        for i in idxs:
            prim_inter = len(qt & self._primary_tokens[i])
            sec_inter  = len(qt & self._sec_tokens[i])
            if not prim_inter and not sec_inter:
                continue
            prim_cov = prim_inter / max(len(qt), 1)
            seq = SequenceMatcher(None, q, self._primary[i]).ratio
            score = 100 * (0.6 * prim_cov + 0.2 * seq) + 8 * (sec_inter / max(len(qt), 1))
            out.append((i, min(100.0, score)))
        return out

    # semantic scoring
    def _load_embeddings(self):
        from sentence_transformers import SentenceTransformer
        self._embedder = SentenceTransformer(self._embed_model_name)
        cache = self.path.with_suffix('.emb.npy')
        if cache.exists:
            emb = np.load(cache)
            if emb.shape[0] == len(self.products):
                self._embeddings = emb
                return
        emb = self._embedder.encode(
            [p.get('name', '') for p in self.products],
            batch_size=64, show_progress_bar=True, normalize_embeddings=True,
        ).astype(np.float32)
        np.save(cache, emb)
        self._embeddings = emb

    def _semantic_scores(self, raw_query: str, shortlist=None):
        qv = self._embedder.encode([raw_query], normalize_embeddings=True)[0]
        idxs = np.arange(len(self.products)) if shortlist is None else np.array(shortlist)
        sims = self._embeddings[idxs] @ qv            # cosine (vectors are normalized)
        return [(int(i), float(100 * s)) for i, s in zip(idxs, sims)]

    # public API
    def match(self, product_name: str, top_k: int = 5, score_cutoff: float = 50.0,
              max_budget=None, min_budget=None, colors=None, type_hint=None):
        """Return up to top_k ranked product dicts for an NER PRODUCT_NAME.

        max_budget / min_budget : ints in VND (hard filter). Use parse_budget.
        colors                  : list of Vietnamese colour words (soft +12 boost).
        type_hint               : NER TYPE string (soft +8 boost when it matches).
        """
        q = self._expand_query(product_name or '')
        if not q:
            return []

        # 1) candidate scores
        if self.method == 'lexical':
            scores = self._lexical_scores(q)
        elif self.method == 'semantic':
            scores = self._semantic_scores(product_name)
        else:  # hybrid: lexical shortlist, then semantic re-rank
            lex = sorted(self._lexical_scores(q), key=lambda x: -x[1])[:max(top_k * 10, 50)]
            shortlist = [i for i, _ in lex] or None
            sem = dict(self._semantic_scores(product_name, shortlist=shortlist))
            lexd = dict(lex)
            scores = [(i, 0.5 * lexd.get(i, 0) + 0.5 * sem.get(i, 0)) for i in (shortlist or [])]

        # 2) filters + soft boosts
        colset = {c.lower() for c in colors} if colors else set
        results = []
        for i, s in scores:
            p = self.products[i]
            price = p.get('price')
            if max_budget is not None and price is not None and price > max_budget:
                continue
            if min_budget is not None and price is not None and price < min_budget:
                continue
            if colset and colset & {c.lower() for c in p.get('color', [])}:
                s += 12
            if type_hint and _normalize(type_hint) in _normalize(p.get('type', '')):
                s += 8
            if s >= score_cutoff:
                results.append((s, i))

        results.sort(key=lambda x: -x[0])
        out = []
        for s, i in results[:top_k]:
            p = self.products[i]
            out.append({
                'product_id': p.get('product_id'),
                'name':       p.get('name'),
                'price':      p.get('price'),
                'color':      p.get('color'),
                'type':       p.get('type'),
                'score':      round(float(s), 1),
            })
        return out

    def suggest(self, budget=None, colors=None, type_hint=None, top_k: int = 3):
        """Browse-by-attribute suggestion when there is NO product name (R1).
        Picks products closest-to-budget (best value) within the cap, filtered by
        type / colour when given (with graceful fallback so results are never empty)."""
        colset = {c.lower() for c in colors} if colors else set
        th_toks = set(_normalize(type_hint).split) if type_hint else set

        def pool(use_color, use_type):
            out = []
            for i, p in enumerate(self.products):
                price = p.get('price')
                if budget is not None and price is not None and price > budget:
                    continue
                if use_type and th_toks and not (th_toks & self._primary_tokens[i]):  # type ∈ primary
                    continue
                if use_color and colset and not (colset & {c.lower() for c in p.get('color', [])}):
                    continue
                out.append(i)
            return out

        # progressively relax filters so we always return something
        idxs = (pool(True, True) or pool(False, True) or pool(False, False))
        # closest-to-budget first (best value); if no budget, just by price desc
        idxs.sort(key=lambda i: -(self.products[i].get('price') or 0))
        out = []
        for i in idxs[:top_k]:
            p = self.products[i]
            out.append({'product_id': p.get('product_id'), 'name': p.get('name'),
                        'price': p.get('price'), 'color': p.get('color'),
                        'type': p.get('type'), 'score': None})
        return out

    def get(self, product_id):
        """Return the full catalogue record for a product_id (for ask_product_info etc.)."""
        for p in self.products:
            if p.get('product_id') == product_id:
                return p
        return None

    def match_from_entities(self, entities: dict, top_k: int = 5):
        """Convenience: drive .match directly from a NER entities dict
        ({'PRODUCT_NAME': [...], 'MAX_BUDGET': [...], 'PRODUCT_COLOR': [...], 'TYPE': [...]})."""
        def first(key):
            v = entities.get(key)
            return v[0] if isinstance(v, list) and v else (v or None)
        name = first('PRODUCT_NAME')
        if not name:
            return []
        return self.match(
            name, top_k=top_k,
            max_budget=parse_budget(first('MAX_BUDGET')),
            min_budget=parse_budget(first('MIN_BUDGET')),
            colors=entities.get('PRODUCT_COLOR'),
            type_hint=first('TYPE'),
        )


if __name__ == '__main__':
    pm = ProductMatcher('final_data/products_2010_2026_updated.json', method='lexical')
    for q in ['lego city', 'mẹc f1', 'con ferrari', 'porsche 911']:
        print(f'\nQ = {q!r}')
        for r in pm.match(q, top_k=3):
            print(f"  {r['score']:5.1f}  {r['name']}  ({r['price']}đ)")

    # Full NER -> matcher integration
    demo_entities = {'PRODUCT_NAME': ['ferrari'], 'MAX_BUDGET': ['1m'], 'PRODUCT_COLOR': ['đỏ']}
    print('\nFrom NER entities', demo_entities)
    for r in pm.match_from_entities(demo_entities, top_k=3):
        print(f"  {r['score']:5.1f}  {r['name']}  ({r['price']}đ)  colors={r['color']}")
