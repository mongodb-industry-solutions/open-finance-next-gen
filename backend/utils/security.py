"""Security utilities for safe logging and input handling."""


def sanitize_log_input(value) -> str:
    """
    Sanitize user input for safe logging by removing/escaping control characters.
    
    Prevents log injection attacks where malicious input containing newlines
    or carriage returns could forge log entries or inject malicious content.
    
    Args:
        value: The value to sanitize (will be converted to string if not already)
        
    Returns:
        Sanitized string safe for logging
    """
    if value is None:
        return "None"
    if not isinstance(value, str):
        value = str(value)
    # Replace newlines and carriage returns with escaped representations
    return value.replace('\r', '\\r').replace('\n', '\\n')

