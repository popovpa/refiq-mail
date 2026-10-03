from app.templates.layout import render_landing_email

RESET_SUBJECT = "Восстановление пароля — RefIQ"


def render_password_reset(*, reset_url: str, ttl_minutes: int) -> tuple[str, str, str]:
    return render_landing_email(
        subject=RESET_SUBJECT,
        heading="Восстановление пароля",
        intro="Мы получили запрос на восстановление пароля вашей учётной записи RefIQ.",
        cta_label="Восстановить пароль",
        cta_url=reset_url,
        footer=(
            f"Ссылка действует {ttl_minutes} минут и может быть использована только один раз. "
            "Если вы не запрашивали восстановление пароля, просто проигнорируйте это письмо."
        ),
    )
