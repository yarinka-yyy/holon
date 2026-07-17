"""Secret-free, Python 3.11-compatible Guard IPC boundary."""

from .client import PipeClient, PipeGuardClient, PipeProtocolError, PipeUnavailable
from .codec import IPC_VERSION, MAX_MESSAGE_BYTES, PIPE_NAME
from .model import PROTECTED_STATES, GuardAvailability, GuardHealth, GuardState

__all__ = [
    "IPC_VERSION",
    "MAX_MESSAGE_BYTES",
    "PIPE_NAME",
    "PROTECTED_STATES",
    "GuardAvailability",
    "GuardHealth",
    "GuardState",
    "PipeClient",
    "PipeGuardClient",
    "PipeProtocolError",
    "PipeUnavailable",
]
