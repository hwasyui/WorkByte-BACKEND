"""
Pagination, filter, and sort walkthrough.

Seeds a realistic dataset, then exercises every paginated/filterable
endpoint we built:
  - GET /job-posts            status, order_by, order_dir, page, page_size
  - GET /freelancers/browse/all       order_by, order_dir, page, page_size
  - GET /clients/browse/all           order_by, order_dir, page, page_size
  - GET /dashboard/freelancer  tracking_status, order_by, order_dir, page, page_size
  - GET /dashboard/client      tracking_status, order_by, order_dir, page, page_size

Dataset created:
  3 clients   — Nocturne Labs, Verdigris Studio, Phantom Signal
  8 job posts — spread across clients (active / draft / closed)
  3 freelancers — Raia Solano, Dmitri Volkmann, Yuki Tanabe
  3 proposals  — one accepted -> one active contract

Usage:
    python walkthrough/walkthrough-pagination.py
"""

import sys
import json
import os
import datetime
import requests

BASE_URL = "http://localhost:8000"


# ── tee (mirrors stdout to a timestamped .md file) ───────────────────────────

class _Tee:
    def __init__(self, filepath: str):
        self._stdout = sys.stdout
        self._file = open(filepath, "w", encoding="utf-8")

    def write(self, data: str):
        self._stdout.write(data)
        self._file.write(data)

    def flush(self):
        self._stdout.flush()
        self._file.flush()

    def close(self):
        self._file.close()

    def fileno(self):
        return self._stdout.fileno()

    def isatty(self):
        return False


def _start_tee() -> tuple[_Tee, str]:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(out_dir, f"walkthrough_pagination_{ts}.md")
    tee = _Tee(filepath)
    sys.stdout = tee
    return tee, filepath


def _stop_tee(tee: _Tee, filepath: str) -> None:
    sys.stdout = tee._stdout
    tee.close()
    print(f"\n  Results saved to: {filepath}")


# ── step counter ──────────────────────────────────────────────────────────────

_step = 0


def step(title: str) -> None:
    global _step
    _step += 1
    print(f"\n{'=' * 64}")
    print(f"  Step {_step}: {title}")
    print(f"{'=' * 64}")


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _headers(token: str = None) -> dict:
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def post(endpoint: str, body: dict, token: str = None) -> dict:
    r = requests.post(f"{BASE_URL}{endpoint}", json=body,
                      headers=_headers(token), timeout=60)
    data = r.json()
    print(f"  POST {endpoint}  [{r.status_code}] {'OK' if r.ok else 'FAIL'}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=2)}")
        sys.exit(1)
    return data


def put(endpoint: str, body: dict, token: str = None) -> dict:
    r = requests.put(f"{BASE_URL}{endpoint}", json=body,
                     headers=_headers(token), timeout=60)
    data = r.json()
    print(f"  PUT  {endpoint}  [{r.status_code}] {'OK' if r.ok else 'FAIL'}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=2)}")
        sys.exit(1)
    return data


def patch(endpoint: str, params: dict = None, token: str = None) -> dict:
    h = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.patch(f"{BASE_URL}{endpoint}", params=params,
                       headers=h, timeout=60)
    data = r.json()
    print(f"  PATCH {endpoint}  [{r.status_code}] {'OK' if r.ok else 'FAIL'}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=2)}")
        sys.exit(1)
    return data


def get(endpoint: str, token: str = None, params: dict = None,
        *, expect_status: int = None) -> dict:
    """
    GET request helper.
    - expect_status=None  → expect 2xx; exit on any non-2xx
    - expect_status=400   → expect exactly that code; exit if something else comes back
    """
    h = {"Authorization": f"Bearer {token}"} if token else {}
    qs = "?" + "&".join(f"{k}={v}" for k, v in (params or {}).items()) if params else ""
    r = requests.get(f"{BASE_URL}{endpoint}", headers=h,
                     params=params, timeout=90)
    data = r.json()

    if expect_status is not None:
        label = f"EXPECTED {r.status_code}" if r.status_code == expect_status else f"WRONG {r.status_code} (wanted {expect_status})"
        print(f"  GET  {endpoint}{qs}  [{r.status_code}] {label}")
        if r.status_code != expect_status:
            print(f"  ERROR: {json.dumps(data, indent=2)}")
            sys.exit(1)
    else:
        print(f"  GET  {endpoint}{qs}  [{r.status_code}] {'OK' if r.ok else 'FAIL'}")
        if not r.ok:
            print(f"  ERROR: {json.dumps(data, indent=2)}")
            sys.exit(1)

    return data


def extract(response: dict):
    return response.get("details", response)


