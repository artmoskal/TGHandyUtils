"""Centralized error messages for consistent UX."""


class ErrorMessages:
    """Centralized error messages to eliminate duplicate strings."""
    
    # Recipient errors
    RECIPIENT_NOT_FOUND = "❌ Recipient not found"
    RECIPIENT_DISABLED = "❌ {name} is disabled"
    NO_RECIPIENTS_CONFIGURED = "❌ No recipients configured. Please add accounts first."
    
    # Task creation errors
    TASK_CREATION_FAILED = "❌ Failed to create task in database."
    TASK_NOT_FOUND = "❌ Task {task_id} not found"
    TASK_ADD_FAILED = "❌ Failed to add to {recipient}"
    TASK_REMOVE_FAILED = "❌ Failed to remove from {recipient}"
    
    # Platform errors
    PLATFORM_CONNECTION_FAILED = "❌ Could not connect to {platform}"
    PLATFORM_TASK_CREATION_FAILED = "❌ Failed to create task on {platform}"
    PLATFORM_TASK_DELETION_FAILED = "❌ Failed to delete task from {platform}"
    
    # General errors
    OPERATION_FAILED = "❌ Operation failed"
    UNKNOWN_ERROR = "❌ An unknown error occurred"
    INVALID_INPUT = "❌ Invalid input provided"
    
    # Specific common patterns found in analysis
    REQUESTED_RECIPIENTS_NOT_FOUND = "❌ Requested recipients not found or disabled."
    ERROR_REMOVING_FROM_RECIPIENT = "❌ Error removing from {recipient}: {error}"
    
    @classmethod
    def format_recipient_disabled(cls, name: str) -> str:
        """Format recipient disabled message."""
        return cls.RECIPIENT_DISABLED.format(name=name)
    
    @classmethod
    def format_task_not_found(cls, task_id: int) -> str:
        """Format task not found message."""
        return cls.TASK_NOT_FOUND.format(task_id=task_id)
    
    @classmethod
    def format_platform_connection_failed(cls, platform: str) -> str:
        """Format platform connection failed message."""
        return cls.PLATFORM_CONNECTION_FAILED.format(platform=platform)
    
    @classmethod
    def format_task_add_failed(cls, recipient: str) -> str:
        """Format task add failed message."""
        return cls.TASK_ADD_FAILED.format(recipient=recipient)
    
    @classmethod
    def format_task_remove_failed(cls, recipient: str) -> str:
        """Format task remove failed message."""
        return cls.TASK_REMOVE_FAILED.format(recipient=recipient)
    
    @classmethod
    def format_error_removing_from_recipient(cls, recipient: str, error: str) -> str:
        """Format error removing from recipient message."""
        return cls.ERROR_REMOVING_FROM_RECIPIENT.format(recipient=recipient, error=error)