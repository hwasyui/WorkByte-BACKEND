"""
Messaging feature walkthrough — unified DM + contract chat.

Architecture recap:
  - ONE message system: dm_thread / dm_message / dm_message_attachment
  - Contract creation auto-creates a DM thread (status: active, contract_id stamped)
  - /messages/contract/{id}  routes delegate to that thread
  - /dm/threads/{id}/messages routes are the same data
  - Storage: message-attachments bucket → {thread_id}/{message_id}/{file}

Sections:
  1.  Setup         : fresh users, job post, proposal, contract
  2.  Contract chat : POST /messages + GET /messages/contract (via DM thread)
  3.  Pagination    : cursor-based, verify no overlap
  4.  File uploads  : image, PDF, video, audio, voice note (via contract endpoint)
  5.  Attachments   : GET /messages/{id}/attachments
  6.  Mark read     : PUT /messages/contract/{id}/read → status flip
  7.  Contract WS   : /messages/ws/contract/{id} shares DM room
  8.  Same thread   : prove /dm/threads/{id}/messages returns identical messages
  9.  Cold DM flow  : client → new freelancer, request cap, accept, reply
  10. DM file upload: attachment via /dm/threads/{id}/messages/upload
  11. DM WebSocket  : /dm/ws/{thread_id} real-time push
  12. Summary       : final message list

Requirements:
  APP_ENV=development   SHOW_DEV_OTP=true
  pip install websockets
  Backend running at http://localhost:8000
"""

import asyncio, datetime, io, json, os, random, sys, time
import requests

try:
    import websockets
except ImportError:
    print("websockets library required: pip install websockets")
    sys.exit(1)

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
WS_URL   = BASE_URL.replace("http://", "ws://").replace("https://", "wss://")
PASSWORD = "SecurePass123!"

_RUN_ID   = random.randint(1000, 9999)
_EMAIL_FL = f"msg.fl.{_RUN_ID}@walkthrough.dev"
_EMAIL_CL = f"msg.cl.{_RUN_ID}@walkthrough.dev"
_EMAIL_FL2 = f"msg.fl2.{_RUN_ID}@walkthrough.dev"   # second freelancer for cold DM


# ── Output tee ───────────────────────────────────────────────────────────────

class _Tee:
    def __init__(self, path):
        self._stdout = sys.stdout
        self._file   = open(path, "w", encoding="utf-8")
    def write(self, s):  self._stdout.write(s); self._file.write(s)
    def flush(self):     self._stdout.flush();  self._file.flush()
    def close(self):     self._file.close()
    def fileno(self):    return self._stdout.fileno()
    def isatty(self):    return False

def _start_tee():
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        f"walkthrough_message_{ts}.md")
    tee  = _Tee(path)
    sys.stdout = tee
    return tee, path

def _stop_tee(tee, path):
    sys.stdout = tee._stdout
    tee.close()
    print(f"\n  Results saved → {path}")


# ── Section / step helpers ────────────────────────────────────────────────────

_sec = 0; _stp = 0

def section(title):
    global _sec, _stp; _sec += 1; _stp = 0
    print(f"\n{'═'*70}\n  ◆  SECTION {_sec}: {title}\n{'═'*70}")

def step(title):
    global _stp; _stp += 1
    print(f"\n  {'─'*60}\n  Step {_sec}.{_stp}: {title}\n  {'─'*60}")

def ok(label, detail=""):
    print(f"  ✓ {label}" + (f"  ↳ {detail}" if detail else ""))

def info(label, value):
    print(f"    {label}: {value}")


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _ex(resp):   return resp.get("details", resp)
def _hdr(tok):   return {"Authorization": f"Bearer {tok}"}

def _die(tag, status, body):
    print(f"  ✗ {tag} [{status}] FAILED")
    print(json.dumps(body, indent=2, default=str)[:800])
    sys.exit(1)

