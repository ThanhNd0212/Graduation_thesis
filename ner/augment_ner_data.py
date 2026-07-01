#!/usr/bin/env python3
"""Augment NER training data using Gemini for weak entity classes.

Targeted (not uniform) augmentation based on current label counts and F1 scores:

  product_ner  +85  PRODUCT_COLOR  (15  -> 100)   F1=0.55
  product_ner  +50  SHIP_TIME+SHIP_DATE adjacent   boundary bleed issue
  product_ner  +77  QUANTITY       (23  -> 100)
  product_ner +109  COMPLEXITY     (91  -> 200)   F1=0.33 — weakest
  product_ner  +94  TYPE           (106 -> 200)   F1=0.40
  budget_ner   +80  MIN_BUDGET     (20  -> 100)   skewed vs MAX_BUDGET
  info_ner     +30  NAME lowercase                 case diversity
  info_ner     +20  CITY abbreviated               abbreviation diversity

NOT augmented (already adequate):
  PRODUCT_NAME(574), PHONE(F1=1.0), ADDRESS(F1=0.73), NAME(F1=0.95), MAX_BUDGET(F1=0.87)

Output:
  ner_data/augmented_product_ner.json
  ner_data/augmented_budget_ner.json
  ner_data/augmented_info_ner.json

Usage:
  python ner/augment_ner_data.py
  python ner/augment_ner_data.py --dry-run
  python ner/augment_ner_data.py --tasks product_color,quantity
"""

import argparse, json, os, re, sys, time
from pathlib import Path
from dotenv import load_dotenv
import google.generativeai as genai

BASE      = Path(__file__).parent.parent
DATA_DIR  = BASE / 'ner_data'
MODEL_NAME    = 'gemini-2.5-flash-lite'
RETRY_LIMIT   = 3
BATCH_SIZE    = 10   # samples per Gemini call
REQUEST_DELAY = 1.0

_FENCE_RE = re.compile(r'```(?:json)?\s*([\s\S]*?)```', re.IGNORECASE)

