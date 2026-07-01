import json
from collections import Counter
from pathlib import Path

product = json.loads(Path('ner_data/product_ner.json').read_text(encoding='utf-8'))
budget  = json.loads(Path('ner_data/budget_ner.json').read_text(encoding='utf-8'))
info    = json.loads(Path('ner_data/info_ner.json').read_text(encoding='utf-8'))

# Label counts
all_records = product + budget + info
counts = Counter(e['label'] for r in all_records for e in r['entities'])
print('=== Label counts (all files) ===')
for label, cnt in sorted(counts.items(), key=lambda x: -x[1]):
    print(f'  {label:<20} {cnt}')

# Co-occurrence: SHIP_TIME + SHIP_DATE in same sample
both_ship = [r for r in product if
    any(e['label'] == 'SHIP_TIME' for e in r['entities']) and
    any(e['label'] == 'SHIP_DATE' for e in r['entities'])]
print(f'\n=== SHIP_TIME + SHIP_DATE in same sample: {len(both_ship)} ===')
for r in both_ship[:4]:
    txt = r['text'].replace('\n', ' ').encode('ascii', 'replace').decode
    print(f'  {txt!r}')
    for e in r['entities']:
        lbl = e["label"]
        val = e["text"].encode('ascii','replace').decode
        print(f'    {lbl:<15} {val!r}')

# PRODUCT_COLOR adjacent to PRODUCT_NAME
def adjacent(r, gap=15):
    names  = [e for e in r['entities'] if e['label'] == 'PRODUCT_NAME']
    colors = [e for e in r['entities'] if e['label'] == 'PRODUCT_COLOR']
    for n in names:
        for c in colors:
            if abs(c['start'] - n['end']) <= gap or abs(n['start'] - c['end']) <= gap:
                return True
    return False

adj_color = [r for r in product if adjacent(r)]
print(f'\n=== PRODUCT_COLOR adjacent to PRODUCT_NAME: {len(adj_color)} ===')
for r in adj_color[:4]:
    txt = r['text'].replace('\n', ' ').encode('ascii','replace').decode
    print(f'  {txt!r}')
    for e in r['entities']:
        lbl = e["label"]
        val = e["text"].encode('ascii','replace').decode
        print(f'    {lbl:<15} {val!r}')

# Samples with NO entity (negative examples ratio)
no_ent = {
    'product_ner': sum(1 for r in product if not r['entities']),
    'budget_ner' : sum(1 for r in budget  if not r['entities']),
    'info_ner'   : sum(1 for r in info    if not r['entities']),
}
print('\n=== No-entity samples ===')
for fname, cnt in no_ent.items():
    total = len({'product_ner': product, 'budget_ner': budget, 'info_ner': info}[fname])
    print(f'  {fname:<15} {cnt}/{total} ({cnt/total*100:.0f}%)')

# Entity span length distribution
ship_time_texts = [e['text'] for r in product for e in r['entities'] if e['label'] == 'SHIP_TIME']
ship_date_texts = [e['text'] for r in product for e in r['entities'] if e['label'] == 'SHIP_DATE']
print(f'\n=== SHIP_TIME sample values (first 10) ===')
for t in ship_time_texts[:10]:
    print(f'  {t.encode("ascii","replace").decode!r}')
print(f'\n=== SHIP_DATE sample values (first 10) ===')
for t in ship_date_texts[:10]:
    print(f'  {t.encode("ascii","replace").decode!r}')
print(f'\n=== PRODUCT_COLOR all values ===')
color_texts = [e['text'] for r in product for e in r['entities'] if e['label'] == 'PRODUCT_COLOR']
for t in color_texts:
    print(f'  {t.encode("ascii","replace").decode!r}')
