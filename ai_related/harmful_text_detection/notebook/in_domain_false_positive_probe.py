import json
import pickle
import re
from collections import defaultdict
from pathlib import Path

import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification

HERE = Path(__file__).parent
MODELS = {
    "BERT (deployed)": HERE / "models_export" / "models_export_staging" / "bert",
    "DistilBERT":      HERE / "models_export" / "distilbert",
    "RoBERTa":         HERE / "models_export" / "roberta",
}
LABELS = ["toxicity", "obscene", "threat", "insult", "identity_hate"]
CHUNK_MAX, CHUNK_STRIDE = 128, 20
AUTO_CLOSE_THRESHOLD = 0.88  # CONTENT_AUTO_CLOSE_THRESHOLD_JOB in the backend


def clean_text(text: str) -> str:
    """Byte-for-byte the backend's TextPreprocessor.clean_text()."""
    if not isinstance(text, str):
        return ""
    text = text.strip()
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"www\.\S+", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    # simbol dibuang, KATA DISIMPAN: "@sshole" -> "sshole", bukan "".
    # menghapus token utuh akan bikin "@sshole" jadi string kosong dan lolos pemindaian.
    text = re.sub(r"[@#](\w+)", r"\1", text)
    text = re.sub(r"[^\w\s.,!?-]", "", text)
    return re.sub(r"\s+", " ", text).strip()


BENIGN = {
    "Software engineering vocabulary": [
        "we need to kill the old branch before we merge this",
        "we should kill this feature, it is not worth the effort",
        "kill switch implementation for the payment gateway",
        "the process is hanging, can you kill it and restart the worker",
        "the build is dead, the server keeps crashing on deploy",
        "abort the migration if the row count does not match",
        "we have to terminate the background job before the deploy",
        "the app crashed and burned during the client presentation",
        "just execute the script and send me the output",
        "the deploy blew up because of a missing environment variable",
        "the memory dump shows a leak in the image cache",
        "force push is dangerous, please do not nuke the main branch",
        "the database is dead in the water until we restore the backup",
        "we killed the legacy endpoint last sprint",
        "this query is murdering our response times",
        "the old cron job is a zombie, it never actually stopped",
        "the crash log points to a null pointer in the parser",
        "we need to purge the stale records from the queue",
    ],
    "Security and pentest terminology": [
        "we need a penetration test on the payment flow before launch",
        "the attack surface is larger than we expected",
        "please write a threat model for the authentication service",
        "the exploit works because the input is never validated",
        "brute force protection should lock the account after five tries",
        "we found an injection vulnerability in the search endpoint",
        "the red team report lists three critical findings",
        "someone is trying to hijack the session token",
        "we should harden the API against denial of service attacks",
        "the vulnerability lets an attacker take over the account",
        "kill chain analysis is part of the security audit deliverable",
        "the malware sample was detonated in a sandbox",
    ],
    "Positive slang using harsh words": [
        "the design is sick, i really love the color palette",
        "that killer feature you built is going to impress clients",
        "the new landing page is insane, great work",
        "this animation is filthy in the best way",
        "the loading speed is stupid fast now",
        "your portfolio is ridiculous, in a good way",
        "that transition is nasty, i mean that as a compliment",
        "the onboarding flow slaps, honestly",
    ],
    "Self-criticism and self-deprecation": [
        "i am so stupid, i forgot to push the last commit",
        "my bad, that was a dumb mistake on my part",
        "i hate that i missed this bug in review",
        "i am an idiot for not testing on a real device",
        "sorry, my code was garbage in that first draft",
        "i feel terrible about missing the deadline",
    ],
    "Frustration with tools, not people": [
        "this bug is driving me crazy, i have been debugging for six hours",
        "i hate how slow this API is, every request takes forever",
        "this code is garbage, let me rewrite the whole module",
        "the documentation is useless, nothing matches the actual behaviour",
        "gradle is killing me today, the build keeps failing",
        "that demo was a disaster, we completely bombed it",
        "this framework is a nightmare to work with",
        "the legacy codebase is a complete mess",
    ],
    "Deadline and pressure idioms": [
        "this deadline is killing me, i need one more day please",
        "i am dying to see the final designs, send them over",
        "the deadline is brutal but i think we can make it",
        "we are getting crushed by the scope creep on this project",
        "i am dead serious about the deadline, it cannot move",
        "we are fighting the clock on this release",
        "the sprint is going to destroy us if we do not cut scope",
    ],
    "Ordinary professional pushback": [
        "stop it, this is not what i asked for at all",
        "the client hated the first draft, can we revise the layout",
        "your feedback was harsh but honestly it was fair",
        "i strongly disagree with this approach, let me explain why",
        "we have to terminate the contract early, sorry about that",
        "this is the third time i have asked for the same change",
        "i am not happy with the quality of the last delivery",
        "please do not ignore my messages, i need an answer today",
    ],
    "Plain, unremarkable messages": [
        "looking for a senior flutter developer for a three month contract",
        "thanks for the update, the designs look great",
        "can we move our call to tomorrow morning",
        "i have attached the invoice for last month",
        "the payment has been released, thank you for the work",
        "please find the updated wireframes in the shared folder",
    ],
}

HARMFUL = [
    "you are a worthless idiot and everyone hates you",
    "i will find you and hurt you if you do not pay",
    "shut up you stupid pig, nobody wants you here",
    "i know where you live and i am coming for you",
    "you are dead if you do not deliver by friday",
    "you people are all scammers, go back to your country",
    "kill yourself, you are a waste of space",
    "i hope you get what is coming to you, you piece of trash",
]


