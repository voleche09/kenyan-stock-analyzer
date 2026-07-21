"""
Centralized logging setup.
Provides a get_logger() factory that all modules use.
"""

import os
import sys
import logging
from rich.logging import RichHandler


def setup_logging(config=None):
    """
    Configure root logger with RichHandler (console) + FileHandler (file).

    Args:
        config: Config object with log_level, log_file, or None for defaults.
    """
    log_level = getattr(config, 'log_level', 'INFO') if config else 'INFO'
    log_file = getattr(config, 'log_file', './logs/analyzer.log') if config else './logs/analyzer.log'

    # Ensure log directory exists
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    # Root logger
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Remove any existing handlers to avoid duplicates
    root.handlers.clear()

    # Console handler with rich formatting
    console_handler = RichHandler(
        rich_tracebacks=True,
        show_time=True,
        show_path=False,
    )
    console_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    console_fmt = logging.Formatter('%(message)s')
    console_handler.setFormatter(console_fmt)
    root.addHandler(console_handler)

    # File handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(file_fmt)
    root.addHandler(file_handler)


def get_logger(name):
    """
    Get a logger for the given module name.

    Args:
        name: Usually __name__ from the calling module.

    Returns:
        logging.Logger instance.
    """
    return logging.getLogger(name)