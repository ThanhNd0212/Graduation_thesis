# Intent Classification Approaches for Vietnamese Customer Chat

## Project Goal

Compare multiple NLP approaches for **multi-label intent classification** on informal Vietnamese social media customer chat data (e-commerce domain, LEGO/toy shop).

This classification work is **Phase 1** of a larger three-phase system. After classification is complete, Phase 2 adds Named Entity Recognition (product names, phone numbers, addresses) and Phase 3 applies a rule-based chatbot that uses intent + entities to respond automatically — minimizing LLM API costs for the shop. See [system_design.md](system_design.md) for the full pipeline design.

---

## Why This Problem Is Challenging

Before choosing models, it is important to understand the specific challenges of this dataset:

| Challenge | Description | Example |
|---|---|---|
| **Informal Vietnamese** | Teen code, abbreviations, creative spelling | `"k"` = `"không"`, `"đc"` = `"được"`, `"sốp"` = `"shop"` |
| **Code-switching** | English words embedded in Vietnamese sentences | `"box này còn k ạ"`, `"ship về hà nội"`, `"non-lego z ạ"` |
| **Brand/product names** | English proper nouns that must not be altered | `lego`, `ferrari`, `mario`, `ford gt`, `porches 911` |
| **Very short text** | Most messages are 1–15 words | `"shop ơi"`, `"đc ạ"`, `"vâng"` |
| **Multi-label** | Some messages express more than one intent | `["ask_order_status", "ask_order_wait_time"]` |
| **Class imbalance** | 32 classes ranging from 10.55% to 0.94% of data | `give_product`: 528 samples vs `ask_find_product`: 47 samples |

---

## Shared Setup (All Approaches)

### Multi-label Formulation
- Output layer: **32-dimensional sigmoid** (one neuron per intent class)
- Loss function: `BCEWithLogitsLoss` with `pos_weight` per class to handle imbalance
  - `pos_weight_i = (N - count_i) / count_i`
- Prediction threshold: 0.5 default, then tuned per class on validation set

### Data Split
- Suffle the data
- Use **iterative stratification** (`scikit-multilearn` `IterativeStratification`) because standard `train_test_split` does not handle multi-label distributions correctly
- Split ratio: **80% train / 10% validation / 10% test**
- Same fixed split used for all approaches (ensures fair comparison)

### Evaluation Metrics
All approaches are evaluated on the same held-out test set using:

| Metric | Why |
|---|---|
| **Macro-F1** | Primary metric — treats all 32 classes equally, important for rare intents |
| **Micro-F1** | Label-level performance weighted by frequency |
| **Hamming Loss** | Fraction of individual label predictions that are wrong |
| **Subset Accuracy** | Fraction of samples where the full label set is exactly correct |

---

## Approach 1: SVM + TF-IDF (Non-neural Baseline)

### Why This Approach
Every comparative NLP study needs a non-neural lower bound. SVM with TF-IDF features:
- Establishes how much complexity is needed — if this performs near BERT, the data is simpler than expected
- Handles code-switching gracefully: treats `lego`, `không`, `ship` as equal features regardless of language
- Runs in seconds, requires no GPU, and results are easy to interpret
- Reviewers and thesis committees almost always expect a classical ML baseline

### Preprocessing
```
Raw text -> lowercase -> remove extra whitespace -> TF-IDF vectorization
```
- No word segmentation needed (TF-IDF works on tokens as-is)
- Character n-grams (2–4) can be added to capture teen code patterns (e.g., `"kh"`, `"ko"`)

### Model Architecture
```
TF-IDF features (word unigrams + bigrams, optionally char n-grams)
    -> Multi-label SVM using One-vs-Rest strategy (OneVsRestClassifier)
    -> 32 binary SVM classifiers (one per intent class)
```
- Alternatively: Logistic Regression with `multi_label=True` (often competitive with SVM)

### Training Configuration
| Parameter | Value |
|---|---|
| TF-IDF max features | 10,000–50,000 |
| N-gram range | (1, 2) for words, optionally (2, 4) for characters |
| SVM kernel | Linear (best for text) |
| SVM C | Tuned via cross-validation: {0.1, 1, 10} |
| Class weight | `balanced` |

### Limitations
- No understanding of word meaning — `"k"` and `"không"` are different features even though they mean the same thing
- Cannot capture context or word order beyond bigrams
- Will struggle with out-of-vocabulary teen code

