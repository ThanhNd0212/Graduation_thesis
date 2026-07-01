#!/usr/bin/env python3
"""NER inference — load trained ViSoBERT model and predict entities.

Usage:
    # As a module
    from ner.ner_predict import predict_ner
    entities = predict_ner("Nguyên Thảo\n0912495077\nK45/10B Dũng Sĩ Thanh Khê")

    # As a script
    python ner_predict.py
"""

from pathlib import Path
from transformers import AutoTokenizer, AutoModelForTokenClassification
import torch

# Config
MODEL_DIR  = Path(__file__).parent / 'results' / 'ner_model'
MAX_LENGTH = 128
DEVICE     = torch.device('cpu')

# Load model + tokenizer
tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR))
model     = AutoModelForTokenClassification.from_pretrained(
    str(MODEL_DIR),
    low_cpu_mem_usage=True,   # stream weights one layer at a time -> peak RAM ~halved
    torch_dtype=torch.float32,
)
model.eval
# no .to(DEVICE) needed — already on CPU by default

id2label = model.config.id2label  # loaded from config.json


# Inference
def predict_ner(text: str) -> list[dict]:
    """Return list of detected entities with span offsets.

    Each entity: {'label': str, 'start': int, 'end': int, 'text': str}
    """
    enc = tokenizer(
        text,
        max_length=MAX_LENGTH,
        truncation=True,
        return_offsets_mapping=True,
        return_tensors='pt',
    )
    offset_mapping = enc.pop('offset_mapping')[0].tolist
    word_ids       = tokenizer(text, max_length=MAX_LENGTH, truncation=True).word_ids
    enc            = {k: v.to(DEVICE) for k, v in enc.items()}

    with torch.inference_mode:   # lighter than no_grad — no version tracking at all
        preds = model(**enc).logits[0].argmax(-1).tolist

    entities = []
    cur_label, cur_start, cur_end = None, 0, 0

    for pred, offset, wid in zip(preds, offset_mapping, word_ids):
        if wid is None:
            continue
        tok_s, tok_e = offset
        if tok_s == tok_e == 0:
            continue

        tag = id2label[pred]

        if tag == 'O':
            if cur_label:
                entities.append({
                    'label': cur_label,
                    'start': cur_start,
                    'end'  : cur_end,
                    'text' : text[cur_start:cur_end],
                })
            cur_label = None

        elif tag.startswith('B-'):
            if cur_label:
                entities.append({
                    'label': cur_label,
                    'start': cur_start,
                    'end'  : cur_end,
                    'text' : text[cur_start:cur_end],
                })
            cur_label, cur_start, cur_end = tag[2:], tok_s, tok_e

        elif tag.startswith('I-'):
            if cur_label == tag[2:]:
                cur_end = tok_e
            else:
                if cur_label:
                    entities.append({
                        'label': cur_label,
                        'start': cur_start,
                        'end'  : cur_end,
                        'text' : text[cur_start:cur_end],
                    })
                cur_label, cur_start, cur_end = tag[2:], tok_s, tok_e

    if cur_label:
        entities.append({
            'label': cur_label,
            'start': cur_start,
            'end'  : cur_end,
            'text' : text[cur_start:cur_end],
        })

    return entities

def extract_entities_from_text(text, model, tokenizer, id2label, device='cpu'):
    model.eval

    inputs = tokenizer(
        text,
        return_offsets_mapping=True,
        truncation=True,
        max_length=128,
        return_tensors="pt"
    )

    input_ids      = inputs['input_ids'].to(device)
    attention_mask = inputs['attention_mask'].to(device)
    offset_mapping = inputs['offset_mapping'][0].tolist
    word_ids       = inputs.word_ids(batch_index=0)

    with torch.inference_mode:
        outputs     = model(input_ids=input_ids, attention_mask=attention_mask)
        predictions = outputs.logits.argmax(-1)[0].cpu.tolist

    extracted_chunks = []
    current_entity   = None
    prev_word_id     = None

    for pred_id, offset, word_id in zip(predictions, offset_mapping, word_ids):
        # Special tokens [CLS] / [SEP]
        if word_id is None:
            if current_entity:
                extracted_chunks.append(current_entity)
                current_entity = None
            prev_word_id = None
            continue

        start_char, end_char = offset

        # Continuation subword: extend the current entity span, do not re-predict
        if word_id == prev_word_id:
            if current_entity:
                current_entity['end'] = end_char
            prev_word_id = word_id
            continue

        prev_word_id = word_id
        tag = id2label[pred_id]

        if tag == 'O':
            if current_entity:
                extracted_chunks.append(current_entity)
                current_entity = None
            continue

        prefix, entity_type = tag.split('-', 1)

        if prefix == 'B':
            if current_entity:
                extracted_chunks.append(current_entity)
            current_entity = {'type': entity_type, 'start': start_char, 'end': end_char}

        elif prefix == 'I':
            if current_entity and current_entity['type'] == entity_type:
                current_entity['end'] = end_char
            else:
                # I tag without a matching B — treat as a new entity
                if current_entity:
                    extracted_chunks.append(current_entity)
                current_entity = {'type': entity_type, 'start': start_char, 'end': end_char}

    if current_entity:
        extracted_chunks.append(current_entity)

    # Map spans back to source text and clean up
    final_results = {}
    for ent in extracted_chunks:
        cleaned = text[ent['start']:ent['end']].strip().lstrip(':').strip()
        if not cleaned:
            continue
        final_results.setdefault(ent['type'], [])
        if cleaned not in final_results[ent['type']]:
            final_results[ent['type']].append(cleaned)

    return final_results


# Interactive loop
if __name__ == '__main__':
    print('NER — ViSoBERT  (nhập tin nhắn, Enter trống để xác nhận, "q" để thoát)\n')
    while True:
        print('Nhập tin nhắn (Enter trống để kết thúc nhập):')
        lines = []
        while True:
            line = input
            if line.strip().lower() == 'q':
                print('Thoát.')
                exit
            if line == '':
                break
            lines.append(line)

        if not lines:
            continue

        text    = '\n'.join(lines)
        results = extract_entities_from_text(text, model, tokenizer, id2label, device='cpu')

        print('\nKết quả:')
        if results:
            for entity_type, values in results.items():
                for v in values:
                    print(f'  {entity_type:<15} {v}')
        else:
            print('  (không tìm thấy entity)')
        print
