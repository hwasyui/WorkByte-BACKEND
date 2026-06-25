import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from functions.logger import logger


ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
BANNER_FILENAME = "Verify email UI.png"
LOGO_FILENAME = "Verify email UI (1).png"
BANNER_ASSET = os.path.join(ASSETS_DIR, BANNER_FILENAME)
LOGO_ASSET = os.path.join(ASSETS_DIR, LOGO_FILENAME)
_minio_public_base = os.getenv("MINIO_PUBLIC_BASE_URL", "http://localhost:9000").rstrip("/")
DEFAULT_BANNER_URL = f"{_minio_public_base}/email/banner.png"
DEFAULT_LOGO_URL = f"{_minio_public_base}/email/logo.png"


def _expiry_label(minutes: str) -> str:
    return f"{minutes} minute" if str(minutes) == "1" else f"{minutes} minutes"


def _asset_src(filename: str) -> str:
    direct_urls = {
        BANNER_FILENAME: os.getenv("EMAIL_BANNER_URL") or DEFAULT_BANNER_URL,
        LOGO_FILENAME: os.getenv("EMAIL_LOGO_URL") or DEFAULT_LOGO_URL,
    }
    if direct_urls.get(filename):
        return direct_urls[filename].strip()

    folder_url = os.getenv("EMAIL_ASSET_FOLDER_URL", "").strip()
    if folder_url:
        return f"{folder_url.rstrip('/')}/{quote(filename)}"

    base_url = (
        os.getenv("EMAIL_ASSET_BASE_URL")
        or os.getenv("PUBLIC_BACKEND_URL")
        or os.getenv("BACKEND_PUBLIC_URL")
        or ""
    ).strip()
    if not base_url:
        return ""
    return f"{base_url.rstrip('/')}/assets/{quote(filename)}"


def _build_otp_email(
    *,
    otp_code: str,
    otp_expire_minutes: str,
    title: str,
    banner_text: str,
    intro_text: str,
    code_label: str,
    ignore_text: str,
    banner_src: str,
    logo_src: str,
) -> tuple[str, str]:
    expiry = _expiry_label(otp_expire_minutes)
    current_year = datetime.utcnow().year
    text_body = (
        f"{title}\n\n"
        f"{intro_text}\n\n"
        f"{code_label}: {otp_code}\n"
        f"This code will expire in {expiry}.\n"
        "For your security, this code can only be used once.\n\n"
        f"{ignore_text}"
    )
    logo_html = (
        f'<img src="{logo_src}" alt="image" width="170" '
        'style="display:block; width:170px; max-width:50%; height:auto; border:0; margin:0 auto 16px;">'
        if logo_src
        else '<div style="font-size:26px; font-weight:800; color:#4134e8; margin-bottom:12px;">wb | workbyte</div>'
    )
    banner_logo_html = (
        f'<img src="{banner_src}" alt="image" width="260" '
        'style="display:block; width:260px; max-width:100%; height:auto; border:0; margin:0 0 0 auto;">'
        if banner_src
        else ""
    )
    html_body = f"""
    <!doctype html>
    <html>
      <body style="margin:0; padding:0; background:#f3f4f8; font-family:Arial, Helvetica, sans-serif; color:#111827;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f3f4f8; padding:24px 0;">
          <tr>
            <td align="center">
              <table role="presentation" width="640" cellspacing="0" cellpadding="0" style="width:640px; max-width:94%; background:#ffffff; border-radius:10px; overflow:hidden;">
                <tr>
                  <td style="padding:24px 24px 0;">
                    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#332bd6; border-radius:6px; overflow:hidden;">
                      <tr>
                        <td style="padding:44px 12px 42px 42px; width:54%; color:#ffffff; vertical-align:middle;">
                          <div style="font-size:30px; line-height:1.15; font-weight:800; margin:0 0 14px;">{title}</div>
                          <div style="font-size:13px; line-height:1.45; font-weight:700; max-width:330px;">{banner_text}</div>
                        </td>
                        <td style="padding:0 20px 0 0; width:46%; vertical-align:middle;">
                          {banner_logo_html}
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
                <tr>
                  <td style="padding:38px 38px 20px;">
                    <h1 style="margin:0 0 12px; font-size:28px; line-height:1.2; color:#111827;">Hello!</h1>
                    <p style="margin:0; font-size:14px; line-height:1.6; color:#111827;">{intro_text}</p>

                    <table role="presentation" width="450" cellspacing="0" cellpadding="0" align="center" style="width:450px; max-width:100%; margin:32px auto 26px; background:#f3f0ff; border:1px solid #ded8ff; border-radius:5px;">
                      <tr>
                        <td align="center" style="padding:22px 18px;">
                          <div style="font-size:12px; line-height:1; font-weight:800; color:#3128d4; text-transform:uppercase; margin-bottom:14px;">{code_label}</div>
                          <div style="font-size:46px; line-height:1; font-weight:800; letter-spacing:14px; color:#000000;">{otp_code}</div>
                        </td>
                      </tr>
                    </table>

                    <table role="presentation" width="560" cellspacing="0" cellpadding="0" align="center" style="width:560px; max-width:100%; margin:0 auto; background:#f3f0ff; border:1px solid #ded8ff; border-radius:5px;">
                      <tr>
                        <td style="padding:18px 20px; width:52px; vertical-align:middle;">
                          <div style="width:40px; height:40px; border-radius:20px; background:#4134e8; color:#ffffff; text-align:center; line-height:40px; font-size:22px; font-weight:700;">!</div>
                        </td>
                        <td style="padding:18px 20px 18px 0; vertical-align:middle;">
                          <div style="font-size:13px; font-weight:800; color:#111827; margin-bottom:5px;">This code will expire in {expiry}.</div>
                          <div style="font-size:12px; line-height:1.45; color:#111827;">For your security, this code can only be used once.</div>
                        </td>
                      </tr>
                    </table>

                    <p style="margin:30px 0 0; text-align:center; font-size:12px; line-height:1.5; color:#9ca3af;">{ignore_text}</p>
                  </td>
                </tr>
                <tr>
                  <td style="border-top:1px solid #e5e7eb; padding:34px 30px 34px; text-align:center;">
                    {logo_html}
                    <p style="margin:0 0 18px; font-size:13px; line-height:1.5; color:#9ca3af;">Making work simple, secure, and rewarding.</p>
                    <div style="margin:0 0 18px;">
                      <span style="display:inline-block; width:28px; height:28px; border-radius:14px; background:#ede9ff; color:#4134e8; line-height:28px; font-size:12px; font-weight:700; margin:0 4px;">in</span>
                      <span style="display:inline-block; width:28px; height:28px; border-radius:14px; background:#ede9ff; color:#4134e8; line-height:28px; font-size:12px; font-weight:700; margin:0 4px;">web</span>
                      <span style="display:inline-block; width:28px; height:28px; border-radius:14px; background:#ede9ff; color:#4134e8; line-height:28px; font-size:12px; font-weight:700; margin:0 4px;">ig</span>
                      <span style="display:inline-block; width:28px; height:28px; border-radius:14px; background:#ede9ff; color:#4134e8; line-height:28px; font-size:12px; font-weight:700; margin:0 4px;">@</span>
                    </div>
                    <p style="margin:0; font-size:12px; color:#9ca3af;">© {current_year} WorkByte. All rights reserved.</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </body>
    </html>.
    """
    return text_body, html_body