# Task definitions
TASKS = [
    # product_ner
    {
        'name'           : 'product_color',
        'output_file'    : DATA_DIR / 'augmented_product_ner.json',
        'count'          : 85,
        'required_labels': {'PRODUCT_COLOR'},
        'valid_labels'   : {'PRODUCT_NAME', 'PRODUCT_COLOR', 'TYPE', 'QUANTITY'},
        'default_cats'   : ['ask_product_availability'],
        'prompt'         : """\
Generate {n} realistic Vietnamese informal customer chat messages for a LEGO/toy shop.
Each message MUST mention BOTH a specific toy/LEGO product (PRODUCT_NAME) AND its color (PRODUCT_COLOR).

Use casual Vietnamese: teen code, abbreviations, questions about availability or price are fine.
Colors: xanh, đỏ, vàng, trắng, đen, cam, tím, xanh lá, xanh đậm, xanh nhạt, hồng, bạc, nâu, màu xanh, etc.
Products: Ferrari, Porsche, McLaren, Honda, Toyota Supra, ninja go, lego city, star wars, bộ hoa, bộ xe, etc.

Return ONLY a valid JSON array (no markdown fences, no explanation):
[{{"text":"...","entities":[{{"label":"PRODUCT_NAME","text":"exact substr"}},{{"label":"PRODUCT_COLOR","text":"exact substr"}}],"cats":["ask_product_availability"]}},...]

Rules:
- entity "text" must be an EXACT substring of "text" field — copy character for character.
- PRODUCT_COLOR includes the color word and optional "màu" prefix (e.g. "xanh" or "màu xanh").

Examples:
{{"text":"shop có bộ Toyota Supra màu xanh không ạ","entities":[{{"label":"PRODUCT_NAME","text":"Toyota Supra"}},{{"label":"PRODUCT_COLOR","text":"màu xanh"}}],"cats":["ask_product_availability"]}}
{{"text":"con Ferrari đỏ 1:8 còn hàng k shop","entities":[{{"label":"PRODUCT_NAME","text":"Ferrari"}},{{"label":"PRODUCT_COLOR","text":"đỏ"}}],"cats":["ask_product_availability"]}}
{{"text":"bộ ninja go rồng xanh lá bao nhiêu ạ","entities":[{{"label":"PRODUCT_NAME","text":"ninja go rồng"}},{{"label":"PRODUCT_COLOR","text":"xanh lá"}}],"cats":["ask_product_availability"]}}
{{"text":"shop ơi còn bộ porsche trắng k ạ","entities":[{{"label":"PRODUCT_NAME","text":"porsche"}},{{"label":"PRODUCT_COLOR","text":"trắng"}}],"cats":["ask_product_availability"]}}
{{"text":"có bộ lego city màu vàng không shop","entities":[{{"label":"PRODUCT_NAME","text":"lego city"}},{{"label":"PRODUCT_COLOR","text":"màu vàng"}}],"cats":["ask_product_availability"]}}
{{"text":"mình thích bộ star wars màu đen, còn không","entities":[{{"label":"PRODUCT_NAME","text":"star wars"}},{{"label":"PRODUCT_COLOR","text":"màu đen"}}],"cats":["ask_product_availability"]}}
""",
    },
    {
        'name'           : 'ship_time_date',
        'output_file'    : DATA_DIR / 'augmented_product_ner.json',
        'count'          : 50,
        'required_labels': {'SHIP_TIME', 'SHIP_DATE'},
        'valid_labels'   : {'SHIP_TIME', 'SHIP_DATE', 'PRODUCT_NAME'},
        'default_cats'   : ['delivery_time_requirement'],
        'prompt'         : """\
Generate {n} realistic Vietnamese customer chat messages for a LEGO/toy shop where the customer
specifies BOTH a time of day (SHIP_TIME) AND a date (SHIP_DATE) for delivery.

SHIP_TIME = time of day: sáng, chiều, tối, buổi sáng, buổi chiều, 8h, 9h, 10h, 14h, 15h, 3h chiều, tầm 10h, etc.
SHIP_DATE = specific day/date: hôm nay, ngày mai, mai, thứ 2, thứ 3, thứ 4, thứ 5, thứ 6, thứ 7, cuối tuần, tuần sau, etc.

CRITICAL: SHIP_TIME and SHIP_DATE are ALWAYS TWO separate entities — never merge them.
  "sáng mai"     -> SHIP_TIME="sáng", SHIP_DATE="mai"
  "chiều thứ 5"  -> SHIP_TIME="chiều", SHIP_DATE="thứ 5"
  "3h hôm nay"   -> SHIP_TIME="3h", SHIP_DATE="hôm nay"
  "10h ngày mai" -> SHIP_TIME="10h", SHIP_DATE="ngày mai"

Return ONLY a valid JSON array:
[{{"text":"...","entities":[{{"label":"SHIP_TIME","text":"exact time"}},{{"label":"SHIP_DATE","text":"exact date"}}],"cats":["delivery_time_requirement"]}},...]

Examples:
{{"text":"shop ship cho mình sáng mai được không ạ","entities":[{{"label":"SHIP_TIME","text":"sáng"}},{{"label":"SHIP_DATE","text":"mai"}}],"cats":["delivery_time_requirement"]}}
{{"text":"mình cần nhận chiều thứ 5 nha shop","entities":[{{"label":"SHIP_TIME","text":"chiều"}},{{"label":"SHIP_DATE","text":"thứ 5"}}],"cats":["delivery_time_requirement"]}}
{{"text":"tầm 3h hôm nay giao được không shop ơi","entities":[{{"label":"SHIP_TIME","text":"3h"}},{{"label":"SHIP_DATE","text":"hôm nay"}}],"cats":["delivery_time_requirement"]}}
{{"text":"khoảng 10h sáng mai shop có thể giao không","entities":[{{"label":"SHIP_TIME","text":"10h"}},{{"label":"SHIP_DATE","text":"mai"}}],"cats":["delivery_time_requirement"]}}
{{"text":"buổi tối thứ 7 có ship không ạ","entities":[{{"label":"SHIP_TIME","text":"buổi tối"}},{{"label":"SHIP_DATE","text":"thứ 7"}}],"cats":["delivery_time_requirement"]}}
""",
    },
    {
        'name'           : 'quantity',
        'output_file'    : DATA_DIR / 'augmented_product_ner.json',
        'count'          : 77,
        'required_labels': {'QUANTITY'},
        'valid_labels'   : {'PRODUCT_NAME', 'QUANTITY', 'TYPE', 'PRODUCT_COLOR'},
        'default_cats'   : ['ask_product_availability'],
        'prompt'         : """\
Generate {n} realistic Vietnamese customer chat messages for a LEGO/toy shop where the customer
specifies how many items they want to buy (QUANTITY).

QUANTITY = number + classifier: "2 bộ", "3 cái", "1 set", "4 hộp", "2 hộp", "đôi", "1 chiếc"
Mix of scenarios:
- Simple: "mình muốn mua 2 bộ Ferrari"
- With product: "cho em 3 hộp lego city"
- With color: "lấy 1 bộ màu đỏ đi shop"
- Order style: "đặt 2 cái nha"
Vary quantities: 1, 2, 3, 4, 5, 10... and classifiers: bộ, cái, hộp, set, chiếc

Return ONLY a valid JSON array:
[{{"text":"...","entities":[{{"label":"QUANTITY","text":"exact quantity span"}},...],"cats":["ask_product_availability"]}},...]

Rules:
- QUANTITY includes the number AND the classifier word together (e.g. "2 bộ" not just "2")
- entity "text" must be exact substring of "text"

Examples:
{{"text":"mình muốn mua 2 bộ Ferrari shop ơi","entities":[{{"label":"QUANTITY","text":"2 bộ"}},{{"label":"PRODUCT_NAME","text":"Ferrari"}}],"cats":["ask_product_availability"]}}
{{"text":"cho em đặt 3 hộp lego city","entities":[{{"label":"QUANTITY","text":"3 hộp"}},{{"label":"PRODUCT_NAME","text":"lego city"}}],"cats":["ask_product_availability"]}}
{{"text":"shop lấy 1 bộ thôi nha","entities":[{{"label":"QUANTITY","text":"1 bộ"}}],"cats":["ask_product_availability"]}}
{{"text":"e cần 2 cái, màu xanh nha shop","entities":[{{"label":"QUANTITY","text":"2 cái"}},{{"label":"PRODUCT_COLOR","text":"xanh"}}],"cats":["ask_product_availability"]}}
{{"text":"đặt 4 set ninja go giúp mình","entities":[{{"label":"QUANTITY","text":"4 set"}},{{"label":"PRODUCT_NAME","text":"ninja go"}}],"cats":["ask_product_availability"]}}
""",
    },
    {
        'name'           : 'complexity',
        'output_file'    : DATA_DIR / 'augmented_product_ner.json',
        'count'          : 109,
        'required_labels': {'COMPLEXITY'},
        'valid_labels'   : {'PRODUCT_NAME', 'COMPLEXITY', 'TYPE', 'QUANTITY'},
        'default_cats'   : ['ask_product_availability'],
        'prompt'         : """\
Generate {n} realistic Vietnamese customer chat messages for a LEGO/toy shop where the customer
asks about or specifies the COMPLEXITY (difficulty level) of a LEGO set.

COMPLEXITY = difficulty/skill level of assembly:
  Easy: dễ, dễ lắp, dễ ráp, dễ ghép, không khó, đơn giản, cơ bản, cho bé, trẻ em
  Medium: trung bình, vừa, không quá khó, tầm trung
  Hard: khó, khó lắp, phức tạp, cao cấp, nhiều chi tiết, thử thách, chuyên nghiệp
  New: người mới, newbie, mới chơi, lần đầu, chưa có kinh nghiệm

Mix of:
- Asking about complexity: "bộ này có dễ lắp không"
- Specifying preference: "tìm loại không quá khó"
- Age-based: "cho bé 8 tuổi lắp được không"
- Skill-based: "mới chơi lego lần đầu nên cần loại dễ"

Return ONLY a valid JSON array:
[{{"text":"...","entities":[{{"label":"COMPLEXITY","text":"exact complexity span"}},...],"cats":["ask_product_availability"]}},...]

Rules:
- COMPLEXITY is the difficulty descriptor word/phrase (e.g. "dễ lắp", "khó", "trung bình")
- entity "text" must be exact substring of "text"

Examples:
{{"text":"bộ này có dễ lắp không shop","entities":[{{"label":"COMPLEXITY","text":"dễ lắp"}}],"cats":["ask_product_availability"]}}
{{"text":"tìm bộ không quá khó cho mình với","entities":[{{"label":"COMPLEXITY","text":"không quá khó"}}],"cats":["ask_product_availability"]}}
{{"text":"mới chơi lần đầu cần loại dễ thôi","entities":[{{"label":"COMPLEXITY","text":"dễ"}}],"cats":["ask_product_availability"]}}
{{"text":"shop có bộ trung bình không, không dễ quá cũng không khó quá","entities":[{{"label":"COMPLEXITY","text":"trung bình"}}],"cats":["ask_product_availability"]}}
{{"text":"bộ lego city 60300 khó lắp không ạ","entities":[{{"label":"PRODUCT_NAME","text":"lego city 60300"}},{{"label":"COMPLEXITY","text":"khó lắp"}}],"cats":["ask_product_availability"]}}
{{"text":"mình muốn loại phức tạp một tí, có bộ nào không shop","entities":[{{"label":"COMPLEXITY","text":"phức tạp"}}],"cats":["ask_product_availability"]}}
""",
    },
    {
        'name'           : 'type_entity',
        'output_file'    : DATA_DIR / 'augmented_product_ner.json',
        'count'          : 94,
        'required_labels': {'TYPE'},
        'valid_labels'   : {'PRODUCT_NAME', 'TYPE', 'COMPLEXITY', 'QUANTITY', 'PRODUCT_COLOR'},
        'default_cats'   : ['ask_product_availability'],
        'prompt'         : """\
Generate {n} realistic Vietnamese customer chat messages for a LEGO/toy shop where the customer
specifies the TYPE (theme/category) of LEGO or toy they want.

TYPE = product theme or category:
  Vehicles: xe hơi, xe đua, xe tải, xe máy, xe lửa, tàu hỏa, máy bay, tàu thuyền, thuyền, xe cứu thương
  Buildings: nhà, tòa nhà, kiến trúc, lâu đài, thành phố, dinh thự
  Nature: hoa, cây, động vật, thú cưng, khủng long, rừng
  Characters: nhân vật, siêu nhân, robot, người máy, anh hùng
  Sets: bộ ghép hình, bộ sáng tạo, bộ technic, bộ creator

Mix scenarios:
- "tìm loại xe hơi", "có bộ hoa không", "muốn kiểu nhân vật"
- With other entities: "bộ xe hơi màu đỏ còn không"
- Question style: "shop có loại kiến trúc không ạ"

Return ONLY a valid JSON array:
[{{"text":"...","entities":[{{"label":"TYPE","text":"exact type span"}},...],"cats":["ask_product_availability"]}},...]

Rules:
- TYPE is the category/theme word or short phrase
- entity "text" must be exact substring of "text"

Examples:
{{"text":"tìm loại xe hơi giúp mình shop","entities":[{{"label":"TYPE","text":"xe hơi"}}],"cats":["ask_product_availability"]}}
{{"text":"shop có bộ hoa không ạ","entities":[{{"label":"TYPE","text":"hoa"}}],"cats":["ask_product_availability"]}}
{{"text":"mình muốn loại kiến trúc, có không shop","entities":[{{"label":"TYPE","text":"kiến trúc"}}],"cats":["ask_product_availability"]}}
{{"text":"có bộ nhân vật siêu anh hùng không ạ","entities":[{{"label":"TYPE","text":"nhân vật"}}],"cats":["ask_product_availability"]}}
{{"text":"tìm bộ động vật dễ lắp cho bé","entities":[{{"label":"TYPE","text":"động vật"}},{{"label":"COMPLEXITY","text":"dễ lắp"}}],"cats":["ask_product_availability"]}}
{{"text":"shop ơi loại xe đua còn hàng không","entities":[{{"label":"TYPE","text":"xe đua"}}],"cats":["ask_product_availability"]}}
""",
    },
    # budget_ner
    {
        'name'           : 'budget_range',
        'output_file'    : DATA_DIR / 'augmented_budget_ner.json',
        'count'          : 80,
        'required_labels': {'MIN_BUDGET', 'MAX_BUDGET'},
        'valid_labels'   : {'MIN_BUDGET', 'MAX_BUDGET'},
        'default_cats'   : ['provide_budget'],
        'prompt'         : """\
Generate {n} realistic Vietnamese customer chat messages for a LEGO/toy shop where the customer
gives a PRICE RANGE with both a lower (MIN_BUDGET) and upper (MAX_BUDGET) bound.

Formats (all must include BOTH min and max):
  "tầm X đến Y"       -> MIN_BUDGET=X, MAX_BUDGET=Y
  "khoảng X-Y"        -> MIN_BUDGET=X, MAX_BUDGET=Y
  "từ X tới Y"        -> MIN_BUDGET=X, MAX_BUDGET=Y
  "X đến Y là ổn"     -> MIN_BUDGET=X, MAX_BUDGET=Y

Currency units to use (include with number): k, tr, triệu, nghìn, củ, m, xị
Ranges to use: 100-300k, 200-500k, 300k-1tr, 500k-1.5tr, 1-2 triệu, 2-5 triệu, etc.

Return ONLY a valid JSON array:
[{{"text":"...","entities":[{{"label":"MIN_BUDGET","text":"exact lower"}},{{"label":"MAX_BUDGET","text":"exact upper"}}],"cats":["provide_budget"]}},...]

Rules:
- Both MIN_BUDGET and MAX_BUDGET MUST appear in every sample
- Include the currency unit as part of the entity text if it appears right after the number
- entity "text" must be exact substring of "text"

Examples:
{{"text":"shop ơi bên mình có mẫu lego nào tầm 200k đến 300k không ạ","entities":[{{"label":"MIN_BUDGET","text":"200k"}},{{"label":"MAX_BUDGET","text":"300k"}}],"cats":["provide_budget"]}}
{{"text":"budget của mình khoảng 500 nghìn tới 1 triệu thôi","entities":[{{"label":"MIN_BUDGET","text":"500 nghìn"}},{{"label":"MAX_BUDGET","text":"1 triệu"}}],"cats":["provide_budget"]}}
{{"text":"tìm bộ từ 1 đến 2 củ cho mình","entities":[{{"label":"MIN_BUDGET","text":"1"}},{{"label":"MAX_BUDGET","text":"2 củ"}}],"cats":["provide_budget"]}}
{{"text":"khoảng 300-500k là ổn nha shop","entities":[{{"label":"MIN_BUDGET","text":"300"}},{{"label":"MAX_BUDGET","text":"500k"}}],"cats":["provide_budget"]}}
{{"text":"tầm 1.5tr đến 2tr ạ","entities":[{{"label":"MIN_BUDGET","text":"1.5tr"}},{{"label":"MAX_BUDGET","text":"2tr"}}],"cats":["provide_budget"]}}
""",
    },
    # info_ner
    {
        'name'           : 'name_lowercase',
        'output_file'    : DATA_DIR / 'augmented_info_ner.json',
        'count'          : 30,
        'required_labels': {'NAME'},
        'valid_labels'   : {'NAME', 'PHONE', 'ADDRESS', 'CITY'},
        'default_cats'   : ['provide_cus_inf'],
        'prompt'         : """\
Generate {n} realistic Vietnamese customer chat messages where the customer provides their name
in LOWERCASE (not capitalized) — common in casual chat.

Mix of scenarios:
  - Name only: "tên mình là thành"
  - Name + phone: "người nhận: ngọc / 0912345678"
  - Name + address
  - Full shipping info (name + phone + address)

Common lowercase Vietnamese names: thành, ngọc, minh, hương, lan, hà, linh, anh, tùng, nam, mai, thu, hoa, etc.

Return ONLY a valid JSON array:
[{{"text":"...","entities":[{{"label":"NAME","text":"lowercase name"}},...],"cats":["provide_cus_inf"]}},...]

Rules:
- NAME must be in lowercase in the text
- Other entities (PHONE, ADDRESS, CITY) follow normal formatting
- entity "text" must be exact substring of "text"

Examples:
{{"text":"tên mình là thành nha shop","entities":[{{"label":"NAME","text":"thành"}}],"cats":["provide_cus_inf"]}}
{{"text":"người nhận: ngọc\\n0912345678\\n23 Lý Thường Kiệt Hà Nội","entities":[{{"label":"NAME","text":"ngọc"}},{{"label":"PHONE","text":"0912345678"}},{{"label":"ADDRESS","text":"23 Lý Thường Kiệt Hà Nội"}},{{"label":"CITY","text":"Hà Nội"}}],"cats":["provide_cus_inf"]}}
{{"text":"shop ghi tên là minh nhé, sđt 0987654321","entities":[{{"label":"NAME","text":"minh"}},{{"label":"PHONE","text":"0987654321"}}],"cats":["provide_cus_inf"]}}
""",
    },
    {
        'name'           : 'city_abbrev',
        'output_file'    : DATA_DIR / 'augmented_info_ner.json',
        'count'          : 20,
        'required_labels': {'CITY'},
        'valid_labels'   : {'NAME', 'PHONE', 'ADDRESS', 'CITY'},
        'default_cats'   : ['provide_cus_inf'],
        'prompt'         : """\
Generate {n} realistic Vietnamese customer chat messages where the customer mentions a city
using ABBREVIATIONS or informal forms.

Abbreviations to use (mix of uppercase and lowercase):
  hn / HN -> Hà Nội
  hp / HP -> Hải Phòng
  hcm / HCM / tphcm / sg / SG -> Hồ Chí Minh / Sài Gòn
  dn / ĐN -> Đà Nẵng
  ct / CT -> Cần Thơ
  vt / VT -> Vũng Tàu
  qn -> Quảng Ninh

Return ONLY a valid JSON array:
[{{"text":"...","entities":[{{"label":"CITY","text":"exact abbrev from text"}},...],"cats":["provide_cus_inf"]}},...]

Rules:
- CITY "text" must be the EXACT abbreviation as it appears in the message (e.g. "hn" not "Hà Nội")
- Include both providing address and asking about shipping to that city

Examples:
{{"text":"em ở hn ạ, ship được không shop","entities":[{{"label":"CITY","text":"hn"}}],"cats":["provide_cus_inf"]}}
{{"text":"phí ship về HP bao nhiêu vậy shop","entities":[{{"label":"CITY","text":"HP"}}],"cats":["ask_shipping_fee"]}}
{{"text":"mình ở SG shop có ship không","entities":[{{"label":"CITY","text":"SG"}}],"cats":["provide_cus_inf"]}}
{{"text":"giao về hcm được không ạ","entities":[{{"label":"CITY","text":"hcm"}}],"cats":["provide_cus_inf"]}}
""",
    },
]

