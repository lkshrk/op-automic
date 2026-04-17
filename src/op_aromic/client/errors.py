"""API error types."""


class AutomicError(Exception):
    """Base error for Automic API calls."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthError(AutomicError):
    """Authentication or authorization failure."""


class NotFoundError(AutomicError):
    """Requested object does not exist."""


class ConflictError(AutomicError):
    """Object already exists or version conflict."""


class RateLimitError(AutomicError):
    """API rate limit exceeded."""


class FolderMissingError(AutomicError):
    """Target folder path does not exist and auto_create_folders is False."""
