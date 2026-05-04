import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from functions.logger import logger


def send_otp_email(to_email: str, otp_code: str) -> bool:
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USERNAME")
    smtp_pass = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("SMTP_FROM_EMAIL", smtp_user)
    otp_expire_minutes = os.getenv("EMAIL_OTP_EXPIRE_MINUTES", "10")

    if not smtp_user or not smtp_pass:
        logger("EMAIL", "SMTP credentials not configured — cannot send OTP", level="ERROR")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = from_email
        msg["To"] = to_email
        msg["Subject"] = "Your Email Verification Code"

        text_body = (
            f"Your verification code is: {otp_code}\n"
            f"This code expires in {otp_expire_minutes} minutes.\n"
            "If you did not create an account, please ignore this email."
        )
        html_body = f"""
        <html>
          <body style="font-family: Arial, sans-serif; color: #333;">
            <h2>Email Verification</h2>
            <p>Use the code below to complete your account setup:</p>
            <div style="font-size:32px; font-weight:bold; letter-spacing:8px; margin:24px 0; color:#1a1a1a;">
              {otp_code}
            </div>
            <p>This code expires in <strong>{otp_expire_minutes} minutes</strong>.</p>
            <p style="color:#888; font-size:12px;">
              If you did not create an account, please ignore this email.
            </p>
          </body>
        </html>
        """

        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, to_email, msg.as_string())

        logger("EMAIL", f"OTP email sent to {to_email}", level="INFO")
        return True

    except Exception as e:
        logger("EMAIL", f"Failed to send OTP email to {to_email}: {str(e)}", level="ERROR")
        return False
