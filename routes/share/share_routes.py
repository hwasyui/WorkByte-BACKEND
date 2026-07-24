import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from routes.job_posts.job_post_functions import JobPostFunctions
from routes.freelancers.freelancer_functions import FreelancerFunctions
from routes.clients.client_functions import ClientFunctions
from functions.logger import logger

share_router = APIRouter(tags=["Share"])

STORE_LINK = "https://drive.google.com/drive/folders/1WtWooe2u45O-3cfPm1RwS38NFQQLqxWv?usp=sharing"

_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700;900&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: 'Poppins', sans-serif;
      background: #F9F9F9;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }}

    .card {{
      background: #fff;
      border: 1px solid #F0F0F1;
      border-radius: 16px;
      padding: 36px 28px;
      max-width: 360px;
      width: 100%;
      text-align: center;
    }}

    .logo {{
      display: block;
      margin: 0 auto 28px;
      height: 36px;
      width: auto;
    }}

    .badge {{
      display: inline-block;
      background: #E0E7FF;
      color: #4F46E5;
      font-size: 11px;
      font-weight: 600;
      padding: 4px 12px;
      border-radius: 20px;
      margin-bottom: 16px;
    }}

    h1 {{
      font-size: 20px;
      font-weight: 700;
      color: #111827;
      margin-bottom: 12px;
      line-height: 1.3;
    }}

    p {{
      font-size: 13px;
      font-weight: 400;
      color: #7D7D7D;
      margin-bottom: 28px;
      line-height: 1.6;
    }}

    .btn {{
      display: block;
      width: 100%;
      padding: 14px 28px;
      background: linear-gradient(90deg, #4F46E5, #6366F1);
      color: #fff;
      border-radius: 30px;
      text-decoration: none;
      font-size: 15px;
      font-weight: 600;
      box-shadow: 0 6px 14px rgba(79, 70, 229, 0.25);
      transition: opacity 0.15s;
    }}

    .btn:hover {{ opacity: 0.9; }}

    .divider {{
      margin: 16px 0;
      font-size: 12px;
      color: #B6B5B5;
    }}

    .btn-secondary {{
      display: block;
      width: 100%;
      padding: 13px 28px;
      background: #fff;
      color: #4F46E5;
      border: 1.5px solid #E0E7FF;
      border-radius: 30px;
      text-decoration: none;
      font-size: 14px;
      font-weight: 600;
      transition: background 0.15s;
    }}

    .btn-secondary:hover {{ background: #F5F3FF; }}
  </style>
</head>
<body>
  <div class="card">
    <img class="logo" src="/assets/workbyte-logo.png" alt="WorkByte">

    <h1>{title}</h1>
    <div class="badge">{badge}</div>
    <p>· Available on WorkByte App · Tap below to open the app</p>

    <a class="btn" href="{deep_link}" id="open-btn">Open in App</a>
    <div class="divider">or</div>
    <a class="btn-secondary" href="{store_link}">Download WorkByte</a>
  </div>

  <script>
    window.location.href = "{deep_link}";
  </script>
</body>
</html>"""


def _html(title: str, subtitle: str, deep_link: str, badge: str = "WorkByte") -> str:
    return _HTML.format(
        title=title,
        subtitle=subtitle,
        store_link=STORE_LINK,
        deep_link=deep_link,
        badge=badge,
    )


# public web surface - opened by browsers / Android, not the Flutter app
@share_router.get("/share/job/{job_post_id}", response_class=HTMLResponse)
async def share_job_post(job_post_id: str):
    """Open the job post in the app; fall back to the app store page."""
    try:
        job = JobPostFunctions.get_job_post_by_id(job_post_id)
        if not job:
            title = "Job Post"
            subtitle = "View this job on WorkByte."
        else:
            title = job.get("job_title", "Job Post")
            subtitle = f"Posted on WorkByte · tap below to open the app"
    except Exception as e:
        logger("SHARE", f"Error fetching job {job_post_id}: {e}", level="WARNING")
        title = "Job Post"
        subtitle = "View this job on WorkByte."

    deep_link = f"workbyte://job/{job_post_id}"
    return HTMLResponse(_html(title, subtitle, deep_link, badge="Job Post"))


# public web surface - opened by browsers / Android, not the Flutter app
@share_router.get("/share/profile/{user_id}", response_class=HTMLResponse)
async def share_user_profile(user_id: str):
    """Open a freelancer or client profile in the app; fall back to the app store page."""
    display_name = None

    try:
        freelancer = FreelancerFunctions.get_freelancer_by_user_id(user_id)
        if freelancer:
            display_name = freelancer.get("full_name") or freelancer.get("name")
    except Exception:
        pass

    if not display_name:
        try:
            client = ClientFunctions.get_client_by_user_id(user_id)
            if client:
                display_name = client.get("full_name") or client.get("name") or client.get("company_name")
        except Exception:
            pass

    title = f"{display_name}'s Profile" if display_name else "User Profile"
    subtitle = "View this profile on WorkByte."
    deep_link = f"workbyte://profile/{user_id}"
    return HTMLResponse(_html(title, subtitle, deep_link, badge="Profile"))

_ASSET_LINKS = [
    {
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": "app.workbyte.com",
            "sha256_cert_fingerprints": [
                os.environ.get("ANDROID_SHA256_FINGERPRINT", "2D:61:8B:94:7E:5A:8A:62:D8:1A:B3:62:33:5E:75:F9:07:19:77:72:18:CF:CF:39:67:69:00:48:CC:16:2E:86")
            ],
        },
    }
]


# public web surface - opened by browsers / Android, not the Flutter app
@share_router.get("/.well-known/assetlinks.json")
async def asset_links():
    """Android App Links verification file."""
    return JSONResponse(content=_ASSET_LINKS)
