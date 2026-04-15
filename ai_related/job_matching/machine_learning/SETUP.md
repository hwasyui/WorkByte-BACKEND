# ML Job Ranker — Setup & Execution Guide

Run everything **inside** the `capstone-backend` container.

---

## Step 1 — Enter the container

```bash
docker exec -it capstone-backend bash
```

---

## Step 2 — Install dependencies

```bash
pip install lightgbm scikit-learn joblib jupyter nbconvert matplotlib seaborn
```

> These are also added to `requirements.txt` for future builds.

---

## Step 3 — Navigate to the ML folder

```bash
cd /app/ai_related/job_matching/machine_learning
```

---

## Step 4 — Generate synthetic training data

Generates ~700 freelancers, ~600 jobs, ~3 000 proposals, contracts, ratings,
and a `ml_training_pairs.csv` with pre-computed features + outcome labels.
All CSVs land in `data/`.

```bash
jupyter nbconvert --to notebook --execute \
  --ExecutePreprocessor.timeout=300 \
  01_generate_data.ipynb \
  --output 01_generate_data.ipynb
```

Expected output (last few lines):
```
  ml_training_pairs             7 000+ rows  →  data/ml_training_pairs.csv
All data saved!
```

---

## Step 5 — Train the LightGBM ranker

Loads the CSVs, trains LightGBM with early stopping, evaluates AUC vs cosine
similarity baseline, benchmarks inference speed, and saves the model.

```bash
jupyter nbconvert --to notebook --execute \
  --ExecutePreprocessor.timeout=300 \
  02_train_model.ipynb \
  --output 02_train_model.ipynb
```

Expected output (last section):
```
=== FINAL SUMMARY ===
  auc_roc                       : 0.85+
  baseline_cosine_auc           : 0.70–0.75
  auc_improvement               : 0.10+
  inference_100_jobs_ms         : < 5ms
```

Saved artefacts:
```
models/lgbm_job_matcher.pkl      ← model (load with joblib)
models/feature_cols.json         ← ordered feature list
models/model_summary.json        ← metrics snapshot
models/feature_importance.png
models/feature_distributions.png
models/confusion_matrix.png
```

---

## Step 6 — Verify

```bash
python3 - <<'EOF'
import joblib, json, pandas as pd, numpy as np

model     = joblib.load('models/lgbm_job_matcher.pkl')
feat_cols = json.load(open('models/feature_cols.json'))
summary   = json.load(open('models/model_summary.json'))

print('Model loaded OK')
print(f'AUC-ROC:    {summary[\"auc_roc\"]}')
print(f'Baseline:   {summary[\"baseline_cosine_auc\"]}')
print(f'Gain:      +{summary[\"auc_improvement\"]}')
print(f'Speed:      {summary[\"inference_100_jobs_ms\"]}ms / 100 jobs')

# Quick smoke test — one cold-start freelancer vs one job
sample = pd.DataFrame([{c: np.nan for c in feat_cols}])
sample['cosine_sim']             = 0.72
sample['skill_overlap_pct']      = 0.60
sample['skill_required_matched'] = 3
sample['skill_required_total']   = 5
sample['experience_level_match'] = 1
sample['rate_in_budget']         = 1
sample['domain_match']           = 1
sample['is_cold_start']          = 1
score = model.predict_proba(sample)[0, 1]
print(f'Cold-start smoke test score: {score:.4f}  (expect 0.3–0.7 range)')
EOF
```

---

## Retraining

Re-run **Step 4 + 5** whenever you have new proposal/contract outcome data.
The model improves as real `accepted → completed → rated` signals accumulate.

---

## Auth notes (routes)

| Endpoint | Auth |
|---|---|
| `GET /ai/job_matching/match/freelancer-to-jobs` | Freelancer Bearer token — uses **your** profile |
| `GET /ai/job_matching/match/job-to-freelancers/{id}` | Client Bearer token — **must own** the job post |
| `POST /ai/job_matching/embed/freelancer/{id}` | Any authenticated user |
| `POST /ai/job_matching/embed/job/{id}` | Any authenticated user |
| `POST /ai/job_matching/sweep` | Any authenticated user |
