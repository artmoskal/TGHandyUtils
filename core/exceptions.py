"""Custom exceptions for the application."""

class TGHandyUtilsException(Exception):
    """Base exception for TGHandyUtils application."""
    pass

class ConfigurationError(TGHandyUtilsException):
    """Raised when configuration is invalid."""
    pass

class DatabaseError(TGHandyUtilsException):
    """Raised when database operations fail."""
    pass

class PlatformError(TGHandyUtilsException):
    """Raised when platform operations fail."""
    pass

class ValidationError(TGHandyUtilsException):
    """Raised when validation fails."""
    pass

class TaskCreationError(PlatformError):
    """Raised when task creation fails."""
    pass

class TranscriptionError(TGHandyUtilsException):
    """Raised when voice transcription fails."""
    pass

class ParsingError(TGHandyUtilsException):
    """Raised when content parsing fails."""
    pass