"""Backward-compatible import; implementation lives in focused services/."""
from .services.application import ContextApplication as Store
from .services.errors import Conflict, NotFound
