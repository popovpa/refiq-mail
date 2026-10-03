from app.templates.account_confirmation import CONFIRM_SUBJECT, render_account_confirmation
from app.templates.password_reset import RESET_SUBJECT, render_password_reset
from app.templates.registry import TemplateCode, get_template, missing_variables


def test_confirmation_email_matches_landing_style():
    url = "https://app.refiq.ru/confirm-account?token=exampletoken"
    subject, text, html = render_account_confirmation(confirm_url=url, ttl_hours=24)
    assert subject == CONFIRM_SUBJECT
    assert "Подтвердите аккаунт" in subject
    assert "Подтвердить аккаунт" in text
    assert "Подтвердить аккаунт" in html
    assert url in text
    assert url in html
    assert "24 часа" in text
    assert "<script" not in html.lower()
    assert "#f2f5ef" in html
    assert "#3d6b50" in html
    assert "#27503a" in html
    assert "border-radius:14px" in html
    assert "border-radius:999px" in html


def test_password_reset_email_matches_landing_style():
    url = "https://app.refiq.ru/reset-password?token=exampletoken"
    subject, text, html = render_password_reset(reset_url=url, ttl_minutes=30)
    assert subject == RESET_SUBJECT
    assert "Восстановление пароля" in subject
    assert url in text
    assert url in html
    assert "30 минут" in text
    assert "Восстановить пароль" in html
    assert "<script" not in html.lower()
    assert "#f2f5ef" in html
    assert "#3d6b50" in html
    assert "#27503a" in html
    assert "border-radius:14px" in html
    assert "border-radius:999px" in html


def test_template_escapes_markup_in_urls():
    url = 'https://app.refiq.ru/confirm-account?token=a<b>&c="d"'
    _subject, text, html = render_account_confirmation(confirm_url=url, ttl_hours=24)
    assert url in text
    assert url not in html
    assert "&lt;b&gt;" in html
    assert "&amp;" in html
    assert "&quot;" in html
    assert "<script" not in html.lower()


def test_registry_requires_current_variables():
    assert missing_variables(TemplateCode.ACCOUNT_CONFIRMATION.value, 1, {"ttlHours": 24}) == ["confirmUrl"]
    assert missing_variables(TemplateCode.PASSWORD_RESET.value, 1, {"resetUrl": "https://example.test"}) == [
        "ttlMinutes"
    ]
    spec = get_template(TemplateCode.ACCOUNT_CONFIRMATION.value, 1)
    assert spec.version == 1
    subject, text, html = spec.render({"confirmUrl": "https://example.test/c", "ttlHours": 24})
    assert "Подтвердите аккаунт" in subject
    assert "https://example.test/c" in text
    assert "https://example.test/c" in html