---

## Approach 2: ViSoBERT Fine-tuned (Vietnamese Social Media BERT)

### Why This Approach
ViSoBERT (`uitnlp/visobert`) is a BERT model pretrained specifically on Vietnamese social media text. Unlike PhoBERT which was trained on formal Vietnamese (news, Wikipedia), ViSoBERT's pretraining corpus reflects the same informal language register as the customer chat data — teen code, abbreviations, emoji, and code-switched English words are all part of its pretraining distribution.

**The core argument:** This data is social media text. A model pretrained on social media text should understand it more naturally than one pretrained on formal text — without requiring any preprocessing or normalization.

**What ViSoBERT handles natively that PhoBERT cannot:**
- Teen code and abbreviations (`k`, `đc`, `sốp`) are likely in-vocabulary or seen in context during pretraining
- Code-switched English product terms (`lego`, `ship`, `box`) appear frequently in Vietnamese social media
- Informal sentence structures, emoji, and particles (`ạ`, `nha`, `ơiii`) are part of the pretraining distribution

### Preprocessing
```
Raw text -> lowercase -> strip extra whitespace -> ViSoBERT tokenizer
```
- **No word segmentation, no normalization** — the model is designed to handle informal text as-is
- ViSoBERT uses its own BPE tokenizer trained on social media vocabulary; do not substitute the PhoBERT tokenizer
- Max sequence length: 128 tokens

### Model Architecture
```
Input text
    -> ViSoBERT (uitnlp/visobert) [12 layers, 768-dim hidden]
    -> [CLS] token representation (768-dim)
    -> Dropout(p=0.3)
    -> Linear(768 -> 32)
    -> Sigmoid (inference) / BCEWithLogitsLoss (training)
```

### Training Configuration
| Parameter | Value |
|---|---|
| Optimizer | AdamW |
| Learning rate | 2e-5 |
| LR scheduler | Linear warmup (10% of steps) then linear decay |
| Batch size | 32 |
| Max epochs | 20 |
| Early stopping | Patience = 5 epochs on val Macro-F1 |
| Gradient clipping | 1.0 |
| Dropout | 0.3 on CLS output |

### Limitations
- Vietnamese social media pretraining may include noisy or contradictory patterns from non-e-commerce domains (social commentary, news reactions) that do not match this shop conversation style
- Less studied and less benchmarked than PhoBERT — fewer reference results to compare against in the literature
- English brand name representations may still be weaker than XLM-RoBERTa (Approach 4) despite social media exposure

---

## Approach 3: Rule-based Normalization -> PhoBERT Fine-tuned

### Why This Approach
Approach 2 exposes PhoBERT to raw informal text it was not pretrained on. A natural hypothesis is: **if we first convert informal Vietnamese to standard Vietnamese, PhoBERT's pretrained knowledge becomes more useful**.

This approach tests that hypothesis using a transparent, controllable rule-based normalization step. It answers a key research question: *Does text normalization compensate for PhoBERT's formal-text pretraining bias?*

Rule-based normalization is chosen over neural normalization (seq2seq) because:
- No parallel corpus (informal -> formal) is available for this domain
- Rules are interpretable and errors are easy to diagnose
- Avoids introducing a second model whose errors compound into the classifier
- Fast and lightweight — no additional GPU memory or training time

### Why Neural Normalization (Seq2Seq / BARTpho) Was Not Chosen
Seq2Seq normalization requires a parallel corpus of (informal text, standard text) pairs. This data does not currently exist for this specific e-commerce domain. Additionally, seq2seq models risk incorrectly normalizing English brand names (`lego` -> some Vietnamese equivalent) unless a brand protection step is added, significantly increasing pipeline complexity.

### Preprocessing Pipeline
```
Raw text
    -> Step 1: Lowercase
    -> Step 2: Protect English tokens (brand names, product codes)
    -> Step 3: Expand teen code abbreviations (normalization dictionary)
    -> Step 4: Collapse repeated characters
    -> Step 5: Strip extra whitespace
    -> PhoBERT tokenizer
```

**Step 2 — English token protection:**
Before normalization, extract tokens matching `/^[a-zA-Z0-9\-]+$/` that are NOT in the teen code dictionary (these are brand names / borrowed words) and skip them during normalization.
- Protected: `lego`, `ferrari`, `ford`, `mario`, `f1`, `porches`, `box`, `ship`, `ok`
- Normalized: `k` -> `không`, `đc` -> `được`

