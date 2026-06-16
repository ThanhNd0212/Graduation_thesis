#!/usr/bin/env python3
"""NER annotation using Gemini 2.5 Flash Lite.

Pipeline:
  1. Read every message from final_data/provide_cus_inf.jsonl
  2. Ask Gemini to return entity texts (NAME / PHONE / ADDRESS) as JSON
  3. Compute character offsets with str.find()
  4. Save to final_data/ner_annotations.json

Usage:
  python annotate_ner.py              # full run
  python annotate_ner.py --resume     # skip already-processed records
  python annotate_ner.py --dry-run    # print prompts, no API calls
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
import google.generativeai as genai

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE        = Path(__file__).parent
INPUT_FILE  = BASE / "final_data" / "product.jsonl"
OUTPUT_FILE = BASE / "final_data" / "product_ner.json"
CHECKPOINT  = BASE / "final_data" / "ner_checkpoint.jsonl"  # one JSON line per done record

# ── Gemini config ──────────────────────────────────────────────────────────────
# Check available model IDs: https://ai.google.dev/gemini-api/docs/models
MODEL_NAME    = "gemini-2.5-flash-lite"
RETRY_LIMIT   = 3
REQUEST_DELAY = 0.6   # seconds between API calls (stay under rate limits)
SAVE_EVERY    = 10    # flush checkpoint every N records

# ── Extraction prompt ──────────────────────────────────────────────────────────
PROMPT_TEMPLATE = """\
You are a Vietnamese Named Entity Recognition (NER) system for customer chat messages in a toy/LEGO shop.

Your task is to identify and extract 9 entities from the input text.

Entity Types
PRODUCT_NAME:
-The specific product that the customer is looking for.
-The name may be complete, incomplete, abbreviated, or described by its characteristics.
-If the customer mentions the brand together with the product, include the entire phrase.
Examples:
-"lego loopy" -> PRODUCT_NAME = "lego loopy"
-"lego hoa hồng" -> PRODUCT_NAME = "lego hoa hồng"
-"bộ xe ferrari" -> PRODUCT_NAME = "bộ xe ferrari"

PRODUCT_COLOR:
-The color of the product.
Examples:
-đỏ
-xanh dương
-màu trắng
-hồng pastel

COMPLEXITY:
-Any expression describing the intended difficulty level, target user, or product complexity.
This may include:
-age recommendations
-easy or difficult level
-beginner or advanced users
-few or many pieces
-number of pieces
-child or adult targets
Examples:
-cho bé 5 tuổi
-cho trẻ em
-người mới chơi
-dân chơi lâu năm
-dễ lắp
-khó một chút
-khoảng 500 mảnh
-2000 mảnh
-ít mảnh
-nhiều mảnh

TYPE:
-The category or theme of the product.
Examples:
-hoa
-xe
-động vật
-phi thuyền
-kiến trúc
-nhân vật
-siêu anh hùng
TYPE may overlap with PRODUCT_NAME.

SHIP_TIME:
-The time of day that the customer wants to receive the order.
-Only extract time expressions.
-Examples:
-sáng, chiều
-8 giờ tối
-5h chiều
-khoảng 9h
-trước 12h

SHIP_DATE:
-The date that the customer wants to receive the order.
-Extract the complete date expression exactly as written.
This includes:
-t7, t2, ...
-weekdays
-calendar dates
-month references
-Vietnamese holiday
-relative dates
Examples:
- sáng mai, chiều mai, ngày kia
-thứ hai
-thứ bảy tuần này
-ngày 20 tháng 7
-20/7
-Noel
-Tết
-trung thu
-ngày mai
-cuối tuần

QUANTITY:
-The number of products the customer wants to buy.
Examples:
-1 bộ
-2 hộp
-mua 3 cái
-lấy 5 sản phẩm

SCALE:
- The scale of the product
Example:
- tỉ lệ 1/8
- 1:14
- 1-144

