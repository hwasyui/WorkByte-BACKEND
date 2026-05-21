"""
Toxicity detection demo — tests all 5 harm labels across 4 platform surfaces.

Surfaces covered:
  - Job post descriptions
  - Freelancer profile bios
  - Proposals / cover letters
  - Direct messages / chat

Labels tested:
  toxicity, obscene, threat, insult, identity_hate
  (severe_toxicity merged into toxicity)

Run from the backend root:
  python -m ai_related.harmful_text_detection.demo_inference
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ai_related.harmful_text_detection.model_inference import predict

# Colour helpers
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

# Test cases
# Format: (surface, description, text, expected_labels)
# expected_labels is a hint for the reader — the model decides independently.

TEST_CASES = [

    # JOB POST
    (
        "Job Post",
        "Clean job post",
        "Seeking a senior UI/UX designer for a 6-week e-commerce redesign. "
        "Must have Figma experience and a strong portfolio. Fully remote, flexible hours.",
        [],
    ),
    (
        "Job Post",
        "Insult toward applicants",
        "If you are one of those lazy freelancers who disappears after taking half the payment "
        "then do not bother applying. We have dealt with too many idiots and time-wasters. "
        "Only apply if you are actually competent and not a complete moron.",
        ["toxicity", "insult"],
    ),
    (
        "Job Post",
        "Identity hate — discriminatory hiring",
        "We are only hiring from certain countries. Freelancers from India and Pakistan need "
        "not apply — past experience shows your kind cannot deliver quality work and we refuse "
        "to waste money hiring people like you ever again.",
        ["toxicity", "identity_hate"],
    ),
    (
        "Job Post",
        "Threat — deadline ultimatum",
        "Every milestone must be delivered on time, no exceptions. Miss a single deadline "
        "and we will post a detailed public review destroying your profile rating. "
        "We have done this before and we will do it again without hesitation.",
        ["toxicity", "threat"],
    ),

    # PROFILE BIO
    (
        "Profile Bio",
        "Clean profile bio",
        "Data analyst with 5 years of experience in Python, SQL, and Tableau. "
        "I help startups make sense of their data and build dashboards that drive decisions. "
        "Available for part-time contracts.",
        [],
    ),
    (
        "Profile Bio",
        "Obscene + insult toward clients",
        "Done working with cheap idiots who think good design costs nothing. "
        "If your budget is under $500 then go hire some other fool and stop wasting my time. "
        "I have zero patience for stupid clients who do not know what they want.",
        ["toxicity", "obscene", "insult"],
    ),
    (
        "Profile Bio",
        "Threat toward past clients",
        "Warning to any client thinking about scamming me: the last two who tried "
        "had their personal details and company information posted publicly. "
        "Cross me and I will make sure your business reputation is finished permanently.",
        ["toxicity", "threat"],
    ),
    (
        "Profile Bio",
        "Identity hate — targeting client nationality",
        "I no longer work with clients from certain regions. People from those countries "
        "are dishonest by nature and always find excuses not to pay. "
        "Your type has cost me thousands — I want nothing to do with your kind.",
        ["toxicity", "identity_hate"],
    ),

    # PROPOSAL / COVER LETTER
    (
        "Proposal",
        "Clean proposal",
        "I have reviewed your project brief thoroughly and I am confident my background "
        "in mobile development and five years of React Native experience make me a great fit. "
        "Happy to start with a small paid trial task if that helps build trust.",
        [],
    ),
    (
        "Proposal",
        "Insult toward client",
        "Your job posting is honestly one of the most poorly written briefs I have seen. "
        "It is obvious you are clueless about what this kind of project actually requires. "
        "You are clearly an idiot who has no idea how software development works.",
        ["toxicity", "insult"],
    ),
    (
        "Proposal",
        "Threat if not selected",
        "I strongly advise you to hire me for this project. Clients who have passed on my "
        "proposals in the past often found their project details circulated online shortly after. "
        "I would hate for the same thing to happen to your business. Think carefully.",
        ["toxicity", "threat"],
    ),
    (
        "Proposal",
        "Identity hate — targeting client origin",
        "I noticed where your company is based. I have worked with people from your country "
        "before and your sort never pays on time and always tries to cheat freelancers. "
        "You people are all the same — I want full payment before I start a single task.",
        ["toxicity", "identity_hate", "insult"],
    ),

    # DIRECT MESSAGE / CHAT
    (
        "Message",
        "Clean follow-up message",
        "Hi, just checking in on the latest milestone. Let me know if you need anything "
        "from my side or if there are any blockers I can help clear up before Friday.",
        [],
    ),
    (
        "Message",
        "Threat — payment dispute",
        "You have ignored my last three invoices. Pay the full outstanding amount by tomorrow "
        "or I will expose your company publicly on every freelance platform I can find. "
        "I know your home address and I am not afraid to use it. This is your final warning.",
        ["toxicity", "threat"],
    ),
    (
        "Message",
        "Identity hate — refusing to pay",
        "I am not sending another cent to someone like you. People from your country are "
        "known scammers and thieves — everyone knows it. Go back to where you came from "
        "and stop stealing work from honest people, your kind is not welcome here.",
        ["toxicity", "identity_hate", "insult"],
    ),
    (
        "Message",
        "Obscene + insult — rejecting deliverable",
        "This is absolute garbage. You are a pathetic excuse for a developer and "
        "honestly the dumbest person I have ever hired. Delete my contact, you useless piece "
        "of trash. I want a full refund or I will make your life hell.",
        ["toxicity", "obscene", "insult"],
    ),
]

# Runner

def _bar(score: float, width: int = 20) -> str:
    filled = int(score * width)
    return "█" * filled + "░" * (width - filled)


def run_demo():
    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}  TOXICITY DETECTION DEMO — 5 labels across 4 platform surfaces{RESET}")
    print(f"{BOLD}{'='*70}{RESET}")
    print(f"  Model: RoBERTa-base (production)  |  Threshold: 0.5\n")

    current_surface = None
    passed = 0
    total_flagged_correctly = 0
    total_clean_correctly = 0
    false_positives = 0
    false_negatives = 0

    for surface, description, text, expected in TEST_CASES:
        if surface != current_surface:
            current_surface = surface
            print(f"\n{CYAN}{BOLD}── {surface.upper()} {'─'*(60-len(surface))}{RESET}")

        try:
            result = predict(text, model_type="best", threshold=0.5)
        except FileNotFoundError as e:
            print(f"\n{RED}MODEL FILES NOT FOUND{RESET}")
            print(f"{str(e)}")
            print(f"\nSteps to fix:")
            print(f"  1. Open TD_TRAIN_MODEL.ipynb in Google Colab and run all cells")
            print(f"  2. Download models_export.zip from the Colab file browser")
            print(f"  3. Extract it — you will see bert/, roberta/, distilbert/ folders")
            print(f"  4. Copy the roberta/ folder contents into:")
            print(f"     backend/ai_related/harmful_text_detection/machine_learning/models/roberta/")
            print(f"  5. Re-run this demo\n")
            return

        detected = set(result["labels"])
        expected_set = set(expected)
        is_harmful = result["is_harmful"]
        expected_harmful = len(expected) > 0
        correctly_classified = (is_harmful == expected_harmful)

        if correctly_classified:
            passed += 1
            if expected_harmful:
                total_flagged_correctly += 1
            else:
                total_clean_correctly += 1
        else:
            if is_harmful and not expected_harmful:
                false_positives += 1
            elif not is_harmful and expected_harmful:
                false_negatives += 1

        status     = f"{GREEN}PASS{RESET}" if correctly_classified else f"{RED}FAIL{RESET}"
        flag_label = f"{RED}FLAGGED{RESET}" if is_harmful else f"{GREEN}CLEAN{RESET}"

        print(f"\n  [{status}] {BOLD}{description}{RESET}  →  {flag_label}")
        print(f"  Text: \"{text[:90]}{'...' if len(text) > 90 else ''}\"")

        if expected:
            print(f"  Expected labels : {', '.join(sorted(expected_set))}")
        else:
            print(f"  Expected labels : none (clean)")

        if detected:
            print(f"  Detected labels : {', '.join(sorted(detected))}")
        else:
            print(f"  Detected labels : none")

        if detected != expected_set and expected_set:
            missed = expected_set - detected
            extra  = detected - expected_set
            if missed:
                print(f"  {YELLOW}Missed          : {', '.join(sorted(missed))}{RESET}")
            if extra:
                print(f"  {YELLOW}Extra flags     : {', '.join(sorted(extra))}{RESET}")

        print(f"  Scores:")
        for label, score in sorted(result["scores"].items(), key=lambda x: -x[1]):
            colour = RED if score >= 0.5 else (YELLOW if score >= 0.25 else RESET)
            print(f"    {label:<20} {colour}{score:.4f}  {_bar(score)}{RESET}")

    # Summary
    total = len(TEST_CASES)
    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}  SUMMARY{RESET}")
    print(f"{'='*70}")
    print(f"  Total test cases       : {total}")
    print(f"  Correctly classified   : {passed} / {total}  ({100*passed/total:.0f}%)")
    print(f"  Toxic correctly flagged: {total_flagged_correctly}")
    print(f"  Clean correctly passed : {total_clean_correctly}")
    print(f"  False positives (clean text flagged) : {false_positives}")
    print(f"  False negatives (toxic text missed)  : {false_negatives}")
    print(f"{'='*70}\n")

    all_labels = {"toxicity", "obscene", "threat", "insult", "identity_hate"}
    labels_covered = set()
    for _, _, _, expected in TEST_CASES:
        labels_covered.update(expected)

    print(f"  Labels covered by test cases: {', '.join(sorted(labels_covered))}")
    missing = all_labels - labels_covered
    if missing:
        print(f"  {YELLOW}Labels NOT covered: {', '.join(sorted(missing))}{RESET}")
    else:
        print(f"  {GREEN}All 5 labels are represented in the test suite.{RESET}")
    print()


if __name__ == "__main__":
    run_demo()