# Helpers
def parse_response(raw: str) -> list[dict]:
    raw = raw.strip()
    m = _FENCE_RE.search(raw)
    if m:
        raw = m.group(1).strip()
    data = json.loads(raw)
    return data if isinstance(data, list) else []


def find_spans(text: str, entities: list[dict]) -> list[dict]:
    spans, search_pos = [], {}
    for ent in entities:
        label  = ent.get('label', '').upper()
        needle = ent.get('text', '')
        if not label or not needle:
            continue
        key      = (label, needle)
        from_pos = search_pos.get(key, 0)
        idx      = text.find(needle, from_pos)
        if idx == -1:
            stripped = needle.strip()
            if stripped != needle:
                idx = text.find(stripped, from_pos)
                if idx != -1:
                    needle = stripped
        if idx == -1:
            print(f'    [WARN] not found: {needle!r}')
            continue
        spans.append({'start': idx, 'end': idx + len(needle), 'label': label, 'text': needle})
        search_pos[key] = idx + 1
    return sorted(spans, key=lambda s: s['start'])


def validate_sample(sample: dict, required_labels: set, valid_labels: set) -> bool:
    if not sample.get('text', '').strip():
        return False
    labels = {e.get('label', '').upper() for e in sample.get('entities', [])}
    return required_labels.issubset(labels)


