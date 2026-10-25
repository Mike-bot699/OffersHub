"""Application error hierarchy.

Every error carries a stable `code` so the UI can react to it, and a message that is
safe to show to the user (credentials are redacted before rendering).
"""


class AppError(Exception):
    status_code = 400
    code = "app_error"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ConfigurationError(AppError):
    status_code = 503
    code = "configuration_error"


class IntegrationError(AppError):
    """An external API failed or refused the request."""

    status_code = 502
    code = "integration_error"

    def __init__(self, message: str, *, retryable: bool = False, details: dict | None = None):
        super().__init__(message, details=details)
        self.retryable = retryable


class CapabilityNotSupported(AppError):
    """The connected platform or the granted permissions do not support an operation."""

    status_code = 422
    code = "capability_not_supported"


class AuthError(AppError):
    status_code = 401
    code = "unauthorized"