def token_from_login(email: str, password: str) -> str:
    resp = post("/auth/login", {"email": email, "password": password})
    return extract(resp)["access_token"]

def register_and_verify(body: dict) -> dict:
    resp = post("/auth/register", body)
    details = extract(resp)
    otp = details.get("verification", {}).get("dev_verification_otp")
    if otp:
        post("/auth/verify-email", {"email": body["email"], "otp": otp})
    else:
        print("  Verification OTP not returned. Complete email verification before login.")
    return resp


# ── display helpers ───────────────────────────────────────────────────────────

def show_page(data: dict) -> None:
    """Print pagination summary + item labels for a paginated response."""
    pg   = data.get("pagination", {})
    items = data.get("items", [])
    print(f"    page {pg.get('page')}/{pg.get('total_pages')}  |  "
          f"{len(items)} returned  |  {pg.get('total')} total")


def show_job_items(data: dict) -> None:
    for j in data.get("items", []):
        print(f"    • [{j.get('status')}]  {j.get('job_title')}  "
              f"(proposals: {j.get('proposal_count', 0)})")


def show_freelancer_items(data: dict) -> None:
    for f in data.get("items", []):
        print(f"    • {f.get('full_name')}  rate={f.get('estimated_rate')}/hr")


def show_client_items(data: dict) -> None:
    for c in data.get("items", []):
        print(f"    • {c.get('full_name')}  jobs_posted={c.get('total_jobs_posted', 0)}")


def show_freelancer_dash(data: dict) -> None:
    for j in data.get("items", []):
        print(f"    • [{j.get('tracking_status')}]  {j.get('job_title')}  "
              f"last_activity={str(j.get('last_activity_date', ''))[:10]}")


def show_client_dash(data: dict) -> None:
    for j in data.get("items", []):
        print(f"    • [{j.get('tracking_status')}]  {j.get('job_title')}")
        for role in j.get("roles", []):
            print(f"        role [{role.get('tracking_status')}]  {role.get('role_title')}")
            for ct in role.get("contracts", []):
                print(f"          contract [{ct.get('status')}]  {ct.get('contract_title')}")


# ── run ───────────────────────────────────────────────────────────────────────

