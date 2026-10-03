from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from app.templates.account_confirmation import render_account_confirmation
from app.templates.password_reset import render_password_reset


class TemplateCode(str, Enum):
    ACCOUNT_CONFIRMATION = "ACCOUNT_CONFIRMATION"
    PASSWORD_RESET = "PASSWORD_RESET"


@dataclass(frozen=True)
class TemplateSpec:
    code: TemplateCode
    version: int
    required_variables: tuple[str, ...]
    render: Callable[[dict], tuple[str, str, str]]


def _render_account_confirmation(variables: dict) -> tuple[str, str, str]:
    return render_account_confirmation(
        confirm_url=str(variables["confirmUrl"]),
        ttl_hours=int(variables["ttlHours"]),
    )


def _render_password_reset(variables: dict) -> tuple[str, str, str]:
    return render_password_reset(
        reset_url=str(variables["resetUrl"]),
        ttl_minutes=int(variables["ttlMinutes"]),
    )


REGISTRY: dict[tuple[str, int], TemplateSpec] = {
    (TemplateCode.ACCOUNT_CONFIRMATION.value, 1): TemplateSpec(
        code=TemplateCode.ACCOUNT_CONFIRMATION,
        version=1,
        required_variables=("confirmUrl", "ttlHours"),
        render=_render_account_confirmation,
    ),
    (TemplateCode.PASSWORD_RESET.value, 1): TemplateSpec(
        code=TemplateCode.PASSWORD_RESET,
        version=1,
        required_variables=("resetUrl", "ttlMinutes"),
        render=_render_password_reset,
    ),
}


class TemplateContractError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def get_template(template_code: str, template_version: int) -> TemplateSpec:
    spec = REGISTRY.get((template_code, template_version))
    if spec is None:
        raise TemplateContractError("UNKNOWN_TEMPLATE", "unsupported mail template")
    return spec


def missing_variables(template_code: str, template_version: int, variables: dict) -> list[str]:
    spec = get_template(template_code, template_version)
    missing: list[str] = []
    for name in spec.required_variables:
        if name not in variables or variables[name] is None or variables[name] == "":
            missing.append(name)
    return missing