**Step 3 — Normalization dictionary (domain-specific, build from data):**

| Teen code | Standard form | Notes |
|---|---|---|
| `k`, `ko`, `kh`, `khum`, `hum`, `kum` | `không` | Most common abbreviation |
| `đc`, `dc` | `được` | |
| `vs` | `với` | |
| `v` (sentence-final) | `vậy` | Context-dependent |
| `b` | `bạn` | |
| `t`, `tớ` | keep | Informal but standard enough |
| `sốp`, `sốc` | `shop` | Domain-specific misspelling |
| `ck` | `chuyển khoản` | Payment term |
| `rep` | `trả lời` | From English "reply" |
| `r`, `rui` | `rồi` | |
| `nha`, `nhen` | `nhé` | |
| `sp` | `sản phẩm` | |
| `mn` | `mọi người` | |
| `m` | `mình` | |

**Step 4 — Repeated character collapsing:**
Regex: replace `(.)\1{2,}` with `\1\1` (keep at most 2 repetitions)
- `"ơiiii"` -> `"ơii"`, `"shoppp"` -> `"shopp"`

**Step 5:** PhoBERT tokenizer (`vinai/phobert-base`)

### Model Architecture
```
Normalized text
    -> PhoBERT-base (vinai/phobert-base) [12 layers, 768-dim hidden, 135M params]
    -> [CLS] token representation (768-dim)
    -> Dropout(p=0.3)
    -> Linear(768 -> 32)
    -> Sigmoid (inference) / BCEWithLogitsLoss (training)
```

### Training Configuration
| Parameter | Value |
|---|---|
| Optimizer | AdamW |
| Learning rate | 2e-5 |
| LR scheduler | Linear warmup (10% of steps) then linear decay |
| Batch size | 32 |
| Max epochs | 20 |
| Early stopping | Patience = 5 epochs on val Macro-F1 |
| Gradient clipping | 1.0 |
| Dropout | 0.3 on CLS output |

### Key Comparison This Enables
- **Approach 3 vs Approach 2 (ViSoBERT):** Two different strategies for the same problem — informal Vietnamese. ViSoBERT handles it via domain-specific pretraining; this approach handles it via explicit preprocessing. Which strategy is more effective?
- **Approach 3 internal:** If normalization + PhoBERT matches or beats raw PhoBERT (without normalization), it validates that preprocessing can substitute for domain-adapted pretraining — an important practical finding for resource-constrained settings where ViSoBERT may not be available

---

## Approach 4: XLM-RoBERTa Fine-tuned (Multilingual BERT)

### Why This Approach
XLM-RoBERTa (`facebook/xlm-roberta-base`) was pretrained on text from 100 languages simultaneously, including both Vietnamese and English. This makes it the only model in this comparison that was explicitly trained on **both languages present in the data**.

**The core argument for this approach:** Your customer chat data is not purely Vietnamese — it is code-switched Vietnamese-English. Models pretrained on a single language (PhoBERT for Vietnamese, or any English-only model) have vocabulary gaps for the other language. XLM-RoBERTa has full representations for both `lego`, `ferrari`, `ship` (English) and `không`, `được`, `ạ` (Vietnamese) in the same embedding space.

**This directly addresses the code-switching problem** that is a structural feature of your data, without requiring any preprocessing.

### Preprocessing
```
Raw text -> lowercase -> strip extra whitespace -> XLM-RoBERTa tokenizer
```
- No word segmentation, no normalization
- The XLM-RoBERTa tokenizer (SentencePiece) handles mixed-language text natively
- English tokens like `ferrari`, `lego`, `ship` have meaningful pretrained representations — unlike PhoBERT where they become fragmented subwords with weak semantics

### Model Architecture
```
Input text
    -> XLM-RoBERTa-base (facebook/xlm-roberta-base) [12 layers, 768-dim hidden, 278M params]
    -> [CLS] token representation (768-dim)
    -> Dropout(p=0.3)
    -> Linear(768 -> 32)
    -> Sigmoid (inference) / BCEWithLogitsLoss (training)
```

### Training Configuration
| Parameter | Value |
|---|---|
| Optimizer | AdamW |
| Learning rate | 2e-5 |
| LR scheduler | Linear warmup (10% of steps) then linear decay |
| Batch size | 32 |
| Max epochs | 20 |
| Early stopping | Patience = 5 epochs on val Macro-F1 |
| Gradient clipping | 1.0 |
| Dropout | 0.3 on CLS output |

