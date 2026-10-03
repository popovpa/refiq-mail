from __future__ import annotations

from botocore.exceptions import BotoCoreError, ClientError

from app.provider.errors import PermanentProviderError, RetryableProviderError

# Provider delivery is at-least-once. A timeout after Postbox accepts the message
# but before MessageId is stored can cause a later lease reclaim to send again.
# Classification here only decides whether the worker should retry.

_RETRYABLE_CODES = {
    "TooManyRequestsException",
    "Throttling",
    "ThrottlingException",
    "ServiceUnavailable",
    "ServiceUnavailableException",
    "InternalFailure",
    "InternalServerError",
    "RequestTimeout",
    "RequestTimeoutException",
    "LimitExceededException",
    "RequestExpired",
}
_RETRYABLE_BOTOCORE = {
    "ReadTimeoutError",
    "ConnectTimeoutError",
    "EndpointConnectionError",
    "ConnectionClosedError",
    "ConnectionError",
}
_PERMANENT_BOTOCORE = {"ParamValidationError", "ValidationError", "NoCredentialsError"}


def _status_code(exc: ClientError) -> int | None:
    status = (exc.response or {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status if isinstance(status, int) else None


def _error_code(exc: ClientError) -> str:
    error = (exc.response or {}).get("Error") or {}
    code = error.get("Code")
    return str(code or "PROVIDER_ERROR")


def _error_message(exc: ClientError) -> str:
    error = (exc.response or {}).get("Error") or {}
    return str(error.get("Message") or exc.__class__.__name__)


def classify_provider_error(exc: Exception) -> ProviderError:
    if isinstance(exc, ClientError):
        code = _error_code(exc)
        message = _error_message(exc)
        status = _status_code(exc)
        if code in _RETRYABLE_CODES or status == 429 or (status is not None and status >= 500):
            return RetryableProviderError(code, message)
        return PermanentProviderError(code, message)
    name = type(exc).__name__
    if name in _PERMANENT_BOTOCORE:
        return PermanentProviderError(name, str(exc) or name)
    if isinstance(exc, BotoCoreError) or name in _RETRYABLE_BOTOCORE or isinstance(exc, TimeoutError):
        return RetryableProviderError(name, str(exc) or name)
    if isinstance(exc, (ConnectionError, OSError)):
        return RetryableProviderError(name, str(exc) or name)
    return PermanentProviderError(name, str(exc) or name)
