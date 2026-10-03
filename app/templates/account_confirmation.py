from app.templates.layout import render_landing_email

CONFIRM_SUBJECT = "Подтвердите аккаунт — RefIQ"


def render_account_confirmation(*, confirm_url: str, ttl_hours: int) -> tuple[str, str, str]:
    return render_landing_email(
        subject=CONFIRM_SUBJECT,
        heading="Подтверждение аккаунта",
        intro="Вы создали учётную запись RefIQ. Нажмите кнопку, чтобы подтвердить email и активировать аккаунт.",
        cta_label="Подтвердить аккаунт",
        cta_url=confirm_url,
        footer=(
            f"Ссылка действует {ttl_hours} часа и нужна, чтобы активировать аккаунт. "
            "Если вы не регистрировались в RefIQ, просто проигнорируйте это письмо."
        ),
    )
