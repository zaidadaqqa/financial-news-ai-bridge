class BridgeException(Exception):
    """Base exception for the Financial News AI Bridge."""

    pass


class DiscordConnectionError(BridgeException):
    """Raised when Discord fails to connect or authenticate."""

    pass


class TelegramPublishError(BridgeException):
    """Raised when a Telegram publishing operation fails."""

    pass


class AIResponseError(BridgeException):
    """Raised when the AI Provider returns an error."""

    pass


class ValidationError(BridgeException):
    """Raised when JSON or Number validation fails."""

    pass


class ConfigurationError(BridgeException):
    """Raised when environment variables are missing or invalid."""

    pass


class DatabaseError(BridgeException):
    """Raised when a database operation fails."""

    pass


class RetryableError(BridgeException):
    """Raised for errors that should be retried (e.g. 429, Timeout)."""

    pass


class FatalError(BridgeException):
    """Raised for non-recoverable errors."""

    pass