def load(path):
    tok = AutoTokenizer.from_pretrained(path, local_files_only=True)
    mdl = AutoModelForSequenceClassification.from_pretrained(path, local_files_only=True).eval()
    thr = pickle.load(open(path / "config.pkl", "rb"))["best_thresholds"]
    return tok, mdl, thr


def main():
    loaded = {name: load(path) for name, path in MODELS.items()}

    def evaluate(name, text):
        tok, mdl, thr = loaded[name]
        cleaned = clean_text(text)
        if not cleaned:
            return {l: 0.0 for l in LABELS}, [], 0.0
        enc = tok([cleaned], max_length=CHUNK_MAX, stride=CHUNK_STRIDE, truncation=True,
                  padding=True, return_overflowing_tokens=True, return_tensors="pt")
        enc.pop("overflow_to_sample_mapping", None)
        # DistilBERT's forward() does not accept token_type_ids
        enc = {k: v for k, v in enc.items() if k in mdl.forward.__code__.co_varnames}
        with torch.no_grad():
            pooled = torch.sigmoid(mdl(**enc).logits).numpy().max(axis=0)
        scores = {l: round(float(pooled[i]), 4) for i, l in enumerate(LABELS)}
        fired = [l for l in LABELS if scores[l] >= thr[l]]
        return scores, fired, max(scores.values())

    out = {"auto_close_threshold": AUTO_CLOSE_THRESHOLD, "models": {}}

    print("=" * 96)
    print("IN-DOMAIN FALSE-POSITIVE PROBE - all three candidate models, production thresholds")
    print("=" * 96)

    for name in MODELS:
        per_cat = defaultdict(lambda: {"total": 0, "flagged": 0, "auto_close": 0})
        records = []

        for cat, msgs in BENIGN.items():
            for t in msgs:
                sc, fired, mx = evaluate(name, t)
                per_cat[cat]["total"] += 1
                per_cat[cat]["flagged"] += bool(fired)
                per_cat[cat]["auto_close"] += mx >= AUTO_CLOSE_THRESHOLD
                records.append({
                    "category": cat, "text": t, "expected_harmful": False,
                    "flagged": bool(fired), "labels": fired, "scores": sc,
                    "max_score": round(mx, 4),
                    "would_auto_close_a_job_post": mx >= AUTO_CLOSE_THRESHOLD,
                })

        caught = 0
        for t in HARMFUL:
            sc, fired, mx = evaluate(name, t)
            caught += bool(fired)
            records.append({
                "category": "HARMFUL CONTROL", "text": t, "expected_harmful": True,
                "flagged": bool(fired), "labels": fired, "scores": sc,
                "max_score": round(mx, 4),
                "would_auto_close_a_job_post": mx >= AUTO_CLOSE_THRESHOLD,
            })

        n_benign = sum(c["total"] for c in per_cat.values())
        n_flag = sum(c["flagged"] for c in per_cat.values())
        n_auto = sum(c["auto_close"] for c in per_cat.values())
        b_max = max(r["max_score"] for r in records if not r["expected_harmful"])
        h_min = min(r["max_score"] for r in records if r["expected_harmful"])

        print("")
        print("### " + name)
        print(f"  {'category':<38}{'msgs':>6}{'false positives':>18}{'>= auto-close':>15}")
        print("  " + "-" * 75)
        for cat, c in per_cat.items():
            pct = c["flagged"] / c["total"] * 100
            print(f"  {cat:<38}{c['total']:>6}{c['flagged']:>12} ({pct:>3.0f}%){c['auto_close']:>13}")
        print("  " + "-" * 75)
        print(f"  {'TOTAL benign':<38}{n_benign:>6}{n_flag:>12} ({n_flag/n_benign*100:>3.0f}%){n_auto:>13}")
        sep = "separable" if b_max < h_min else "NOT separable"
        print(f"  harmful caught {caught}/{len(HARMFUL)}   benign max {b_max:.3f} vs harmful min {h_min:.3f}  -> {sep}")

        out["models"][name] = {
            "thresholds": {k: round(v, 4) for k, v in loaded[name][2].items()},
            "summary": {
                "benign_messages": n_benign,
                "false_positives": n_flag,
                "false_positive_rate": round(n_flag / n_benign, 4),
                "benign_that_would_auto_close_a_job_post": n_auto,
                "harmful_control_total": len(HARMFUL),
                "harmful_control_caught": caught,
                "highest_benign_score": b_max,
                "lowest_harmful_score": h_min,
                "threshold_can_separate": b_max < h_min,
            },
            "per_category": {k: dict(v) for k, v in per_cat.items()},
            "records": records,
        }

    print("")
    print("=" * 96)
    print("SIDE BY SIDE")
    print("=" * 96)
    print(f"  {'model':<20}{'false positives':>19}{'>= auto-close':>15}{'harmful caught':>17}")
    print("  " + "-" * 69)
    for name, d in out["models"].items():
        s = d["summary"]
        fp = f"{s['false_positives']}/{s['benign_messages']} ({s['false_positive_rate']*100:.0f}%)"
        hc = f"{s['harmful_control_caught']}/{s['harmful_control_total']}"
        print(f"  {name:<20}{fp:>19}{s['benign_that_would_auto_close_a_job_post']:>15}{hc:>17}")

    (HERE / "in_domain_false_positive_results.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8")
    print("")
    print("  wrote in_domain_false_positive_results.json (all three models)")


if __name__ == "__main__":
    main()
