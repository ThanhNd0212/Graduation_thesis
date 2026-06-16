"""NLU = your trained models: PhoBERT intent classifier + ViSoBERT NER.

No keyword rules — this loads the actual checkpoints and reuses the exact
preprocessing/inference from the notebooks:
  - Intent: normalize() (teen-code) → underthesea word segmentation → PhoBERT → sigmoid
    → per-class thresholds.  Segmentation matches the training pipeline in
    approach3_results/approach3_phobert_normalized_after_trained.ipynb.
  - NER: ViSoBERT token-classification, first-sub-word aggregation (the fixed inference).

API:  analyze(text) -> (intents: list[str], entities: dict[str, list[str]])
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import joblib
import numpy as np
import torch
from transformers import (
    AutoTokenizer,
    AutoModelForSequenceClassification,
    AutoModelForTokenClassification,
)

_ROOT = Path(__file__).resolve().parent.parent
INTENT_DIR     = _ROOT / 'approach3_results' / 'results' / 'intent_model'
INTENT_MLB     = _ROOT / 'approach3_results' / 'results' / 'mlb.joblib'
INTENT_METRICS = _ROOT / 'approach3_results' / 'results' / 'metrics.json'
NER_DIR        = _ROOT / 'ner' / 'results' / 'ner_model'
MAX_LEN = 128

# ── underthesea word segmentation (matches training pipeline) ─────────────────
try:
    from underthesea import word_tokenize as _uts_tokenize
    def _word_segment(text: str) -> str:
        return _uts_tokenize(text, format='text')
    _USE_SEGMENT = True
except ImportError:
    def _word_segment(text: str) -> str:
        return text
    _USE_SEGMENT = False

# ── intent preprocessing: teen-code normalization → word segmentation ─────────
TEEN_CODE = {
    'k': 'không', 'ko': 'không', 'kh': 'không', 'khum': 'không', 'hum': 'không', 'kum': 'không',
    'đc': 'được', 'dc': 'được', 'vs': 'với', 'b': 'bạn', 'm': 'mình',
    'sốp': 'shop', 'sốc': 'shop', 'ck': 'chuyển khoản', 'rep': 'trả lời', 'sp': 'sản phẩm',
    'mn': 'mọi người', 'r': 'rồi', 'rui': 'rồi', 'nha': 'nhé', 'nhen': 'nhé',
}


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r'(.)\1{2,}', r'\1\1', text)
    out = []
    for tok in text.split():
        if re.match(r'^[a-z0-9\-]+$', tok) and tok not in TEEN_CODE:
            out.append(tok)
        else:
            out.append(TEEN_CODE.get(tok, tok))
    normalized = ' '.join(out)
    return _word_segment(normalized) if _USE_SEGMENT else normalized


class IntentClassifier:
    def __init__(self, model_dir=INTENT_DIR, mlb_path=INTENT_MLB,
                 metrics_path=INTENT_METRICS, device=None):
        self.device = device or torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.tokenizer = AutoTokenizer.from_pretrained(str(model_dir), use_fast=False)
        self.model = AutoModelForSequenceClassification.from_pretrained(str(model_dir)).to(self.device).eval()
        self.classes = list(joblib.load(mlb_path).classes_)
        thr = json.loads(Path(metrics_path).read_text(encoding='utf-8'))['thresholds']
        self.thresholds = np.array([thr[c] for c in self.classes], dtype=np.float32)

    @torch.no_grad()
    def predict(self, text: str):
        enc = self.tokenizer(normalize(text), truncation=True, max_length=MAX_LEN,
                             return_tensors='pt').to(self.device)
        proba = torch.sigmoid(self.model(**enc).logits)[0].cpu().numpy()
        return [c for c, p, t in zip(self.classes, proba, self.thresholds) if p >= t]


class NERTagger:
    def __init__(self, model_dir=NER_DIR, device=None):
        self.device = device or torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
        self.model = AutoModelForTokenClassification.from_pretrained(str(model_dir)).to(self.device).eval()
        self.id2label = {int(i): l for i, l in self.model.config.id2label.items()}

    @torch.no_grad()
    def predict(self, text: str):
        enc = self.tokenizer(text, max_length=MAX_LEN, truncation=True,
                             return_offsets_mapping=True, return_tensors='pt')
        offsets  = enc['offset_mapping'][0].tolist()
        word_ids = enc.word_ids(batch_index=0)
        inputs   = {k: v.to(self.device) for k, v in enc.items() if k != 'offset_mapping'}
        preds = self.model(**inputs).logits[0].argmax(-1).tolist()

        # first sub-word carries the tag; continuation sub-words only extend the span
        words, prev = [], None
        for pred, (s, e), wid in zip(preds, offsets, word_ids):
            if wid is None:
                continue
            if wid != prev:
                words.append([self.id2label[pred], s, e])
            else:
                words[-1][2] = e
            prev = wid

        ents, cur = [], None
        for tag, s, e in words:
            if tag == 'O':
                if cur:
                    ents.append(cur); cur = None
            elif tag.startswith('B-'):
                if cur:
                    ents.append(cur)
                cur = {'label': tag[2:], 'start': s, 'end': e}
            else:
                et = tag[2:]
                if cur and cur['label'] == et:
                    cur['end'] = e
                else:
                    if cur:
                        ents.append(cur)
                    cur = {'label': et, 'start': s, 'end': e}
        if cur:
            ents.append(cur)
        for en in ents:
            en['text'] = text[en['start']:en['end']]
        return ents


class NLU:
    """Combine the two trained models behind one .analyze() call."""

    def __init__(self, device=None):
        self.intent = IntentClassifier(device=device)
        self.ner = NERTagger(device=device)

    def analyze(self, text: str):
        intents = self.intent.predict(text)
        entities: dict[str, list[str]] = {}
        for e in self.ner.predict(text):
            entities.setdefault(e['label'], []).append(e['text'])
        return intents, entities