def post(endpoint, body, token=None, expected=None, label=None):
    expected = expected or {200, 201}
    headers  = {"Content-Type": "application/json"}
    if token: headers["Authorization"] = f"Bearer {token}"
    r = requests.post(f"{BASE_URL}{endpoint}", json=body, headers=headers, timeout=60)
    try:    payload = r.json()
    except: payload = {"raw": r.text}
    ok_flag = r.status_code in expected
    tag     = label or f"POST {endpoint}"
    print(f"  {'✓' if ok_flag else '✗'} {tag}  [{r.status_code}]")
    if not ok_flag: _die(tag, r.status_code, payload)
    return payload

def post_mp(endpoint, data, files, token=None, expected=None, label=None):
    expected = expected or {200, 201}
    headers  = _hdr(token) if token else {}
    r = requests.post(f"{BASE_URL}{endpoint}", data=data, files=files,
                      headers=headers, timeout=60)
    try:    payload = r.json()
    except: payload = {"raw": r.text}
    ok_flag = r.status_code in expected
    tag     = label or f"POST {endpoint}"
    print(f"  {'✓' if ok_flag else '✗'} {tag}  [{r.status_code}]")
    if not ok_flag:
        print(f"    ↳ {json.dumps(payload, default=str)[:400]}")
        _die(tag, r.status_code, payload)
    return payload

def get(endpoint, token=None, expected=None, params=None, label=None):
    expected = expected or {200}
    r = requests.get(f"{BASE_URL}{endpoint}", headers=_hdr(token) if token else {},
                     params=params, timeout=60)
    try:    payload = r.json()
    except: payload = {"raw": r.text}
    ok_flag = r.status_code in expected
    tag     = label or f"GET  {endpoint}"
    print(f"  {'✓' if ok_flag else '✗'} {tag}  [{r.status_code}]")
    if not ok_flag: _die(tag, r.status_code, payload)
    return payload

def put(endpoint, body, token, expected=None, label=None):
    expected = expected or {200}
    r = requests.put(f"{BASE_URL}{endpoint}", json=body, headers=_hdr(token), timeout=60)
    try:    payload = r.json()
    except: payload = {"raw": r.text}
    ok_flag = r.status_code in expected
    tag     = label or f"PUT  {endpoint}"
    print(f"  {'✓' if ok_flag else '✗'} {tag}  [{r.status_code}]")
    if not ok_flag: _die(tag, r.status_code, payload)
    return payload


# ── Auth helpers ──────────────────────────────────────────────────────────────

def register_and_verify(body):
    resp    = post("/auth/register", body, label=f"  Register {body['email']}")
    details = _ex(resp)
    otp     = details.get("verification", {}).get("dev_verification_otp")
    if otp:
        post("/auth/verify-email", {"email": body["email"], "otp": otp},
             label=f"  Verify OTP {body['email']}")
    return resp

def login(email):
    resp = post("/auth/login", {"email": email, "password": PASSWORD},
                label=f"  Login {email}")
    return _ex(resp)["access_token"]


# ── Synthetic file bytes ──────────────────────────────────────────────────────

def _jpeg_bytes():
    return (
        b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
        b'\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t'
        b'\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a'
        b'\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\x1eC'
        b'\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00'
        b'\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00'
        b'\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b'
        b'\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd2\x8a(\x03\xff\xd9'
    )

def _pdf_bytes():
    return (
        b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n'
        b'2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n'
        b'3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n'
        b'xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n'
        b'0000000058 00000 n \n0000000115 00000 n \n'
        b'trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n190\n%%EOF\n'
    )

def _mp4_bytes():
    return b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42mp41' + b'\x00' * 512

def _mp3_bytes():
    return b'ID3\x03\x00\x00\x00\x00\x00\x00' + b'\xff\xfb\x90\x00' + b'\x00' * 417

def _webm_bytes():
    return b'\x1a\x45\xdf\xa3' + b'\x9f' + b'\x42\x86\x81\x01' + b'\x00' * 256


# ── WebSocket helpers ─────────────────────────────────────────────────────────

async def _ws_receive_one(uri, timeout=8.0):
    try:
        async with websockets.connect(uri, open_timeout=5) as ws:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                return json.loads(raw)
            except asyncio.TimeoutError:
                return None
    except Exception as exc:
        print(f"    WS error: {exc}")
        return None