AUTHENTICITY:
- expression indicating that the customer is concerned about whether the product is genuine, authentic, officially licensed, or an original brand product.

  This includes:

  - genuine products
  - authentic products
  - official products
  - officially licensed products
  - original brand products
  - non-counterfeit products

  Examples:

  - hàng chính hãng
  - hàng auth
  - auth
  - hàng thật
  - lego chính hãng
  - lego xịn
  - hàng fake
  - hàng nhái
  - fake
  - fake 1
  - replica
  - rep
  - hàng copy


Annotation Rules
Copy entity text EXACTLY as it appears in the input.
Preserve all original characters, spacing, punctuation, and diacritics.
Do NOT normalize, rewrite, or paraphrase.
If the same entity appears more than once, extract every occurrence separately.
Entities are allowed to overlap.
PRODUCT_NAME and TYPE may overlap.
Only extract information explicitly mentioned in the text.
Do NOT infer or hallucinate missing information.
Return ONLY a valid JSON array.
Do NOT provide explanations.
Do NOT use markdown.
If no entities are found, return:
[]

Output format:

[{"label": "PRODUCT_NAME|PRODUCT_COLOR|COMPLEXITY|TYPE|SHIP_TIME|SHIP_DATE|QUANTITY|SCALE|AUTHENTICITY","text": ""}]

--- Examples ---

Input:
"Mình muốn tìm lego hoa hồng màu đỏ"

Output:[{"label": "PRODUCT_NAME","text": "lego hoa hồng"},{"label": "TYPE","text": "hoa"},{"label": "PRODUCT_COLOR","text": "đỏ"}]

Input:
"Có bộ xe ferrari khoảng 1000 mảnh cho người mới chơi không?"

Output:[{"label": "PRODUCT_NAME","text": "bộ xe ferrari"},{"label": "TYPE","text": "xe"},{"label": "COMPLEXITY","text": "1000 mảnh"},{"label": "COMPLEXITY","text": "người mới chơi"}]

Input:
"Cho mình 2 bộ lego loopy màu xanh, giao thứ bảy tuần này lúc 8h tối"

Output:
[{"label": "QUANTITY","text": "2 bộ"},{"label": "PRODUCT_NAME","text": "lego loopy"},{"label": "PRODUCT_COLOR","text": "xanh"},{"label": "SHIP_DATE","text": "thứ bảy tuần này"},{"label": "SHIP_TIME","text": "8h tối"}]

Input:"tầm sáng mai tớ lấy"
Output: "[{"lable":"SHIP_TIME","text": "sáng"},{"lable":"SHIP_DATE","text":"mai"}]
--- End examples ---

