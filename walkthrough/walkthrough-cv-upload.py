"""
CV Upload Walkthrough for Capstone freelance platform.

Tests the CV upload and parsing functionality.

Usage (from inside the backend container):
    pip install requests
    python walkthrough/walkthrough-cv-upload.py

Or from outside the container:
    docker exec -it capstone-backend python /app/walkthrough/walkthrough-cv-upload.py

Set BASE_URL below if your backend runs on a different host/port.
"""

import os
import sys
import datetime
import requests

# ── configuration ─────────────────────────────────────────────────────────────

BASE_URL = "http://localhost:8000"

# ── output tee (terminal + markdown file) ─────────────────────────────────────

class _Tee:
    """Write to both the real stdout and a file simultaneously."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.file = open(filepath, 'w', encoding='utf-8')
        self._stdout = sys.stdout
        sys.stdout = self

    def write(self, data: str):
        self._stdout.write(data)
        self.file.write(data)
        self.file.flush()

    def flush(self):
        self._stdout.flush()
        self.file.flush()

    def close(self):
        self.file.close()

    def fileno(self):
        return self._stdout.fileno()

    def isatty(self):
        return self._stdout.isatty()

    def __del__(self):
        if hasattr(self, 'file'):
            self.close()


def _start_tee() -> tuple[_Tee, str]:
    """
    Redirect sys.stdout through _Tee so all print() calls go to both the
    terminal and a markdown file.  Returns (tee_instance, filepath).
    """
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(out_dir, f"walkthrough_cv_upload_results_{ts}.md")
    tee = _Tee(filepath)
    sys.stdout = tee
    return tee, filepath


def _stop_tee(tee: _Tee, filepath: str) -> None:
    """Restore sys.stdout and flush/close the file."""
    sys.stdout = tee._stdout
    tee.close()
    print(f"\n  Results saved to: {filepath}")

# ── helpers ───────────────────────────────────────────────────────────────────

_step = 0

def step(title: str) -> None:
    global _step
    _step += 1
    print(f"\n{'='*60}")
    print(f"  Step {_step}: {title}")
    print(f"{'='*60}")


def post(endpoint: str, body: dict, token: str = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.post(f"{BASE_URL}{endpoint}", json=body, headers=headers, timeout=60)
    data = r.json()
    status = "OK" if r.ok else "FAIL"
    print(f"  POST {endpoint}  [{r.status_code}] {status}")
    if not r.ok:
        print(f"    Error: {data}")
    return data


def get(endpoint: str, token: str = None, params: dict = None) -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(f"{BASE_URL}{endpoint}", headers=headers, params=params, timeout=90)
    data = r.json()
    status = "OK" if r.ok else "FAIL"
    print(f"  GET  {endpoint}  [{r.status_code}] {status}")
    if not r.ok:
        print(f"    Error: {data}")
    return data


def post_file(endpoint: str, files: dict, data: dict = None, token: str = None) -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.post(f"{BASE_URL}{endpoint}", files=files, data=data, headers=headers, timeout=120)
    status = "OK" if r.ok else "FAIL"
    print(f"  POST {endpoint}  [{r.status_code}] {status}")
    if r.ok:
        return r.json()
    else:
        print(f"    Error: {r.text}")
        return {}


def extract(response: dict) -> dict:
    """Pull the actual payload out of ResponseSchema wrapper (key is 'details')."""
    return response.get("details", response)


def token_from_login(email: str, password: str) -> str:
    resp = post("/auth/login", {"email": email, "password": password})
    return extract(resp)["access_token"]


# ── main walkthrough ──────────────────────────────────────────────────────────

def run():
    tee, out_path = _start_tee()

    print("\n" + "="*60)
    print("  Capstone CV Upload Walkthrough")
    print("="*60)
    print(f"  Target: {BASE_URL}")
    print(f"  Output: {out_path}")

    # ── 1. Register freelancer ─────────────────────────────────────────────────

    step("Register freelancer (Angelica Suti Whiharto)")
    post("/auth/register", {
        "email": "angelica@testcv.com",
        "password": "SecurePass123",
        "user_type": "freelancer",
        "full_name": "Angelica Suti Whiharto"
    })

    # ── 2. Log in ─────────────────────────────────────────────────────────────

    step("Log in as freelancer")
    tok_freelancer = token_from_login("angelica@testcv.com", "SecurePass123")
    print("  Token obtained.")

    # ── 3. Get freelancer profile ─────────────────────────────────────────────

    step("Get freelancer profile ID")
    freelancers = extract(get("/freelancers", tok_freelancer))
    fid = freelancers[0]["freelancer_id"]
    print(f"  freelancer_id: {fid}")

    # ── 4. Upload CV without LLM ──────────────────────────────────────────────

    step("Upload CV (Angelica Suti Whiharto_CV.pdf) without LLM parsing")
    cv_path = os.path.join(os.path.dirname(__file__), "Angelica Suti Whiharto_CV.pdf")
    if not os.path.exists(cv_path):
        print(f"  ERROR: CV file not found at {cv_path}")
        return

    with open(cv_path, "rb") as f:
        files = {"file": ("Angelica Suti Whiharto_CV.pdf", f, "application/pdf")}
        data = {"use_llm": "false"}
        resp = post_file("/cv_upload", files=files, data=data, token=tok_freelancer)

    if resp:
        print("  Upload successful!")
        print(f"  File URL: {resp.get('file_url', 'N/A')}")
        print(f"  File Name: {resp.get('file_name', 'N/A')}")
        print(f"  File Type: {resp.get('file_type', 'N/A')}")
        parsed = resp.get("parsed_profile", {})
        print(f"  Parsed Name: {parsed.get('full_name', 'N/A')}")
        print(f"  Parsed Email: {parsed.get('email', 'N/A')}")
        print(f"  Parsed Phone: {parsed.get('phone', 'N/A')}")
        print(f"  Skills: {len(parsed.get('skills', []))} found")
        print(f"  Languages: {len(parsed.get('languages', []))} found")
        print(f"  Specialities: {len(parsed.get('specialities', []))} found")
        print(f"  Work Experience: {len(parsed.get('work_experience', []))} entries")
        print(f"  Education: {len(parsed.get('education', []))} entries")

    # ── 5. Verify database update ─────────────────────────────────────────────

    step("Verify CV URL saved in freelancer profile")
    freelancer_profile = extract(get(f"/freelancers/{fid}", tok_freelancer))
    cv_url = freelancer_profile.get("cv_file_url")
    if cv_url:
        print(f"  ✅ CV URL saved in database: {cv_url}")
    else:
        print("  ❌ CV URL not found in database")

    # ── 6. Upload CV with LLM (optional) ──────────────────────────────────────

    step("Upload CV again with LLM parsing enabled")
    with open(cv_path, "rb") as f:
        files = {"file": ("Angelica Suti Whiharto_CV.pdf", f, "application/pdf")}
        data = {"use_llm": "true"}
        resp_llm = post_file("/cv_upload", files=files, data=data, token=tok_freelancer)

    if resp_llm:
        print("  LLM Upload successful!")
        parsed_llm = resp_llm.get("parsed_profile", {})
        print(f"  LLM Parsed Name: {parsed_llm.get('full_name', 'N/A')}")
        print(f"  LLM Parsed Email: {parsed_llm.get('email', 'N/A')}")
        print(f"  LLM Parsed Phone: {parsed_llm.get('phone', 'N/A')}")
        print(f"  LLM Skills: {len(parsed_llm.get('skills', []))} found")
        print(f"  LLM Languages: {len(parsed_llm.get('languages', []))} found")
        print(f"  LLM Specialities: {len(parsed_llm.get('specialities', []))} found")

    # ── 7. Final verification ─────────────────────────────────────────────────

    step("Final verification - freelancer profile with CV")
    final_profile = extract(get(f"/freelancers/{fid}", tok_freelancer))
    print(f"  Full Name: {final_profile.get('full_name')}")
    print(f"  CV File URL: {final_profile.get('cv_file_url', 'Not set')}")
    bio = final_profile.get('bio')
    if bio:
        print(f"  Bio: {bio[:100]}...")
    else:
        print("  Bio: Not set")

    print("\n" + "="*60)
    print("  CV Upload Walkthrough Complete")
    print("="*60)

    _stop_tee(tee, out_path)


if __name__ == "__main__":
    run()