def call_gemini(model, prompt: str, dry_run: bool) -> list[dict]:
    if dry_run:
        print('  [DRY-RUN] skip API call')
        return []
    for attempt in range(RETRY_LIMIT):
        try:
            resp = model.generate_content(prompt)
            return parse_response(resp.text)
        except json.JSONDecodeError as e:
            print(f'  [WARN] JSON parse error (attempt {attempt+1}): {e}')
        except Exception as e:
            print(f'  [WARN] API error (attempt {attempt+1}): {e}')
        if attempt < RETRY_LIMIT - 1:
            time.sleep(2 ** attempt)
    return []


def load_existing(path: Path) -> list[dict]:
    if not path.exists:
        return []
    return json.loads(path.read_text(encoding='utf-8'))


def save_json(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


# Main
def run_task(task: dict, gemini_model, dry_run: bool) -> list[dict]:
    name            = task['name']
    count           = task['count']
    required_labels = task['required_labels']
    valid_labels    = task['valid_labels']
    prompt_template = task['prompt']
    default_cats    = task['default_cats']

    collected: list[dict] = []
    attempts  = 0
    max_attempts = (count // BATCH_SIZE + 2) * 3

    print(f'\n Task: {name}  (target: {count} samples) ')
    while len(collected) < count and attempts < max_attempts:
        remaining = count - len(collected)
        batch_n   = min(BATCH_SIZE, remaining + 5)
        prompt    = prompt_template.format(n=batch_n)

        print(f'  Requesting {batch_n} samples... ({len(collected)}/{count} collected)')
        raw_samples = call_gemini(gemini_model, prompt, dry_run)

        accepted = 0
        for s in raw_samples:
            if len(collected) >= count:
                break
            if not validate_sample(s, required_labels, valid_labels):
                continue
            text     = s['text']
            spans    = find_spans(text, s.get('entities', []))
            span_lbs = {sp['label'] for sp in spans}
            if not required_labels.issubset(span_lbs):
                print(f'  [SKIP] required labels not findable: {text[:60]!r}')
                continue
            spans = [sp for sp in spans if sp['label'] in valid_labels]
            collected.append({
                'text'    : text,
                'cats'    : s.get('cats', default_cats),
                'entities': spans,
            })
            accepted += 1

        print(f'  Accepted {accepted}/{len(raw_samples)} from this batch')
        attempts += 1
        if not dry_run:
            time.sleep(REQUEST_DELAY)

    print(f'  Done: {len(collected)}/{count} samples collected')
    return collected


def main:
    parser = argparse.ArgumentParser
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--tasks', default='',
                        help='Comma-separated task names to run (default: all). '
                             'Names: product_color, ship_time_date, quantity, complexity, '
                             'type_entity, budget_range, name_lowercase, city_abbrev')
    args = parser.parse_args

    task_filter = set(args.tasks.split(',')) if args.tasks else set
    active_tasks = [t for t in TASKS if not task_filter or t['name'] in task_filter]
    if not active_tasks:
        sys.exit(f'[ERROR] No matching tasks for: {args.tasks}')

    load_dotenv(BASE / '.env')
    api_key = os.getenv('GOOGLE_API_KEY', '')
    if not api_key and not args.dry_run:
        sys.exit('[ERROR] GOOGLE_API_KEY not set')

    if not args.dry_run:
        genai.configure(api_key=api_key)
        gemini_model = genai.GenerativeModel(MODEL_NAME)
    else:
        gemini_model = None

    print(f'Running {len(active_tasks)} task(s): {[t["name"] for t in active_tasks]}')

    output_buckets: dict[Path, list[dict]] = {}
    for task in active_tasks:
        new_samples = run_task(task, gemini_model, args.dry_run)
        out = task['output_file']
        output_buckets.setdefault(out, []).extend(new_samples)

    for out_path, new_samples in output_buckets.items():
        existing = load_existing(out_path)
        merged   = existing + new_samples
        save_json(out_path, merged)
        print(f'\nSaved {len(new_samples)} new samples to {out_path}')
        print(f'Total in file: {len(merged)}')

    print('\nAll tasks complete.')
    print('Next: upload augmented_*.json to Colab ner_data/ and retrain.')


if __name__ == '__main__':
    main
