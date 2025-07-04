"""Modern modular handler system - replaces monolithic handlers.py."""

# Only import what actually exists in the transition
from .commands import main_commands

# Export all handler registration functions
__all__ = [
    'main_commands'
]