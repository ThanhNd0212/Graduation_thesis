# Approach 1 — Improvements Notes

Documents every change made in `approach1_svm_improved.ipynb` compared to the baseline `approach1_svm_tfidf.ipynb`, and the reasoning behind each decision.

---

## 1. Teen Code Normalization (Preprocessing)

**What changed:**  
Added a normalization step before TF-IDF vectorization. A dictionary maps common informal abbreviations to their standard Vietnamese equivalents (`k` → `không`, `đc` → `được`, `sốp` → `shop`, etc.). Repeated characters are also collapsed (`ơiiii` → `ơii`). English tokens (brand names, borrowed words like `lego`, `ferrari`, `ship`) are detected by ASCII pattern and skipped — they are never normalized.

**Why:**  
PhoBERT treats `k` and `không` as completely different tokens even though they mean the same thing. Without normalization, TF-IDF learns two separate features for identical meaning, diluting the signal. Normalizing collapses them into one, making the vocabulary more compact and each feature more informative.

**Trade-off:**  
Context-dependent abbreviations like `v` (can mean `vậy` or be part of another word) were intentionally left out to avoid wrong substitutions. Rule-based normalization is also limited to known patterns — unseen teen code still passes through unchanged.

---

## 2. Multi-label Sample Augmentation (Data)

**What changed:**  
Training samples that carry more than one intent label are duplicated twice before training. Augmentation is applied to the training set only — validation and test sets are untouched.

**Why:**  
Multi-label samples make up a small minority of the dataset. The `OneVsRestClassifier` trains each label's classifier on the full training set, but co-occurring label pairs (e.g. `ask_order_status` + `ask_order_wait_time`) appear so rarely that the classifiers never learn their co-occurrence pattern. Duplicating these samples increases their weight in training without introducing new data or distorting the overall label distribution significantly.

**Trade-off:**  
Simple duplication does not add new linguistic variety — it just increases frequency. A more principled approach would be back-translation or paraphrase generation, but those require additional infrastructure and are not guaranteed to improve results on a small dataset.

---

## 3. Handcrafted Features (Feature Engineering)

**What changed:**  
Six message-level features are extracted and appended to the TF-IDF matrix via `scipy.sparse.hstack`:

| Feature | Description |
|---|---|
| Word count | Number of tokens in the message |
| Char count | Total character length |
| Question mark count | Number of `?` characters |
| Phone number present | 1 if a Vietnamese mobile number pattern is detected |
| Any digit present | 1 if the message contains any digit |
| Politeness marker | 1 if `ạ`, `nhé`, or `nha` appears |

**Why:**  
TF-IDF captures what words are present but ignores message-level structure. Very short messages (1–2 words) like `"shop ơi"` or `"vâng"` are hard to classify from vocabulary alone but are almost always `greeting` or `agree`. A phone number strongly signals `provide_cus_inf`. A question mark correlates with `ask_*` intents. These signals are invisible to TF-IDF but trivial to compute.

**Trade-off:**  
The feature set is small and domain-specific. Adding too many handcrafted features risks overfitting on the training set patterns. The six chosen features were selected for being directly interpretable and clearly correlated with specific intents.

---

## 4. Logistic Regression Instead of LinearSVC (Model)

**What changed:**  
Replaced `LinearSVC` with `LogisticRegression` (`solver='lbfgs'`, `class_weight='balanced'`) as the base estimator inside `OneVsRestClassifier`.

**Why:**  
`LinearSVC` produces raw decision function scores (unbounded, uncalibrated). These can be used for ranking but are poorly suited for threshold tuning because the scale varies across classes and runs. `LogisticRegression` produces proper probabilities in `[0, 1]` via `predict_proba`, making per-class threshold search interpretable and consistent — a threshold of 0.3 means the same thing across all 32 classifiers.

**Trade-off:**  
Logistic Regression is slightly slower to train than LinearSVC on large sparse matrices. On this dataset size (~4000 samples, 50k features) the difference is negligible. LR is also marginally less accurate than a well-tuned SVM on some text tasks, but the calibration benefit for threshold tuning outweighs this.

---

## 5. Per-class Threshold Tuning (Model)

**What changed:**  
Instead of using a fixed threshold of 0.5 for all 32 classifiers, an optimal threshold is found per class by searching `t ∈ [0.10, 0.90]` in steps of 0.05 and choosing the value that maximises F1 on the validation set.

**Why:**  
This is the highest-impact fix for two specific weaknesses:

- **Multi-label under-prediction:** Co-occurring intents often score 0.3–0.4 probability (above random but below the 0.5 default). A per-class lower threshold allows them to be predicted without flooding every sample with false positives.
- **`other` class:** `other` is semantically incoherent — the model produces low, uncertain probabilities for it. A tuned threshold allows it to be predicted when appropriate without requiring a high-confidence score that the model can never produce for this class.

**Trade-off:**  
Thresholds are tuned on the validation set, so they may not generalise perfectly to the test set. With only ~500 validation samples and 32 classes, some class-level threshold estimates are noisy (especially for classes with fewer than 10 validation samples). This is a known limitation of threshold tuning on small datasets.

---

## Summary of Changes

| Change | File phase | Targets weakness |
|---|---|---|
| Teen code normalization | Preprocessing | Vocabulary fragmentation from informal text |
| Multi-label augmentation | Data | Under-prediction of co-occurring intents |
| Handcrafted features | Feature engineering | Short messages with weak TF-IDF signal |
| Logistic Regression | Model | Uncalibrated scores from LinearSVC |
| Per-class threshold tuning | Model | Multi-label under-prediction, `other` class |