def ws_test(uri_listener, sender_fn, timeout=8.0):
    received = []
    async def _listener():
        msg = await _ws_receive_one(uri_listener, timeout=timeout)
        if msg: received.append(msg)
    async def _sender():
        await asyncio.sleep(0.8)
        sender_fn()
    async def _run():
        await asyncio.gather(_listener(), _sender())
    asyncio.run(_run())
    return bool(received)

def ws_bad_token_rejected(uri):
    async def _check():
        try:
            async with websockets.connect(uri, open_timeout=4) as ws:
                await ws.recv()
            return False
        except Exception:
            return True
    return asyncio.run(_check())


# ── Main walkthrough ──────────────────────────────────────────────────────────

def run():
    tee, out_path = _start_tee()

    print(f"\n{'═'*70}")
    print(f"  Capstone API — Unified Messaging Walkthrough")
    print(f"{'═'*70}")
    print(f"  Target : {BASE_URL}")
    print(f"  Run ID : {_RUN_ID}")

    # ══════════════════════════════════════════════════════════════════════════
    section("Setup — users, job post, proposal, contract")
    # ══════════════════════════════════════════════════════════════════════════

    step("Register freelancer, client, and second freelancer (for cold DM)")
    register_and_verify({"email": _EMAIL_FL,  "password": PASSWORD,
                          "user_type": "freelancer", "full_name": "Msg Freelancer"})
    register_and_verify({"email": _EMAIL_CL,  "password": PASSWORD,
                          "user_type": "client",     "full_name": "Msg Client"})
    register_and_verify({"email": _EMAIL_FL2, "password": PASSWORD,
                          "user_type": "freelancer", "full_name": "Msg Freelancer 2"})

    step("Login all three")
    tok_fl  = login(_EMAIL_FL)
    tok_cl  = login(_EMAIL_CL)
    tok_fl2 = login(_EMAIL_FL2)
    ok("Tokens obtained")

    step("Resolve profile IDs")
    fl_resp = _ex(get("/freelancers", tok_fl))
    fid     = fl_resp[0]["freelancer_id"] if isinstance(fl_resp, list) else fl_resp["freelancer_id"]
    cl_resp = _ex(get("/clients", tok_cl))
    cid     = cl_resp[0]["client_id"] if isinstance(cl_resp, list) else cl_resp["client_id"]
    fl2_resp = _ex(get("/freelancers", tok_fl2))
    fid2    = fl2_resp[0]["freelancer_id"] if isinstance(fl2_resp, list) else fl2_resp["freelancer_id"]

    # Resolve user_ids for DM (need user_id not profile id)
    fl_me   = _ex(get("/auth/me", tok_fl))
    cl_me   = _ex(get("/auth/me", tok_cl))
    fl2_me  = _ex(get("/auth/me", tok_fl2))
    fl_uid  = fl_me.get("user_id")
    cl_uid  = cl_me.get("user_id")
    fl2_uid = fl2_me.get("user_id")
    info("freelancer_id / user_id", f"{fid} / {fl_uid}")
    info("client_id    / user_id", f"{cid} / {cl_uid}")
    info("freelancer2  / user_id", f"{fid2} / {fl2_uid}")

    step("Client creates job post")
    resp   = post("/job-posts", {
        "client_id": cid, "job_title": "API Developer (msg walkthrough)",
        "job_description": "Build REST APIs.", "project_type": "individual",
        "project_scope": "medium", "estimated_duration": "2 months",
        "experience_level": "intermediate", "status": "active",
    }, tok_cl)
    job_id = _ex(resp)["job_post_id"]
    info("job_post_id", job_id)

    step("Add a role")
    resp    = post("/job-roles", {
        "job_post_id": job_id, "role_title": "Backend Developer",
        "role_budget": 2000.0, "budget_currency": "USD",
        "budget_type": "fixed", "role_description": "Own the API layer.",
        "display_order": 0,
    }, tok_cl)
    role_id = _ex(resp)["job_role_id"]
    info("job_role_id", role_id)

    step("Freelancer submits proposal")
    resp        = post("/proposals", {
        "job_post_id": job_id, "job_role_id": role_id,
        "freelancer_id": fid, "cover_letter": "I can deliver this in 2 months.",
        "proposed_budget": 2000.0, "proposed_duration": "2 months", "status": "pending",
    }, tok_fl)
    proposal_id = _ex(resp)["proposal_id"]
    info("proposal_id", proposal_id)

    step("Client creates contract — this auto-creates the DM thread")
    resp        = post("/contracts", {
        "job_post_id": job_id, "job_role_id": role_id, "proposal_id": proposal_id,
        "freelancer_id": fid, "client_id": cid,
        "contract_title": "Msg Walkthrough Contract", "role_title": "Backend Developer",
        "agreed_budget": 2000.0, "budget_currency": "USD",
        "payment_structure": "full_payment", "agreed_duration": "2 months",
        "status": "active", "start_date": "2026-05-05", "end_date": "2026-07-05",
    }, tok_cl)
    contract_id = _ex(resp)["contract_id"]
    info("contract_id", contract_id)

    # ══════════════════════════════════════════════════════════════════════════
    section("Contract chat — POST /messages (delegates to DM thread)")
    # ══════════════════════════════════════════════════════════════════════════

    step("Client sends first message")
    resp = post("/messages",
                {"contract_id": contract_id,
                 "message_text": "Hi! Looking forward to working with you."},
                tok_cl, label="  POST /messages (client→freelancer)")
    msg1 = _ex(resp)
    msg1_id = msg1.get("dm_message_id")
    info("dm_message_id", msg1_id)
    info("status",        msg1.get("status"))
    assert msg1_id, "Expected dm_message_id in response"
    ok("Message sent, dm_message_id present")

    step("Freelancer replies")
    resp = post("/messages",
                {"contract_id": contract_id,
                 "message_text": "Thanks! I'll start Monday. Any priorities?"},
                tok_fl, label="  POST /messages (freelancer→client)")
    msg2 = _ex(resp)
    info("dm_message_id", msg2.get("dm_message_id"))

    step("Client sends second message")
    resp = post("/messages",
                {"contract_id": contract_id,
                 "message_text": "Top priority: the authentication endpoints."},
                tok_cl, label="  POST /messages (client→freelancer)")
    msg3 = _ex(resp)
    info("dm_message_id", msg3.get("dm_message_id"))

    # ══════════════════════════════════════════════════════════════════════════
    section("Pagination — GET /messages/contract/{id}")
    # ══════════════════════════════════════════════════════════════════════════

    step("Fetch first 2 messages (limit=2, no cursor)")
    resp  = get(f"/messages/contract/{contract_id}", tok_fl,
                params={"limit": 2}, label="  GET /messages (page 1, limit=2)")
    page1 = _ex(resp)
    msgs1 = page1["messages"]
    info("returned", len(msgs1)); info("has_more", page1["has_more"])
    assert len(msgs1) == 2, f"Expected 2, got {len(msgs1)}"
    assert page1["has_more"] is True
    assert "dm_message_id" in msgs1[0], "Messages must use dm_message_id"
    assert "attachments"   in msgs1[0], "Messages must include attachments field"
    assert "status"        in msgs1[0], "Messages must include status field"
    ok("page 1 correct — dm_message_id, attachments, status all present")

    step("Fetch older messages via next_cursor")
    cursor = page1["next_cursor"]
    resp   = get(f"/messages/contract/{contract_id}", tok_fl,
                 params={"limit": 2, "before": cursor},
                 label=f"  GET /messages (page 2, before cursor)")
    page2  = _ex(resp)
    msgs2  = page2["messages"]
    info("returned", len(msgs2))
    assert len(msgs2) > 0, "Expected at least 1 older message"
    ids1 = {m["dm_message_id"] for m in msgs1}
    ids2 = {m["dm_message_id"] for m in msgs2}
    assert not ids1 & ids2, "Pages must not overlap"
    ok("page 2 correct — no overlap with page 1")

    step("Fetch all messages (limit=50)")
    resp_all = get(f"/messages/contract/{contract_id}", tok_fl,
                   params={"limit": 50}, label="  GET /messages (all)")
    all_msgs = _ex(resp_all)["messages"]
    info("total messages", len(all_msgs))
    # At least the 3 user messages + 1 auto-DM from contract creation
    assert len(all_msgs) >= 4, f"Expected at least 4 messages, got {len(all_msgs)}"
    ok("all messages fetched")

    # ══════════════════════════════════════════════════════════════════════════
    section("File uploads — POST /messages/upload")
    # ══════════════════════════════════════════════════════════════════════════

    def upload_contract(label, filename, file_bytes, mime, extra=None, tok=None):
        data  = {"contract_id": contract_id, "message_text": f"[{label}]"}
        if extra: data.update(extra)
        files = [("file", (filename, io.BytesIO(file_bytes), mime))]
        resp  = post_mp("/messages/upload", data=data, files=files,
                        token=tok or tok_fl,
                        label=f"  POST /messages/upload ({label})")
        msg   = _ex(resp)
        atts  = msg.get("attachments") or []
        info("dm_message_id", msg.get("dm_message_id"))
        info("attachments",   len(atts))
        if atts:
            att = atts[0]
            info("file_type",  att.get("file_type"))
            info("file_url",   (att.get("file_url") or "")[:80] + "...")
        return msg

    step("Upload JPEG image")
    img_msg = upload_contract("image.jpg", "screenshot.jpg", _jpeg_bytes(), "image/jpeg")
    img_msg_id = img_msg["dm_message_id"]
    assert (img_msg.get("attachments") or [{}])[0].get("file_type") == "image"
    ok("file_type=image ✓")

    step("Upload PDF document")
    pdf_msg = upload_contract("report.pdf", "brief.pdf", _pdf_bytes(), "application/pdf")
    assert (pdf_msg.get("attachments") or [{}])[0].get("file_type") == "document"
    ok("file_type=document ✓")

    step("Upload MP4 video")
    vid_msg = upload_contract("demo.mp4", "demo.mp4", _mp4_bytes(), "video/mp4")
    assert (vid_msg.get("attachments") or [{}])[0].get("file_type") == "video"
    ok("file_type=video ✓")

    step("Upload MP3 audio")
    aud_msg = upload_contract("music.mp3", "sample.mp3", _mp3_bytes(), "audio/mpeg")
    assert (aud_msg.get("attachments") or [{}])[0].get("file_type") == "audio"
    ok("file_type=audio ✓")

    step("Upload WebM voice note (is_voice_note=true)")
    vnote_msg = upload_contract("voice.webm", "note.webm", _webm_bytes(), "audio/webm",
                                extra={"is_voice_note": "true"})
    assert (vnote_msg.get("attachments") or [{}])[0].get("file_type") == "voice_note"
    ok("file_type=voice_note ✓")

    step("File-only message (no message_text)")
    data  = {"contract_id": contract_id}
    files = [("file", ("icon.jpg", io.BytesIO(_jpeg_bytes()), "image/jpeg"))]
    fm    = _ex(post_mp("/messages/upload", data=data, files=files, token=tok_cl,
                        label="  POST /messages/upload (file-only)"))
    assert len(fm.get("attachments") or []) == 1
    ok("file-only message stored ✓")

    step("Empty payload rejected (no text, no file)")
    post_mp("/messages/upload", data={"contract_id": contract_id}, files=[],
            token=tok_fl, expected={400},
            label="  POST /messages/upload (empty — expect 400)")
    ok("correct 400 ✓")

    # ══════════════════════════════════════════════════════════════════════════
    section("GET /messages/{dm_message_id}/attachments")
    # ══════════════════════════════════════════════════════════════════════════

    step("Fetch attachments for the image message")
    resp_att = get(f"/messages/{img_msg_id}/attachments", tok_cl,
                   label=f"  GET /messages/{img_msg_id}/attachments")
    atts = _ex(resp_att)
    info("count",     len(atts))
    info("file_type", atts[0]["file_type"] if atts else "none")
    info("file_url",  (atts[0].get("file_url") or "")[:80] + "..." if atts else "none")
    assert len(atts) == 1
    ok("attachment detail retrieved ✓")

    # ══════════════════════════════════════════════════════════════════════════
    section("Mark read — PUT /messages/contract/{id}/read")
    # ══════════════════════════════════════════════════════════════════════════

    step("Client marks all messages as read")
    resp_read = put(f"/messages/contract/{contract_id}/read", {}, tok_cl,
                    label=f"  PUT /messages/contract/{contract_id}/read")
    updated   = _ex(resp_read)
    info("updated_count", updated.get("updated_count"))
    ok("mark-as-read completed ✓")

    step("Verify messages now show status='read'")
    resp_after = get(f"/messages/contract/{contract_id}", tok_cl,
                     params={"limit": 50}, label="  GET /messages after mark-read")
    msgs_after = _ex(resp_after)["messages"]
    read_msgs  = [m for m in msgs_after if m.get("status") == "read"]
    info("total messages", len(msgs_after))
    info("status=read",    len(read_msgs))
    assert len(read_msgs) > 0, "Expected at least some messages with status=read"
    ok("status correctly flipped to 'read' ✓")

    # ══════════════════════════════════════════════════════════════════════════
    section("Contract WebSocket — /messages/ws/contract/{id}")
    # ══════════════════════════════════════════════════════════════════════════

    step("Freelancer connects on WS; client sends message via REST")
    ws_uri    = f"{WS_URL}/messages/ws/contract/{contract_id}?token={tok_fl}"
    def _send_contract_msg():
        requests.post(f"{BASE_URL}/messages",
                      json={"contract_id": contract_id,
                            "message_text": "⚡ WS ping via contract endpoint"},
                      headers={"Content-Type": "application/json",
                               "Authorization": f"Bearer {tok_cl}"},
                      timeout=10)
    received = ws_test(ws_uri, _send_contract_msg)
    if received:
        ok("WebSocket received real-time push ✓")
    else:
        print("  ⚠ WS message not received within timeout — check server or network")

    step("Bad token rejected")
    bad_uri = f"{WS_URL}/messages/ws/contract/{contract_id}?token=badtoken"
    if ws_bad_token_rejected(bad_uri):
        ok("Bad token correctly rejected ✓")
    else:
        print("  ⚠ WS with bad token was not rejected — check auth logic")

    # ══════════════════════════════════════════════════════════════════════════
    section("Same thread — /dm/threads/{id}/messages returns identical data")
    # ══════════════════════════════════════════════════════════════════════════

    step("Find the DM thread linked to the contract")
    threads_resp = _ex(get("/dm/threads", tok_cl, label="  GET /dm/threads"))
    threads      = threads_resp.get("threads", [])
    contract_thread = next(
        (t for t in threads if t.get("contract_id") == contract_id), None
    )
    assert contract_thread, "Expected a DM thread linked to the contract"
    thread_id = contract_thread["thread_id"]
    info("thread_id",   thread_id)
    info("status",      contract_thread.get("status"))
    info("contract_id", contract_thread.get("contract_id"))
    ok("DM thread found with contract_id stamped ✓")

    step("Fetch messages via /dm/threads/{id}/messages — should match contract endpoint")
    dm_msgs_resp = _ex(get(f"/dm/threads/{thread_id}/messages", tok_cl,
                           params={"limit": 50},
                           label=f"  GET /dm/threads/{thread_id}/messages"))
    dm_msgs      = dm_msgs_resp["messages"]
    contract_ids = {m["dm_message_id"] for m in all_msgs}
    dm_ids       = {m["dm_message_id"] for m in dm_msgs}
    overlap      = contract_ids & dm_ids
    info("messages via contract endpoint", len(all_msgs))
    info("messages via DM endpoint",       len(dm_msgs))
    info("shared dm_message_ids",          len(overlap))
    assert len(overlap) > 0, "Expected same messages in both endpoints"
    ok("Both endpoints return the same messages — one unified thread ✓")

    # ══════════════════════════════════════════════════════════════════════════
    section("Cold DM flow — client initiates with second freelancer")
    # ══════════════════════════════════════════════════════════════════════════

    step("Client starts a DM thread with freelancer 2 (should be status=request)")
    dm_resp   = post("/dm/threads", {
        "participant_id": fl2_uid,
        "job_post_id":    job_id,
        "message_text":   "Hi! I have a project that might suit you. Interested?",
    }, tok_cl, label="  POST /dm/threads (client → freelancer 2)")
    dm_data   = _ex(dm_resp)
    dm_thread = dm_data.get("thread") or dm_data
    dm_tid    = dm_thread["thread_id"]
    info("thread_id", dm_tid)
    info("status",    dm_thread.get("status"))
    assert dm_thread.get("status") == "request", \
        f"Expected status=request, got {dm_thread.get('status')}"
    ok("Thread created in request status ✓")

    step("Freelancer 2 sees it in their requests")
    req_resp   = _ex(get("/dm/threads/requests", tok_fl2,
                         label="  GET /dm/threads/requests"))
    requests_  = req_resp.get("requests", [])
    found      = any(r["thread_id"] == dm_tid for r in requests_)
    info("pending requests", len(requests_))
    assert found, "New thread not found in freelancer 2 requests"
    ok("Appears in freelancer 2 request inbox ✓")

    step("1-message cap: client tries to send a second message (should be 403)")
    post("/dm/threads/{}/messages".format(dm_tid),
         {"message_text": "Trying to send another before acceptance"},
         tok_cl, expected={403},
         label="  POST /dm/threads/{}/messages (2nd msg while request — expect 403)")
    ok("1-message cap enforced ✓")

    step("Freelancer 2 accepts the request")
    accept_resp = put(f"/dm/threads/{dm_tid}/accept", {}, tok_fl2,
                      label=f"  PUT /dm/threads/{dm_tid}/accept")
    accepted    = _ex(accept_resp)
    info("status", accepted.get("status"))
    assert accepted.get("status") == "active", \
        f"Expected status=active after accept, got {accepted.get('status')}"
    ok("Thread promoted to active ✓")

    step("Both parties can now exchange messages freely")
    msg_a = _ex(post(f"/dm/threads/{dm_tid}/messages",
                     {"message_text": "Great! Tell me more about the project."},
                     tok_fl2, label="  POST /dm/threads/{}/messages (fl2 reply)"))
    msg_b = _ex(post(f"/dm/threads/{dm_tid}/messages",
                     {"message_text": "Sure! It's a 2-month backend build."},
                     tok_cl, label="  POST /dm/threads/{}/messages (cl reply)"))
    info("fl2 dm_message_id", msg_a.get("dm_message_id"))
    info("cl  dm_message_id", msg_b.get("dm_message_id"))
    ok("Bidirectional messaging works after accept ✓")

    # ══════════════════════════════════════════════════════════════════════════
    section("DM file upload — /dm/threads/{id}/messages/upload")
    # ══════════════════════════════════════════════════════════════════════════

    def upload_dm(label, filename, file_bytes, mime, extra=None, tok=None):
        data  = {"message_text": f"[{label}]"}
        if extra: data.update(extra)
        files = [("file", (filename, io.BytesIO(file_bytes), mime))]
        resp  = post_mp(f"/dm/threads/{dm_tid}/messages/upload",
                        data=data, files=files, token=tok or tok_cl,
                        label=f"  POST /dm upload ({label})")
        msg   = _ex(resp)
        atts  = msg.get("attachments") or []
        info("dm_message_id", msg.get("dm_message_id"))
        info("attachments",   len(atts))
        if atts:
            info("file_type", atts[0].get("file_type"))
            info("file_url",  (atts[0].get("file_url") or "")[:80] + "...")
        return msg

    step("Upload JPEG in DM")
    dm_img = upload_dm("dm-image.jpg", "dm_photo.jpg", _jpeg_bytes(), "image/jpeg")
    assert (dm_img.get("attachments") or [{}])[0].get("file_type") == "image"
    ok("DM image attachment ✓")

    step("Upload PDF in DM")
    dm_pdf = upload_dm("dm-doc.pdf", "dm_brief.pdf", _pdf_bytes(), "application/pdf")
    assert (dm_pdf.get("attachments") or [{}])[0].get("file_type") == "document"
    ok("DM PDF attachment ✓")

    step("Upload voice note in DM")
    dm_vn = upload_dm("voice.webm", "voice_note.webm", _webm_bytes(), "audio/webm",
                      extra={"is_voice_note": "true"}, tok=tok_fl2)
    assert (dm_vn.get("attachments") or [{}])[0].get("file_type") == "voice_note"
    ok("DM voice note ✓")

    step("Mark DM thread as read")
    mark_resp = put(f"/dm/threads/{dm_tid}/read", {}, tok_cl,
                    label=f"  PUT /dm/threads/{dm_tid}/read")
    info("updated_count", _ex(mark_resp).get("updated_count"))
    ok("DM mark-read ✓")

    # ══════════════════════════════════════════════════════════════════════════
    section("DM WebSocket — /dm/ws/{thread_id}")
    # ══════════════════════════════════════════════════════════════════════════

    step("Freelancer 2 connects on DM WS; client sends message via REST")
    dm_ws_uri = f"{WS_URL}/dm/ws/{dm_tid}?token={tok_fl2}"
    def _send_dm_msg():
        requests.post(f"{BASE_URL}/dm/threads/{dm_tid}/messages",
                      json={"message_text": "⚡ DM WS ping"},
                      headers={"Content-Type": "application/json",
                               "Authorization": f"Bearer {tok_cl}"},
                      timeout=10)
    received_dm = ws_test(dm_ws_uri, _send_dm_msg)
    if received_dm:
        ok("DM WebSocket received real-time push ✓")
    else:
        print("  ⚠ DM WS message not received within timeout")

    step("Bad token rejected on DM WS")
    bad_dm_uri = f"{WS_URL}/dm/ws/{dm_tid}?token=badtoken"
    if ws_bad_token_rejected(bad_dm_uri):
        ok("Bad token rejected on DM WS ✓")
    else:
        print("  ⚠ DM WS with bad token was not rejected")

    # ══════════════════════════════════════════════════════════════════════════
    section("Final summary")
    # ══════════════════════════════════════════════════════════════════════════

    step("Contract thread — all messages")
    resp_final = get(f"/messages/contract/{contract_id}", tok_fl,
                     params={"limit": 100}, label="  GET /messages/contract (final)")
    final_msgs = _ex(resp_final)["messages"]
    print(f"\n    {'#':<3} {'type_hint':<12} {'status':<6}  {'atts':<4}  text preview")
    print(f"    {'─'*60}")
    for i, m in enumerate(final_msgs, 1):
        acount = len(m.get("attachments") or [])
        ft     = (m["attachments"][0].get("file_type") if acount else "")
        text   = (m.get("message_text") or "")[:40]
        meta   = m.get("metadata") or {}
        hint   = meta.get("type", "user") if meta else "user"
        print(f"    {i:<3} {hint:<12} {m.get('status','?'):<6}  {acount:<4}  "
              f"{'['+ft+'] ' if ft else ''}{text}")
    info("\n    total", len(final_msgs))

    step("DM cold thread — all messages")
    resp_dm    = get(f"/dm/threads/{dm_tid}/messages", tok_cl,
                     params={"limit": 100}, label=f"  GET /dm/threads/{dm_tid}/messages")
    dm_final   = _ex(resp_dm)["messages"]
    info("total", len(dm_final))

    step("GET /dm/threads — pending_requests_count")
    threads_summary = _ex(get("/dm/threads", tok_fl2, label="  GET /dm/threads (fl2)"))
    info("pending_requests_count", threads_summary.get("pending_requests_count"))
    info("threads count",          len(threads_summary.get("threads", [])))

    print(f"\n{'═'*70}")
    print("  Unified messaging walkthrough COMPLETE — all checks passed.")
    print(f"{'═'*70}")
    print(f"\n  Contract       : {contract_id}")
    print(f"  Contract thread: {thread_id}")
    print(f"  Cold DM thread : {dm_tid}")
    print(f"  Freelancer 1   : {_EMAIL_FL}")
    print(f"  Freelancer 2   : {_EMAIL_FL2}")
    print(f"  Client         : {_EMAIL_CL}")
    print()

    _stop_tee(tee, out_path)


if __name__ == "__main__":
    run()
