import html


def render_landing_email(
    *,
    subject: str,
    heading: str,
    intro: str,
    cta_label: str,
    cta_url: str,
    footer: str,
    fallback_label: str = "Если кнопка не открывается, скопируйте ссылку в браузер:",
) -> tuple[str, str, str]:
    safe_url = html.escape(cta_url, quote=True)
    safe_heading = html.escape(heading)
    safe_intro = html.escape(intro)
    safe_cta = html.escape(cta_label)
    safe_footer = html.escape(footer)
    safe_fallback = html.escape(fallback_label)
    text = (
        f"{heading}\n\n"
        f"{intro}\n\n"
        f"{cta_label}:\n{cta_url}\n\n"
        f"{footer}\n"
    )
    html_body = f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{html.escape(subject)}</title>
</head>
<body style="margin:0;padding:0;background:#f2f5ef;font-family:system-ui,-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;color:#212823;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f2f5ef;padding:32px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:480px;background:#ffffff;border:1px solid #dbe4d8;border-radius:14px;box-shadow:0 12px 28px rgba(33,40,35,0.08);">
          <tr>
            <td style="padding:28px 28px 8px 28px;">
              <div style="font-size:22px;font-weight:750;letter-spacing:-0.03em;line-height:1.2;">
                Ref<span style="color:#3d6b50;">IQ</span>
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:8px 28px 0 28px;">
              <h1 style="margin:0;font-size:22px;line-height:1.3;letter-spacing:-0.02em;">{safe_heading}</h1>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 28px 0 28px;font-size:15px;line-height:1.55;color:#64756a;">
              {safe_intro}
            </td>
          </tr>
          <tr>
            <td style="padding:24px 28px 8px 28px;" align="center">
              <a href="{safe_url}" style="display:inline-block;background:#27503a;color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;letter-spacing:-0.01em;padding:12px 22px;border-radius:999px;box-shadow:0 6px 16px rgba(39,80,58,0.22);">
                {safe_cta}
              </a>
            </td>
          </tr>
          <tr>
            <td style="padding:12px 28px 0 28px;font-size:13px;line-height:1.5;color:#64756a;word-break:break-all;">
              {safe_fallback}<br />
              <a href="{safe_url}" style="color:#27503a;">{safe_url}</a>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 28px 28px 28px;font-size:13px;line-height:1.5;color:#64756a;">
              {safe_footer}
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
    return subject, text, html_body
