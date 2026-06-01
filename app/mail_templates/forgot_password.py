from app.config import settings


def get_forgot_password_template(name: str, token: str) -> str:
    reset_link = f"{settings.ALIGNAUTH_APP_URL}/reset-password?token={token}"
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      <title>Reset Your Password</title>
    </head>
    <body style="background:#f4f4f7;margin:0;padding:40px 0;font-family:Arial,sans-serif;">
      <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;">
        <div style="background:#6661D2;color:white;text-align:center;padding:24px 20px;">
          <h1 style="margin:0;font-size:24px;">Reset Your Password</h1>
        </div>
        <div style="padding:30px 25px;color:#333;">
          <p>Hi <strong>{name}</strong>,</p>
          <p>We received a request to reset your password. Click the button below to set a new password:</p>
          <p style="text-align:center;">
            <a href="{reset_link}" style="display:inline-block;background:#6661D2;color:#fff;text-decoration:none;padding:12px 24px;border-radius:6px;">Reset Password</a>
          </p>
          <p>If the button does not work, copy and paste this link into your browser:</p>
          <p style="word-break:break-all;color:#6661D2;">{reset_link}</p>
          <p>If you did not request this, you can safely ignore this email.</p>
        </div>
      </div>
    </body>
    </html>
    """