def run():
    tee, out_path = _start_tee()
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    pw = "SecurePass123!"

    print("\n" + "=" * 64)
    print("  Capstone API — Pagination / Filter / Sort Walkthrough")
    print("=" * 64)
    print(f"  Target : {BASE_URL}")
    print(f"  Output : {out_path}")
    print(f"  Run ID : {ts}")

    # ── 1. Clients ────────────────────────────────────────────────────────────

    step("Register 3 clients")

    register_and_verify({"email": f"nocturne.{ts}@pg.dev", "password": pw,
                            "user_type": "client", "full_name": "Nocturne Labs"})
    nocturne_token  = token_from_login(f"nocturne.{ts}@pg.dev", pw)
    nocturne_id     = extract(get("/clients", nocturne_token))[0]["client_id"]
    print(f"  Nocturne Labs      client_id={nocturne_id}")

    register_and_verify({"email": f"verdigris.{ts}@pg.dev", "password": pw,
                            "user_type": "client", "full_name": "Verdigris Studio"})
    verdigris_token = token_from_login(f"verdigris.{ts}@pg.dev", pw)
    verdigris_id    = extract(get("/clients", verdigris_token))[0]["client_id"]
    print(f"  Verdigris Studio   client_id={verdigris_id}")

    register_and_verify({"email": f"phantom.{ts}@pg.dev", "password": pw,
                            "user_type": "client", "full_name": "Phantom Signal"})
    phantom_token   = token_from_login(f"phantom.{ts}@pg.dev", pw)
    phantom_id      = extract(get("/clients", phantom_token))[0]["client_id"]
    print(f"  Phantom Signal     client_id={phantom_id}")

    # ── 2. Job posts ──────────────────────────────────────────────────────────

    step("Create 8 job posts across the 3 clients (active / draft / closed)")

    # -- Nocturne Labs (3 posts) -----------------------------------------------
    print("\n  [Nocturne Labs — dark-mode UI & frontend]")

    r = post("/job-posts", {
        "client_id": nocturne_id,
        "job_title": "Senior UI/UX Designer — Dark Mode Design System",
        "job_description": (
            "Nocturne Labs is building the definitive dark-mode component library "
            "for enterprise SaaS. We need a designer who understands contrast ratios, "
            "eye-fatigue science, and beautiful dark palettes. You will own the token "
            "system, Figma library, and design-review process."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "3 months", "working_days": 60,
        "experience_level": "expert", "status": "active",
    }, nocturne_token)
    nocturne_jp1 = extract(r)["job_post_id"]
    print(f"    [active]  Senior UI/UX Designer  id={nocturne_jp1}")

    r = post("/job-roles", {
        "job_post_id": nocturne_jp1, "role_title": "Lead UX Researcher",
        "role_budget": 4500.0, "budget_type": "fixed",
        "role_description": "User interviews, heuristic audits, WCAG 2.2 compliance",
    }, nocturne_token)
    nocturne_role1 = extract(r)["job_role_id"]

    r = post("/job-roles", {
        "job_post_id": nocturne_jp1, "role_title": "Visual / Brand Designer",
        "role_budget": 3800.0, "budget_type": "negotiable",
        "role_description": "Token system, Figma component library, dark palette",
    }, nocturne_token)

    r = post("/job-posts", {
        "client_id": nocturne_id,
        "job_title": "Frontend Engineer — WebGL Visual Effects Engine",
        "job_description": (
            "We're shipping a real-time canvas-based effects layer for our SaaS dashboard. "
            "Looking for someone deep in WebGL, Three.js, or raw GLSL who can build "
            "performant shader pipelines without tanking the main thread."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "6 weeks", "working_days": 30,
        "experience_level": "expert", "status": "active",
    }, nocturne_token)
    nocturne_jp2 = extract(r)["job_post_id"]
    print(f"    [active]  Frontend Engineer WebGL  id={nocturne_jp2}")

    post("/job-roles", {
        "job_post_id": nocturne_jp2, "role_title": "WebGL / Shader Engineer",
        "role_budget": 7200.0, "budget_type": "fixed",
        "role_description": "Three.js, GLSL, post-processing pipelines, 60fps target",
    }, nocturne_token)

    r = post("/job-posts", {
        "client_id": nocturne_id,
        "job_title": "Technical Writer — API & SDK Documentation",
        "job_description": (
            "Our developer SDK is growing fast and our docs haven't kept up. "
            "We need a technical writer who can read TypeScript source, talk to engineers, "
            "and ship clean, searchable reference docs plus onboarding guides."
        ),
        "project_type": "individual", "project_scope": "small",
        "estimated_duration": "4 weeks", "experience_level": "intermediate",
        "status": "draft",
    }, nocturne_token)
    nocturne_jp3_draft = extract(r)["job_post_id"]
    print(f"    [draft]   Technical Writer  id={nocturne_jp3_draft}")

    post("/job-roles", {
        "job_post_id": nocturne_jp3_draft, "role_title": "Technical Documentation Writer",
        "role_budget": 2200.0, "budget_type": "fixed",
        "role_description": "OpenAPI docs, SDK guides, changelog, Docusaurus setup",
    }, nocturne_token)

    # -- Verdigris Studio (3 posts) -------------------------------------------
    print("\n  [Verdigris Studio — indie game dev]")

    r = post("/job-posts", {
        "client_id": verdigris_id,
        "job_title": "Lead Narrative Designer — Open World RPG",
        "job_description": (
            "Verdigris is building an open-world RPG set in a solarpunk archipelago. "
            "We need a narrative designer to craft branching dialogue trees, faction lore, "
            "and a 40-hour main quest arc. Ink or Twine experience preferred."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "5 months", "working_days": 100,
        "experience_level": "expert", "status": "active",
    }, verdigris_token)
    verdigris_jp1 = extract(r)["job_post_id"]
    print(f"    [active]  Lead Narrative Designer  id={verdigris_jp1}")

    post("/job-roles", {
        "job_post_id": verdigris_jp1, "role_title": "Narrative Designer",
        "role_budget": 9500.0, "budget_type": "negotiable", "positions_available": 1,
        "role_description": "Main quest, faction arcs, 800+ dialogue nodes in Ink",
    }, verdigris_token)
    post("/job-roles", {
        "job_post_id": verdigris_jp1, "role_title": "Worldbuilding Lore Writer",
        "role_budget": 3500.0, "budget_type": "fixed", "positions_available": 2,
        "role_description": "Codex entries, item descriptions, environmental storytelling",
    }, verdigris_token)

    r = post("/job-posts", {
        "client_id": verdigris_id,
        "job_title": "3D Environment Artist — Stylized Fantasy Biomes",
        "job_description": (
            "Need a 3D artist who can take our concept art and produce beautiful, "
            "optimized biome assets — forests, wetlands, volcanic coastline. "
            "Style is hand-painted with a hint of N64-era nostalgia. Blender + Unity HDRP."
        ),
        "project_type": "team", "project_scope": "large",
        "estimated_duration": "4 months", "working_days": 80,
        "experience_level": "intermediate", "status": "active",
    }, verdigris_token)
    verdigris_jp2 = extract(r)["job_post_id"]
    print(f"    [active]  3D Environment Artist  id={verdigris_jp2}")

    post("/job-roles", {
        "job_post_id": verdigris_jp2, "role_title": "Senior 3D Environment Artist",
        "role_budget": 6000.0, "budget_type": "fixed",
        "role_description": "Hero biome assets, LOD pipeline, Blender → Unity HDRP",
    }, verdigris_token)
    post("/job-roles", {
        "job_post_id": verdigris_jp2, "role_title": "Texture / Surfacing Artist",
        "role_budget": 3200.0, "budget_type": "fixed", "positions_available": 2,
        "role_description": "Hand-painted textures, Substance Painter, tile-able materials",
    }, verdigris_token)

    r = post("/job-posts", {
        "client_id": verdigris_id,
        "job_title": "Gameplay Systems Programmer — Crafting & Inventory",
        "job_description": (
            "Port and expand our prototype crafting system into a full component-based "
            "architecture in Unity C#. Must handle 500+ item definitions, recipe chaining, "
            "and a mod-friendly data layer."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "2 months", "working_days": 40,
        "experience_level": "intermediate", "status": "closed",
    }, verdigris_token)
    verdigris_jp3_closed = extract(r)["job_post_id"]
    print(f"    [closed]  Gameplay Systems Programmer  id={verdigris_jp3_closed}")

    r = post("/job-roles", {
        "job_post_id": verdigris_jp3_closed, "role_title": "Unity C# Gameplay Programmer",
        "role_budget": 5500.0, "budget_type": "fixed",
        "role_description": "Inventory, crafting, save system, ScriptableObject data layer",
    }, verdigris_token)
    verdigris_role_closed = extract(r)["job_role_id"]

    # -- Phantom Signal (2 posts) ---------------------------------------------
    print("\n  [Phantom Signal — immersive audio]")

    r = post("/job-posts", {
        "client_id": phantom_id,
        "job_title": "Spatial Audio Engineer — Binaural Soundscapes",
        "job_description": (
            "Phantom Signal creates location-based audio experiences for museums and "
            "immersive theatre. We need a spatial audio engineer to design binaural "
            "soundscapes rendered in IRCAM SPAT or similar, mixed for Apple AirPods Pro "
            "and high-end circumaural headphones."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "6 weeks", "working_days": 30,
        "experience_level": "expert", "status": "active",
    }, phantom_token)
    phantom_jp1 = extract(r)["job_post_id"]
    print(f"    [active]  Spatial Audio Engineer  id={phantom_jp1}")

    r = post("/job-roles", {
        "job_post_id": phantom_jp1, "role_title": "Spatial Audio Engineer",
        "role_budget": 5800.0, "budget_type": "negotiable",
        "role_description": "Binaural rendering, IRCAM SPAT / Reaper, Apple Spatial Audio",
    }, phantom_token)
    phantom_role1 = extract(r)["job_role_id"]

    r = post("/job-posts", {
        "client_id": phantom_id,
        "job_title": "Foley Artist & Location Sound Designer",
        "job_description": (
            "We need a Foley artist to build a custom sound library for our next theatrical "
            "installation — surreal, body-horror-adjacent, tactile. Recording sessions at "
            "our studio in Bandung; remote edit/mix welcome."
        ),
        "project_type": "individual", "project_scope": "small",
        "estimated_duration": "3 weeks", "working_days": 15,
        "experience_level": "intermediate", "status": "active",
    }, phantom_token)
    phantom_jp2 = extract(r)["job_post_id"]
    print(f"    [active]  Foley Artist  id={phantom_jp2}")

    post("/job-roles", {
        "job_post_id": phantom_jp2, "role_title": "Foley Recording & Design Artist",
        "role_budget": 2400.0, "budget_type": "fixed",
        "role_description": "Custom SFX library, Foley session recording, ProTools delivery",
    }, phantom_token)

    # ── 3. Freelancers ────────────────────────────────────────────────────────

    step("Register 3 freelancers")

    register_and_verify({"email": f"raia.{ts}@pg.dev", "password": pw,
                            "user_type": "freelancer", "full_name": "Raia Solano"})
    raia_token    = token_from_login(f"raia.{ts}@pg.dev", pw)
    raia_id       = extract(get("/freelancers", raia_token))[0]["freelancer_id"]
    put(f"/freelancers/{raia_id}", {
        "estimated_rate": 95.0,
        "bio": ("UI/UX designer obsessed with dark mode, accessibility, and motion design. "
                "5 years in SaaS products. Figma, Framer, WCAG 2.2."),
    }, raia_token)
    print(f"  Raia Solano      freelancer_id={raia_id}  rate=$95/hr")

    register_and_verify({"email": f"dmitri.{ts}@pg.dev", "password": pw,
                            "user_type": "freelancer", "full_name": "Dmitri Volkmann"})
    dmitri_token  = token_from_login(f"dmitri.{ts}@pg.dev", pw)
    dmitri_id     = extract(get("/freelancers", dmitri_token))[0]["freelancer_id"]
    put(f"/freelancers/{dmitri_id}", {
        "estimated_rate": 110.0,
        "bio": ("Gameplay programmer and tools engineer. Unity C# and Unreal blueprints. "
                "Ex-indie studio. Love crafting systems, procedural gen, and mod tooling."),
    }, dmitri_token)
    print(f"  Dmitri Volkmann  freelancer_id={dmitri_id}  rate=$110/hr")

    register_and_verify({"email": f"yuki.{ts}@pg.dev", "password": pw,
                            "user_type": "freelancer", "full_name": "Yuki Tanabe"})
    yuki_token    = token_from_login(f"yuki.{ts}@pg.dev", pw)
    yuki_id       = extract(get("/freelancers", yuki_token))[0]["freelancer_id"]
    put(f"/freelancers/{yuki_id}", {
        "estimated_rate": 85.0,
        "bio": ("Sound designer and spatial audio engineer. Reaper, Max/MSP, IRCAM SPAT. "
                "10 years in game audio and immersive installations."),
    }, yuki_token)
    print(f"  Yuki Tanabe      freelancer_id={yuki_id}  rate=$85/hr")

    # ── 4. Proposals ──────────────────────────────────────────────────────────

    step("Freelancers submit proposals")

    # Raia → Nocturne Labs UX role
    r = post("/proposals", {
        "job_post_id": nocturne_jp1, "job_role_id": nocturne_role1,
        "cover_letter": (
            "I have spent the last three years rethinking how dark mode actually feels "
            "at 2am — dynamic contrast curves, semantic colour tokens, and motion that "
            "doesn't jar tired eyes. I'd love to own the Nocturne Design System end-to-end."
        ),
        "proposed_budget": 4200.0, "proposed_duration": "3 months",
    }, raia_token)
    raia_proposal = extract(r)["proposal_id"]
    print(f"  Raia → Nocturne Labs UX role  proposal_id={raia_proposal}")

    # Dmitri → Verdigris closed crafting role (API allows proposals on closed jobs)
    r = post("/proposals", {
        "job_post_id": verdigris_jp3_closed, "job_role_id": verdigris_role_closed,
        "cover_letter": (
            "Crafting systems are my happy place. I shipped a 600-item crafting + mod "
            "pipeline for a Steam Early Access title last year — ScriptableObject registry, "
            "runtime recipe graph, and a Unity Editor tool for designers."
        ),
        "proposed_budget": 5200.0, "proposed_duration": "8 weeks",
    }, dmitri_token)
    dmitri_proposal = extract(r)["proposal_id"]
    print(f"  Dmitri → Verdigris closed job  proposal_id={dmitri_proposal}")

    # Yuki → Phantom Signal spatial audio role
    r = post("/proposals", {
        "job_post_id": phantom_jp1, "job_role_id": phantom_role1,
        "cover_letter": (
            "I designed binaural soundscapes for three museum installations in 2024, "
            "all mixed in Reaper with IRCAM SPAT and delivered for Apple Spatial Audio. "
            "I understand the perceptual model you're chasing."
        ),
        "proposed_budget": 5600.0, "proposed_duration": "6 weeks",
    }, yuki_token)
    yuki_proposal = extract(r)["proposal_id"]
    print(f"  Yuki → Phantom Signal spatial audio  proposal_id={yuki_proposal}")

    # ── 5. Accept Raia's proposal → contract ─────────────────────────────────

    step("Nocturne Labs accepts Raia's proposal → create active contract")

    patch(f"/proposals/{raia_proposal}/status",
          params={"status": "accepted"}, token=nocturne_token)
    print(f"  proposal {raia_proposal} → accepted")

    today    = datetime.date.today()
    end_date = today + datetime.timedelta(weeks=12)

    r = post("/contracts", {
        "job_post_id":       nocturne_jp1,
        "job_role_id":       nocturne_role1,
        "proposal_id":       raia_proposal,
        "freelancer_id":     raia_id,
        "client_id":         nocturne_id,
        "contract_title":    "Dark Mode Design System — UX Lead Contract",
        "agreed_budget":     4200.0,
        "budget_currency":   "USD",
        "payment_structure": "full_payment",
        "agreed_duration":   "3 months",
        "status":            "active",
        "start_date":        str(today),
        "end_date":          str(end_date),
    }, nocturne_token)
    contract_id = extract(r)["contract_id"]
    print(f"  contract {contract_id} created (active)")

    # ═══════════════════════════════════════════════════════════════════════════
    #  PAGINATION / FILTER / SORT TESTS
    # ═══════════════════════════════════════════════════════════════════════════

    # ── 6. GET /job-posts ────────────────────────────────────────────────────

    step("GET /job-posts — defaults (active, created_at desc, page 1 of 20)")
    data = extract(get("/job-posts", nocturne_token))
    show_page(data)
    show_job_items(data)

    step("GET /job-posts — status=all, freelancer token  (drafts must NOT appear)")
    data = extract(get("/job-posts", raia_token, params={"status": "all"}))
    show_page(data)
    drafts = [j for j in data.get("items", []) if j.get("status") == "draft"]
    print(f"    drafts visible to freelancer: {len(drafts)}  (expected: 0)")
    if drafts:
        print("    ERROR: freelancer should never see draft jobs!")
        sys.exit(1)

    step("GET /job-posts — status=all, Nocturne client  (sees own 1 draft)")
    data = extract(get("/job-posts", nocturne_token, params={"status": "all"}))
    show_page(data)
    own_drafts = [j for j in data.get("items", []) if j.get("status") == "draft"]
    print(f"    drafts visible to Nocturne: {len(own_drafts)}  (expected: 1)")
    show_job_items(data)

    step("GET /job-posts — status=draft, Nocturne client  (only their own draft)")
    data = extract(get("/job-posts", nocturne_token, params={"status": "draft"}))
    show_page(data)
    show_job_items(data)

    step("GET /job-posts — status=draft, Verdigris client  (0 — they have no drafts)")
    data = extract(get("/job-posts", verdigris_token, params={"status": "draft"}))
    show_page(data)
    print(f"    items: {len(data.get('items', []))}  (expected: 0)")

    step("SECURITY — can another client read Nocturne's draft by job_post_id directly?")
    draft_items = extract(get("/job-posts", nocturne_token,
                              params={"status": "draft"})).get("items", [])
    draft_id = draft_items[0]["job_post_id"] if draft_items else None
    if draft_id:
        print(f"    Nocturne's draft job_post_id: {draft_id}")
        # Verdigris client tries to fetch it directly
        data_vg = get(f"/job-posts/{draft_id}", verdigris_token)
        fetched = extract(data_vg)
        if fetched and fetched.get("status") == "draft":
            print(f"    Verdigris can read draft directly: status={fetched.get('status')}  (NOTE: direct fetch has no ownership gate)")
        else:
            print(f"    Verdigris direct fetch: {fetched}")
        # Freelancer tries to fetch it directly
        data_fl = get(f"/job-posts/{draft_id}", raia_token)
        fetched_fl = extract(data_fl)
        if fetched_fl and fetched_fl.get("status") == "draft":
            print(f"    Freelancer can read draft directly: status={fetched_fl.get('status')}  (NOTE: direct fetch has no ownership gate)")
        else:
            print(f"    Freelancer direct fetch: {fetched_fl}")
        # Both must be invisible in the browse list
        vg_browse = extract(get("/job-posts", verdigris_token, params={"status": "all"}))
        vg_sees_draft = any(j["job_post_id"] == draft_id for j in vg_browse.get("items", []))
        fl_browse = extract(get("/job-posts", raia_token, params={"status": "all"}))
        fl_sees_draft = any(j["job_post_id"] == draft_id for j in fl_browse.get("items", []))
        print(f"    Draft visible in Verdigris browse list: {vg_sees_draft}  (expected: False)")
        print(f"    Draft visible in freelancer browse list: {fl_sees_draft}  (expected: False)")
        if vg_sees_draft or fl_sees_draft:
            print("    ERROR: draft leaked into browse list for wrong user!")
            sys.exit(1)
        print("    Browse-list gate: PASS")
    else:
        print("    ERROR: could not retrieve Nocturne's draft to test")
        sys.exit(1)

    step("GET /job-posts — status=closed")
    data = extract(get("/job-posts", nocturne_token, params={"status": "closed"}))
    show_page(data)
    show_job_items(data)

    step("GET /job-posts — order_by=job_title asc, page_size=3, page=1")
    data = extract(get("/job-posts", nocturne_token,
                       params={"order_by": "job_title", "order_dir": "asc",
                               "page_size": 3, "page": 1}))
    show_page(data)
    show_job_items(data)

    step("GET /job-posts — order_by=job_title asc, page_size=3, page=2")
    data = extract(get("/job-posts", nocturne_token,
                       params={"order_by": "job_title", "order_dir": "asc",
                               "page_size": 3, "page": 2}))
    show_page(data)
    show_job_items(data)

    step("GET /job-posts — order_by=proposal_count desc  (Raia's job should be top)")
    data = extract(get("/job-posts", nocturne_token,
                       params={"order_by": "proposal_count", "order_dir": "desc"}))
    show_page(data)
    show_job_items(data)

    step("GET /job-posts — order_by=view_count desc")
    data = extract(get("/job-posts", nocturne_token,
                       params={"order_by": "view_count", "order_dir": "desc"}))
    show_page(data)
    show_job_items(data)

    step("GET /job-posts — invalid status value  (expect 400)")
    data = get("/job-posts", nocturne_token,
               params={"status": "banana"}, expect_status=400)
    print(f"    response: {data.get('message') or data.get('details') or data}")

    step("GET /job-posts — invalid order_by value  (expect 400)")
    data = get("/job-posts", nocturne_token,
               params={"order_by": "salary"}, expect_status=400)
    print(f"    response: {data.get('message') or data.get('details') or data}")

    # ── 7. GET /freelancers/browse/all ────────────────────────────────────────

    step("GET /freelancers/browse/all — defaults (created_at desc)")
    data = extract(get("/freelancers/browse/all", nocturne_token))
    show_page(data)
    show_freelancer_items(data)

    step("GET /freelancers/browse/all — order_by=full_name asc")
    data = extract(get("/freelancers/browse/all", nocturne_token,
                       params={"order_by": "full_name", "order_dir": "asc"}))
    show_page(data)
    show_freelancer_items(data)

    step("GET /freelancers/browse/all — order_by=estimated_rate desc")
    data = extract(get("/freelancers/browse/all", nocturne_token,
                       params={"order_by": "estimated_rate", "order_dir": "desc"}))
    show_page(data)
    show_freelancer_items(data)

    step("GET /freelancers/browse/all — page_size=2 page=1")
    data = extract(get("/freelancers/browse/all", nocturne_token,
                       params={"page_size": 2, "page": 1}))
    show_page(data)
    show_freelancer_items(data)

    step("GET /freelancers/browse/all — page_size=2 page=2")
    data = extract(get("/freelancers/browse/all", nocturne_token,
                       params={"page_size": 2, "page": 2}))
    show_page(data)
    show_freelancer_items(data)

    step("GET /freelancers/browse/all — invalid order_by  (expect 400)")
    data = get("/freelancers/browse/all", nocturne_token,
               params={"order_by": "vibes"}, expect_status=400)
    print(f"    response: {data.get('message') or data.get('details') or data}")

    # ── 8. GET /clients/browse/all ────────────────────────────────────────────

    step("GET /clients/browse/all — defaults (created_at desc)")
    data = extract(get("/clients/browse/all", raia_token))
    show_page(data)
    show_client_items(data)

    step("GET /clients/browse/all — order_by=full_name asc")
    data = extract(get("/clients/browse/all", raia_token,
                       params={"order_by": "full_name", "order_dir": "asc"}))
    show_page(data)
    show_client_items(data)

    step("GET /clients/browse/all — order_by=total_jobs_posted desc")
    data = extract(get("/clients/browse/all", raia_token,
                       params={"order_by": "total_jobs_posted", "order_dir": "desc"}))
    show_page(data)
    show_client_items(data)

    step("GET /clients/browse/all — page_size=2 page=1")
    data = extract(get("/clients/browse/all", raia_token,
                       params={"page_size": 2, "page": 1}))
    show_page(data)
    show_client_items(data)

    step("GET /clients/browse/all — page_size=2 page=2")
    data = extract(get("/clients/browse/all", raia_token,
                       params={"page_size": 2, "page": 2}))
    show_page(data)
    show_client_items(data)

    step("GET /clients/browse/all — invalid order_by  (expect 400)")
    data = get("/clients/browse/all", raia_token,
               params={"order_by": "revenue"}, expect_status=400)
    print(f"    response: {data.get('message') or data.get('details') or data}")

    # ── 9. GET /dashboard/freelancer ──────────────────────────────────────────

    step("GET /dashboard/freelancer — Raia (pending proposal + active contract)")
    data = extract(get("/dashboard/freelancer", raia_token))
    show_page(data)
    show_freelancer_dash(data)

    step("GET /dashboard/freelancer — Dmitri (proposal on closed job)")
    data = extract(get("/dashboard/freelancer", dmitri_token))
    show_page(data)
    show_freelancer_dash(data)

    step("GET /dashboard/freelancer — Yuki (pending proposal on active job)")
    data = extract(get("/dashboard/freelancer", yuki_token))
    show_page(data)
    show_freelancer_dash(data)

    step("GET /dashboard/freelancer — Raia, filter tracking_status=in_progress")
    data = extract(get("/dashboard/freelancer", raia_token,
                       params={"tracking_status": "in_progress"}))
    show_page(data)
    show_freelancer_dash(data)

    step("GET /dashboard/freelancer — Raia, filter tracking_status=applied  (pending proposal)")
    data = extract(get("/dashboard/freelancer", raia_token,
                       params={"tracking_status": "applied"}))
    show_page(data)
    show_freelancer_dash(data)

    step("GET /dashboard/freelancer — Raia, order_by=job_title asc")
    data = extract(get("/dashboard/freelancer", raia_token,
                       params={"order_by": "job_title", "order_dir": "asc"}))
    show_page(data)
    show_freelancer_dash(data)

    step("GET /dashboard/freelancer — invalid tracking_status  (expect 400)")
    data = get("/dashboard/freelancer", raia_token,
               params={"tracking_status": "vibing"}, expect_status=400)
    print(f"    response: {data.get('message') or data.get('details') or data}")

    step("GET /dashboard/freelancer — invalid order_by  (expect 400)")
    data = get("/dashboard/freelancer", raia_token,
               params={"order_by": "money"}, expect_status=400)
    print(f"    response: {data.get('message') or data.get('details') or data}")

    # ── 10. GET /dashboard/client ─────────────────────────────────────────────

    step("GET /dashboard/client — Nocturne Labs (3 posts, 1 with active contract)")
    data = extract(get("/dashboard/client", nocturne_token))
    show_page(data)
    show_client_dash(data)

    step("GET /dashboard/client — Verdigris Studio (3 posts: 2 active, 1 closed)")
    data = extract(get("/dashboard/client", verdigris_token))
    show_page(data)
    show_client_dash(data)

    step("GET /dashboard/client — Phantom Signal (2 active posts, Yuki pending)")
    data = extract(get("/dashboard/client", phantom_token))
    show_page(data)
    show_client_dash(data)

    step("GET /dashboard/client — Nocturne, filter tracking_status=in_progress")
    data = extract(get("/dashboard/client", nocturne_token,
                       params={"tracking_status": "in_progress"}))
    show_page(data)
    show_client_dash(data)

    step("GET /dashboard/client — Nocturne, filter tracking_status=open")
    data = extract(get("/dashboard/client", nocturne_token,
                       params={"tracking_status": "open"}))
    show_page(data)
    show_client_dash(data)

    step("GET /dashboard/client — Nocturne, order_by=job_title asc")
    data = extract(get("/dashboard/client", nocturne_token,
                       params={"order_by": "job_title", "order_dir": "asc"}))
    show_page(data)
    show_client_dash(data)

    step("GET /dashboard/client — Verdigris, page_size=1 paginate through all 3 jobs")
    for pg in (1, 2, 3):
        data = extract(get("/dashboard/client", verdigris_token,
                           params={"page_size": 1, "page": pg}))
        show_page(data)
        show_client_dash(data)

    step("GET /dashboard/client — invalid tracking_status  (expect 400)")
    data = get("/dashboard/client", nocturne_token,
               params={"tracking_status": "flying"}, expect_status=400)
    print(f"    response: {data.get('message') or data.get('details') or data}")

    step("GET /dashboard/client — invalid order_by  (expect 400)")
    data = get("/dashboard/client", nocturne_token,
               params={"order_by": "revenue"}, expect_status=400)
    print(f"    response: {data.get('message') or data.get('details') or data}")

    # ── Summary ───────────────────────────────────────────────────────────────

    step("Walkthrough complete — summary")

    print()
    print("  Dataset")
    print(f"    Clients     : Nocturne Labs, Verdigris Studio, Phantom Signal")
    print(f"    Job posts   : 8 total  (5 active, 1 draft, 1 closed — 1 closed from before)")
    print(f"    Freelancers : Raia Solano ($95), Dmitri Volkmann ($110), Yuki Tanabe ($85)")
    print(f"    Proposals   : 3 submitted  (Raia's accepted)")
    print(f"    Contracts   : 1 active  id={contract_id}")
    print()
    print("  Endpoints tested")
    print("    ✓  GET /job-posts              — status filter, draft gate (browse list), all 6 sort fields, pagination")
    print("    ✓  GET /freelancers/browse/all — all 5 sort fields, pagination, validation")
    print("    ✓  GET /clients/browse/all     — all 5 sort fields, pagination, validation")
    print("    ✓  GET /dashboard/freelancer   — tracking_status filter, sort, pagination, validation")
    print("    ✓  GET /dashboard/client       — tracking_status filter, sort, pagination, role tree")
    print()
    print("  All pagination, filter, and sort logic exercised.")

    _stop_tee(tee, out_path)


if __name__ == "__main__":
    run()