def _send_templated_otp_email(
    *,
    to_email: str,
    otp_code: str,
    subject: str,
    title: str,
    banner_text: str,
    intro_text: str,
    code_label: str,
    ignore_text: str,
    log_success: str,
    missing_credentials_log: str,
    failure_log: str,
) -> bool:
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USERNAME")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("SMTP_FROM_EMAIL", smtp_user)
    otp_expire_minutes = os.getenv("EMAIL_OTP_EXPIRE_MINUTES", "10")

    if not smtp_user or not smtp_pass:
        logger("EMAIL", missing_credentials_log, level="ERROR")
        return False

    try:
        banner_src = _asset_src(BANNER_FILENAME)
        logo_src = _asset_src(LOGO_FILENAME)

        msg = MIMEMultipart("alternative")
        msg["From"] = from_email
        msg["To"] = to_email
        msg["Subject"] = subject

        text_body, html_body = _build_otp_email(
            otp_code=otp_code,
            otp_expire_minutes=otp_expire_minutes,
            title=title,
            banner_text=banner_text,
            intro_text=intro_text,
            code_label=code_label,
            ignore_text=ignore_text,
            banner_src=banner_src,
            logo_src=logo_src,
        )
        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, to_email, msg.as_string())

        logger("EMAIL", f"{log_success} {to_email}", level="INFO")
        return True

    except Exception as e:
        logger("EMAIL", f"{failure_log} to {to_email}: {str(e)}", level="ERROR")
        return False


def send_otp_email(to_email: str, otp_code: str) -> bool:
    return _send_templated_otp_email(
        to_email=to_email,
        otp_code=otp_code,
        subject="Verify your WorkByte email",
        title="Verify Your Email",
        banner_text="Thanks for signing up. Complete your account setup by verifying your email address using the code below.",
        intro_text="Use the verification code below to complete your account setup.",
        code_label="Your verification code",
        ignore_text="If you didn't request this code, you can safely ignore this email.",
        log_success="OTP email sent to",
        missing_credentials_log="SMTP credentials not configured - cannot send OTP",
        failure_log="Failed to send OTP email",
    )


def send_password_reset_email(to_email: str, otp_code: str) -> bool:
    return _send_templated_otp_email(
        to_email=to_email,
        otp_code=otp_code,
        subject="Reset your WorkByte password",
        title="Reset Your Password",
        banner_text="We received a request to reset your password. Use the code below to continue securely.",
        intro_text="Use the password reset code below to set a new password for your account.",
        code_label="Your reset code",
        ignore_text="If you didn't request a password reset, you can safely ignore this email.",
        log_success="Password reset OTP sent to",
        missing_credentials_log="SMTP credentials not configured - cannot send password reset OTP",
        failure_log="Failed to send password reset email",
    )