Input text:
"""

# ── Regex to strip markdown code fences from model output ─────────────────────
_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


# ── Core helpers ───────────────────────────────────────────────────────────────

def load_input(path: Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_checkpoint(path: Path) -> dict[int, dict]:
    """Return {id: record} for already-processed records."""
    done = {}
    if not path.exists():
        return done
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rec = json.loads(line)
                done[rec["id"]] = rec
    return done


def parse_llm_response(raw: str) -> list[dict]:
    raw = raw.strip()
    m = _FENCE_RE.search(raw)
    if m:
        raw = m.group(1).strip()
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError(f"Expected list, got {type(data)}")
    return data


def find_spans(text: str, entities: list[dict]) -> list[dict]:
    """Map each entity dict {'label', 'text'} → {'start','end','label','text'}.

    For duplicate entity texts the search advances past the previous match so
    each occurrence maps to a different position.
    """
    next_search: dict[tuple, int] = {}  # (label, needle) → next search offset
    seen: set[tuple] = set()            # (start, label) — deduplication guard
    spans = []

    for ent in entities:
        label  = ent.get("label", "").upper()
        needle = ent.get("text", "")
        if label not in {"PRODUCT_NAME", "PRODUCT_COLOR","COMPLEXITY","TYPE","SHIP_TIME","SHIP_DATE","QUANTITY"} or not needle:
            continue

        key      = (label, needle)
        from_pos = next_search.get(key, 0)

        idx = text.find(needle, from_pos)

        # Fallback: try stripping whitespace (LLM sometimes trims)
        if idx == -1:
            stripped = needle.strip()
            if stripped and stripped != needle:
                idx = text.find(stripped, from_pos)
                if idx != -1:
                    needle = stripped

        if idx == -1:
            print(f"  [WARN] entity not found in text: {ent['text']!r}")
            continue

        dedup = (idx, label)
        if dedup not in seen:
            seen.add(dedup)
            spans.append({
                "start": idx,
                "end"  : idx + len(needle),
                "label": label,
                "text" : needle,
            })

        next_search[key] = idx + 1  # advance so next duplicate finds its own position

    return sorted(spans, key=lambda s: s["start"])


def call_gemini(model, text: str, dry_run: bool = False) -> list[dict]:
    prompt = PROMPT_TEMPLATE + text
    if dry_run:
        print("  [DRY-RUN] prompt ready, skipping API call")
        return []

    for attempt in range(RETRY_LIMIT):
        try:
            resp = model.generate_content(prompt)
            return parse_llm_response(resp.text)
        except json.JSONDecodeError as e:
            raw_preview = getattr(resp, "text", "")[:120]
            print(f"  [WARN] JSON parse error (attempt {attempt+1}): {e} | raw: {raw_preview!r}")
        except Exception as e:
            print(f"  [WARN] API error (attempt {attempt+1}): {e}")

        if attempt < RETRY_LIMIT - 1:
            time.sleep(2 ** attempt)

    print("  [ERROR] all retries exhausted, storing empty entities for this record")
    return []


def flush_checkpoint(path: Path, records: list[dict]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Annotate NER entities with Gemini")
    parser.add_argument("--resume",  action="store_true", help="Skip already-processed records")
    parser.add_argument("--dry-run", action="store_true", help="Print prompts without calling API")
    args = parser.parse_args()

    # Load .env
    load_dotenv(BASE / ".env")
    api_key = os.getenv("GOOGLE_API_KEY", "")
    if not api_key and not args.dry_run:
        sys.exit("[ERROR] GOOGLE_API_KEY not found in environment / .env file")

    if not args.dry_run:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(MODEL_NAME)
    else:
        model = None

    # Load data
    samples = load_input(INPUT_FILE)
    print(f"Loaded {len(samples)} records from {INPUT_FILE}")

    # Resume support
    done_map = {}
    if args.resume and CHECKPOINT.exists():
        done_map = load_checkpoint(CHECKPOINT)
        print(f"Resuming: {len(done_map)} records already done")

    pending = [(i, s) for i, s in enumerate(samples) if i not in done_map]
    print(f"Records to process: {len(pending)}")

    buffer: list[dict] = []

    for count, (idx, sample) in enumerate(pending, 1):
        text = sample["text"]
        short = text.replace("\n", " ")[:70]
        print(f"[{count}/{len(pending)}] #{idx} — {short!r}")

        raw_entities = call_gemini(model, text, dry_run=args.dry_run)
        spans        = find_spans(text, raw_entities)

        if spans:
            for s in spans:
                print(f"  {s['label']:<8} [{s['start']}:{s['end']}] {s['text']!r}")
        else:
            print("  (no entities found)")

        record = {
            "id"      : idx,
            "text"    : text,
            "cats"    : sample.get("cats", []),
            "entities": spans,
        }
        done_map[idx] = record
        buffer.append(record)

        # Periodic checkpoint flush
        if len(buffer) >= SAVE_EVERY:
            flush_checkpoint(CHECKPOINT, buffer)
            buffer.clear()
            print(f"  [checkpoint saved]")

        if not args.dry_run:
            time.sleep(REQUEST_DELAY)

    # Final flush
    if buffer:
        flush_checkpoint(CHECKPOINT, buffer)

    # Merge checkpoint → final output (sorted by original order)
    all_records = [done_map[i] for i in range(len(samples)) if i in done_map]
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)

    print(f"\nDone. {len(all_records)} records saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()