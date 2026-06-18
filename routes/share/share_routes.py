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
  <style>
    body {{ font-family: sans-serif; display: flex; flex-direction: column;
           align-items: center; justify-content: center; min-height: 100vh;
           margin: 0; background: #f5f5f5; color: #333; text-align: center; padding: 24px; }}
    h1 {{ font-size: 1.4rem; margin-bottom: 8px; }}
    p  {{ color: #666; margin-bottom: 24px; }}
    a  {{ display: inline-block; padding: 12px 28px; background: #2563eb;
          color: #fff; border-radius: 8px; text-decoration: none; font-weight: 600; }}
    a:hover {{ background: #1d4ed8; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p>{subtitle}</p>
  <a href="{store_link}">Download WorkByte App</a>
  <script>
    // Try to open the deep link immediately.
    window.location.href = "{deep_link}";
    // If the app isn't installed the browser will stay here; after 2.5 s we
    // give up silently (the download button is already visible).
  </script>
</body>
</html>"""


def _html(title: str, subtitle: str, deep_link: str) -> str:
    return _HTML.format(
        title=title,
        subtitle=subtitle,
        store_link=STORE_LINK,
        deep_link=deep_link,
    )


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
    return HTMLResponse(_html(title, subtitle, deep_link))


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
    return HTMLResponse(_html(title, subtitle, deep_link))

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


@share_router.get("/.well-known/assetlinks.json")
async def asset_links():
    """Android App Links verification file."""
    return JSONResponse(content=_ASSET_LINKS)
