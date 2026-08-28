"""
Logging configuration utilities
"""
import logging
import sys
import os


def get_logger(name: str, level=logging.DEBUG) -> logging.Logger:
    """
    Create logger with console handler
    
    Args:
        name: Logger name (usually __name__)
        level: Logging level (default: DEBUG)
    
    Returns:
        logging.Logger: Configured logger instance
    
    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("Starting validation")
    """
    logger = logging.getLogger(name)
    
    if logger.handlers:  # Already configured
        return logger
    
    logger.setLevel(level)
    
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(filename)s:%(lineno)d | %(message)s"
    )
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    
    logger.addHandler(console_handler)
    return logger


def add_file_handler(logger: logging.Logger, log_directory: str, log_filename: str) -> logging.Logger:
    """
    Add file handler to existing logger
    
    Args:
        logger: Logger instance
        log_directory: Directory path for log files
        log_filename: Log filename
    
    Returns:
        logging.Logger: Logger with file handler added
    
    Example:
        >>> logger = get_logger(__name__)
        >>> logger = add_file_handler(logger, '/logs', 'validation.log')
        >>> logger.info("This will go to both console and file")
    """
    os.makedirs(log_directory, exist_ok=True)
    
    log_file = os.path.join(log_directory, log_filename)
    
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(filename)s:%(lineno)d | %(message)s"
    )
    
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    return logger
