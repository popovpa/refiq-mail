class ProviderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RetryableProviderError(ProviderError):
    """Temporary provider or network failure. The worker may retry."""


class PermanentProviderError(ProviderError):
    """The provider rejected the message. Retrying will not succeed."""


class ProviderNotConfigured(PermanentProviderError):
    def __init__(self, message: str = "mail provider is not configured") -> None:
        super().__init__("PROVIDER_NOT_CONFIGURED", message)
