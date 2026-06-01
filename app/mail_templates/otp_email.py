def get_otp_email_template(name: str, otp: str) -> str:
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      <title>Complete verification to Align Labs</title>
    </head>
    <body style="background:#f4f5f7;margin:0;padding:40px 0;font-family:Arial,sans-serif;">
      <div style="max-width:520px;margin:0 auto;background:#fff;border:1px solid #e1e3e8;">
        <div style="padding:36px 40px 10px;">
          <h1 style="margin:0;color:#050038;font-size:28px;">Complete verification to Align Labs</h1>
        </div>
        <div style="padding:10px 40px 40px;">
          <p style="color:#696871;font-size:16px;">Hi {name},</p>
          <p style="color:#696871;font-size:16px;">Please enter this confirmation code in the window where you started creating your account:</p>
          <div style="background:#f4f5f7;color:#6661D2;font-size:42px;font-weight:700;text-align:center;padding:35px 20px;margin:30px 0;letter-spacing:2px;">{otp}</div>
          <p style="color:#696871;font-size:14px;">This code expires in 1 hour.</p>
          <p style="color:#696871;font-size:16px;margin-bottom:0;">If you didn't create an account in Align Labs, please ignore this message.</p>
        </div>
      </div>
    </body>
    </html>
    """
