"""Exception types for the mp72_control package."""


class MP720115Error(Exception):
    """Base class for all errors raised by this package."""


class ValidationError(MP720115Error):
    """Raised when a caller-supplied value is not in the documented
    allowed set for a given SCPI setter (fails fast, before any bytes
    are sent to the instrument)."""


class TransportError(MP720115Error):
    """Raised when the underlying VISA/VXI-11 transport fails (timeout,
    link error, connection refused, malformed response, etc.)."""


class UnsafeCommandError(MP720115Error):
    """Raised if code attempts to build one of the explicitly forbidden
    commands (should never happen via the public API; this is a
    defense-in-depth guard, not a reachable user-facing error path)."""


class VerificationError(MP720115Error):
    """Raised when an instrument readback does not match the requested
    setting (the command was sent but the instrument did not apply it)."""