### What Makes This Approach Different
| | ViSoBERT (Approach 2) | PhoBERT + Norm (Approach 3) | XLM-RoBERTa (Approach 4) |
|---|---|---|---|
| Pretraining language | Vietnamese social media | Formal Vietnamese | 100 languages incl. VN + EN |
| Code-switching handling | Good (social media exposure) | Moderate (normalization helps) | Best (explicit bilingual vocab) |
| Formal Vietnamese knowledge | Moderate | Strong (after normalization) | Moderate |
| English brand names | Moderate | Protected by whitelist | Strong (full representations) |

### Limitations
- Larger model (278M vs PhoBERT's 135M parameters) — slightly slower to train and infer
- Vietnamese-specific knowledge may be weaker than PhoBERT or ViSoBERT since it shares capacity across 100 languages
- May underperform on purely Vietnamese linguistic patterns (dialect, tones) compared to language-specific models

---

## Summary: Why Four Approaches

| Approach | Model | Normalization | Primary Research Question | Test Macro-F1 |
|---|---|---|---|---|
| 1 | SVM + TF-IDF | None | Non-neural lower bound — is deep learning even necessary? | — |
| 2 | ViSoBERT | None | Does social media pretraining give a native advantage on informal chat? | — |
| **3** | **PhoBERT + Norm + Segment** | **Rule-based + underthesea** | Can explicit normalization + segmentation make a formal-text BERT competitive with a social-media BERT? | **0.9022 ✅** |
| 4 | XLM-RoBERTa | None | Does cross-lingual pretraining handle code-switching better than Vietnamese-specific models? | — |

Together, these four approaches form a structured comparison across three axes:
- **Informal text strategy** — social media pretraining (Approach 2) vs explicit normalization (Approach 3)
- **Cross-lingual vs monolingual** — XLM-RoBERTa (Approach 4) vs ViSoBERT (Approach 2)
- **Neural vs non-neural** — Approach 1 vs Approaches 2, 3, 4

---

## Approach 3: Final Training Details (deployed model)

**Notebook:** `approach3_results/approach3_phobert_normalized_after_trained.ipynb`
**Checkpoint:** `approach3_results/results/intent_model/`

Beyond the base architecture described above, the final deployed version adds four techniques:

### (A) Word segmentation with underthesea

```python
from underthesea import word_tokenize
normalized_text = word_tokenize(after_teen_code_normalize, format='text')
# "hà nội" -> "hà_nội"  |  "chuyển khoản" -> "chuyển_khoản"
```

PhoBERT was pretrained on word-segmented Vietnamese text, so feeding segmented input matches its training distribution and improves F1. `underthesea` is used (pure Python, no Java dependency) instead of VnCoreNLP.

### (B) Layer freezing + stronger weight decay

```
Freeze: embedding layer + first 4 of 12 encoder layers
Weight decay: 0.05 (instead of 0.01)
Epochs: 30 (instead of 100, so the LR schedule decays correctly)
```

Training loss previously collapsed to ~0.003 while validation F1 plateaued — a sign of overfitting. Freezing the lower layers (which already encode Vietnamese syntax from pretraining) reduces trainable parameters and stabilizes training.

### (C) Compositional augmentation

Synthesize additional multi-label samples by concatenating two single-label rare-class sentences:

```python
# Example: ask_shipping_fee + ask_order_wait_time had only 4 training samples
"ship bao nhiêu tiền vậy" + " với " + "bao lâu thì nhận được ạ"
-> label: ["ask_shipping_fee", "ask_order_wait_time"]
```

4 target intent pairs x 40 samples each = 160 synthetic samples added.

### (D) Random token masking

A masked copy (15% of tokens dropped at random) of the full augmented set doubles training data:

```
Augmented set: 4,379 samples -> after masking: 8,758 samples
```

### Final test results

| Metric | Value |
|---|---|
| **Macro-F1** | **0.9022** |
| **Micro-F1** | **0.9073** |
| Hamming Loss | 0.0065 |
| Subset Accuracy | 0.8533 |

Lowest per-class F1: `ask_find_product` (0.40) — very few training samples (4 test samples).
Highest per-class F1: `Goodbye`, `add_product`, `ask_payment_method`, `ask_product_info`, `ask_shop_info` (1.00).
