"""
MP720115 VXI-11 / SCPI controller package.

This package is a reusable, production-oriented client library for the
Multicomp Pro MP720115 digital oscilloscope. The unit identifies as UNI-T
and live tests confirm compatibility with the UPO3000E/UPO2000E programming
manual's SCPI dialect; the exact underlying OEM model remains unproven.

See ``README.md`` and ``docs/`` for usage, verified/unverified command
status, and the safety rules enforced throughout this package (no *RST,
no *CLS, no calibration, no storage deletion, no network setters, no AUTO
setup, no undocumented/service commands, no arbitrary raw SCPI from the CLI).
"""

from .controller import MP720115, MP720115Error, ValidationError, VerificationError
from .scpi_blocks import parse_ieee_block, build_ieee_block, parse_preamble

__all__ = [
    "MP720115",
    "MP720115Error",
    "ValidationError",
    "VerificationError",
    "parse_ieee_block",
    "build_ieee_block",
    "parse_preamble",
]
