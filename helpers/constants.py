"""HTTP and retry constants for consistent behavior."""


class HttpConstants:
    """Centralized HTTP timeout and retry constants."""
    
    # Timeout values
    HTTP_TIMEOUT = 30
    
    # Retry configuration
    MAX_RETRIES = 3
    BACKOFF_FACTOR = 2.0
    
    # Connection timeouts
    CONNECTION_TIMEOUT = 10
    READ_TIMEOUT = 30
    
    # Rate limiting
    RATE_LIMIT_DELAY = 1.0
    MAX_RATE_LIMIT_RETRIES = 5