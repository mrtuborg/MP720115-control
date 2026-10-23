"""
IEEE 488.2 definite-length arbitrary block data parsing/building, and
MP720115 ``:WAVeform:PREamble?`` parsing.

Format reference (source: UNI-T "UPO3000E&UPO2000E Series Oscilloscope
Programming Manual", page 6, section "Data Return", and reused throughout
the manual's :SYSTem:SETup?, :WAVeform:DATA? and :DISPlay:DATA? command
descriptions):

    #<n><len><payload>

  - ``#``   : block introducer byte.
  - ``<n>`` : exactly ONE ASCII digit giving the number of digits in
              ``<len>`` that follow (the "length of the length").
  - ``<len>``: ``<n>`` ASCII digits giving the exact byte count of
              ``<payload>``.
  - ``<payload>``: exactly that many raw bytes (may contain any byte
              value, including ``\\n``, NUL, etc. - it must NOT be
              treated as text or split on newlines).

Manual example (page 6): ``#3123`` + 123 bytes of data, where the digit
``3`` means the next 3 characters (``123``) encode the byte count.

This module intentionally contains *no* I/O. It only parses/builds bytes
already read from (or about to be written to) a transport, so it can be
fully unit tested without any instrument or mock resource.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


class IEEEBlockError(ValueError):
    """Raised when a byte string is not a well-formed IEEE 488.2 block."""


def build_ieee_block(payload: bytes) -> bytes:
    """Encode ``payload`` as an IEEE 488.2 definite-length block.

    Only used for completeness/tests - this library never needs to send
    binary blocks to the instrument for anything in its safe command set,
    but having an inverse of :func:`parse_ieee_block` is useful for
    round-trip testing.
    """
    length = len(payload)
    length_str = str(length)
    header = f"#{len(length_str)}{length_str}".encode("ascii")
    return header + payload


def parse_ieee_block(data: bytes) -> bytes:
    """Parse an IEEE 488.2 definite-length block and return the payload.

    ``data`` must start with the block (leading whitespace is not
    stripped automatically - callers that read raw SCPI responses should
    strip any instrument-added leading ``\\n``/``\\r`` themselves if
    needed, since those are not part of the block format itself).

    Any bytes in ``data`` *after* the declared payload length are
    ignored by this function and returned separately so callers can
    detect/validate trailing terminators (e.g. a single ``\\n``) without
    this function guessing instrument-specific framing.

    Raises
    ------
    IEEEBlockError
        If the header is malformed, truncated, or declares more bytes
        than are actually present in ``data``.
    """
    payload, _trailer = parse_ieee_block_with_trailer(data)
    return payload


def parse_ieee_block_with_trailer(data: bytes) -> Tuple[bytes, bytes]:
    """Like :func:`parse_ieee_block` but also returns the trailing bytes.

    Returns ``(payload, trailer)`` where ``trailer`` is whatever bytes in
    ``data`` follow the declared-length payload (typically empty, or a
    single ``\\n`` terminator appended by the instrument/transport).
    """
    if not isinstance(data, (bytes, bytearray)):
        raise IEEEBlockError(f"expected bytes, got {type(data).__name__}")
    data = bytes(data)

    if len(data) < 2:
        raise IEEEBlockError(f"block too short to contain a header: {data!r}")
    if data[0:1] != b"#":
        raise IEEEBlockError(
            f"not an IEEE 488.2 definite-length block (expected leading '#', "
            f"got {data[0:1]!r})"
        )

    n_digit = data[1:2]
    if not n_digit.isdigit():
        raise IEEEBlockError(
            f"invalid digit-count byte after '#': {n_digit!r} (must be 0-9)"
        )
    n = int(n_digit)
    if n == 0:
        # '#0' is the IEEE 488.2 INDEFINITE-length form (payload runs until
        # a terminator/EOI condition from the transport). This library only
        # ever expects DEFINITE-length blocks from the MP720115 (confirmed
        # by every manual example: :WAVeform:DATA?, :DISPlay:DATA?,
        # :SYSTem:SETup?), so indefinite blocks are explicitly rejected
        # rather than silently mishandled.
        raise IEEEBlockError(
            "indefinite-length block (#0...) is not supported; the "
            "MP720115 only documents definite-length (#<n><len><data>) "
            "blocks for :WAVeform:DATA?, :DISPlay:DATA? and :SYSTem:SETup?"
        )

    header_len = 2 + n
    if len(data) < header_len:
        raise IEEEBlockError(
            f"block header truncated: need {header_len} bytes, got {len(data)}"
        )

    length_field = data[2:header_len]
    if not length_field.isdigit():
        raise IEEEBlockError(f"invalid length field (not all digits): {length_field!r}")
    length = int(length_field)

    payload_start = header_len
    payload_end = payload_start + length
    if len(data) < payload_end:
        raise IEEEBlockError(
            f"declared payload length {length} exceeds available data "
            f"({len(data) - payload_start} bytes present after header)"
        )

    payload = data[payload_start:payload_end]
    trailer = data[payload_end:]
    return payload, trailer


def peek_declared_length(header_bytes: bytes) -> Optional[int]:
    """Given the first few bytes of a response, return the declared
    payload length once enough header bytes are available, else ``None``.

    This is used by streaming/chunked readers (see
    :mod:`mp72_control.controller`) to know how many more bytes to read
    after seeing only the first 2 bytes (``#`` + digit-count). Returns
    ``None`` if ``header_bytes`` does not yet contain the full header
    (caller should read more and retry), and raises
    :class:`IEEEBlockError` if the header is malformed.
    """
    if len(header_bytes) < 2:
        return None
    if header_bytes[0:1] != b"#":
        raise IEEEBlockError(
            f"not an IEEE 488.2 block (expected '#', got {header_bytes[0:1]!r})"
        )
    n_digit = header_bytes[1:2]
    if not n_digit.isdigit():
        raise IEEEBlockError(f"invalid digit-count byte: {n_digit!r}")
    n = int(n_digit)
    if n == 0:
        raise IEEEBlockError("indefinite-length block (#0...) is not supported")
    header_len = 2 + n
    if len(header_bytes) < header_len:
        return None
    length_field = header_bytes[2:header_len]
    if not length_field.isdigit():
        raise IEEEBlockError(f"invalid length field: {length_field!r}")
    return int(length_field)


def header_total_len(header_bytes: bytes) -> Optional[int]:
    """Return how many bytes the full ``#<n><len>`` header occupies, once
    known (``2 + n``), else ``None`` if fewer than 2 bytes are available.
    """
    if len(header_bytes) < 2:
        return None
    if header_bytes[0:1] != b"#":
        raise IEEEBlockError(
            f"not an IEEE 488.2 block (expected '#', got {header_bytes[0:1]!r})"
        )
    n_digit = header_bytes[1:2]
    if not n_digit.isdigit():
        raise IEEEBlockError(f"invalid digit-count byte: {n_digit!r}")
    return 2 + int(n_digit)


# ---------------------------------------------------------------------------
# :WAVeform:PREamble? parsing
# ---------------------------------------------------------------------------

#: Manual-documented field order for :WAVeform:PREamble? (page 67/68):
#: "Format,Type,Points,Count,Xinc,Xor,Xref,Yinc,Yor,Yref"
PREAMBLE_FIELDS = (
    "format",
    "type",
    "points",
    "count",
    "x_increment",
    "x_origin",
    "x_reference",
    "y_increment",
    "y_origin",
    "y_reference",
)

#: Format code -> name, per manual: "Format:BYTE (0), WORD (1), ASCII (2)"
PREAMBLE_FORMAT_CODES = {0: "BYTE", 1: "WORD", 2: "ASCII"}

#: Type code -> name, per manual:
#: "Type:NORMAL (0), PEAK (1), AVER (2), ENVelope (3), HRESolution (4)"
PREAMBLE_TYPE_CODES = {
    0: "NORMAL",
    1: "PEAK",
    2: "AVERAGE",
    3: "ENVELOPE",
    4: "HRESOLUTION",
}


@dataclass(frozen=True)
class Preamble:
    """Parsed :WAVeform:PREamble? response.

    All numeric fields are floats/ints as returned by the instrument;
    ``format_name``/``type_name`` are the manual-documented decode of the
    two leading integer codes.
    """

    format_code: int
    type_code: int
    points: int
    count: int
    x_increment: float
    x_origin: float
    x_reference: float
    y_increment: float
    y_origin: float
    y_reference: float

    @property
    def format_name(self) -> str:
        return PREAMBLE_FORMAT_CODES.get(self.format_code, f"UNKNOWN({self.format_code})")

    @property
    def type_name(self) -> str:
        return PREAMBLE_TYPE_CODES.get(self.type_code, f"UNKNOWN({self.type_code})")

    def code_to_volts(self, raw_code: float) -> float:
        """Convert one raw waveform code to volts using the manual's
        documented preamble semantics (page 67):

            Yinc: unit voltage of Y direction
            Yor : the relative position of Y direction and YREF zero position
            Yref: the reference value of Y direction (screen center point)

        Implemented formula for the tested MP720115 firmware.  A live
        offset sweep showed that increasing channel offset increases the
        returned ADC code by approximately offset/Yinc, so the display
        offset must be subtracted during conversion:

            volts = (raw_code - y_reference) * y_increment - y_origin

        The offset sign was checked live, but no calibrated external
        source was attached. Treat absolute voltages as provisional until
        cross-checked against a known input.
        """
        return (raw_code - self.y_reference) * self.y_increment - self.y_origin

    def code_to_time(self, index: int) -> float:
        """Convert a 0-based sample index to a time offset in seconds,
        using the manual-documented X fields:

            Xinc: time difference between two adjacent points
            Xor : time from trigger point to XREF
            Xref: reference time baseline

        Implemented formula (manual-documented semantics, same status
        caveat as :meth:`code_to_volts` regarding independent
        cross-check):

            t = (index - x_reference) * x_increment + x_origin
        """
        return (index - self.x_reference) * self.x_increment + self.x_origin


def parse_preamble(response: str) -> Preamble:
    """Parse a ``:WAVeform:PREamble?`` response string into a
    :class:`Preamble`.

    Manual example (page 67):
        ``1,0,0,1,8.000e-009,-6.000e-006,0,4.000e-002,0.000e000,100``
    (format=1/WORD, type=0/NORMAL, points=0, count=1, Xinc=8e-9,
    Xor=-6e-6, Xref=0, Yinc=4e-2, Yor=0, Yref=100)
    """
    parts = [p.strip() for p in response.strip().split(",")]
    if len(parts) != len(PREAMBLE_FIELDS):
        raise ValueError(
            f"expected {len(PREAMBLE_FIELDS)} comma-separated preamble fields "
            f"({', '.join(PREAMBLE_FIELDS)}), got {len(parts)}: {response!r}"
        )
    try:
        return Preamble(
            format_code=int(float(parts[0])),
            type_code=int(float(parts[1])),
            points=int(float(parts[2])),
            count=int(float(parts[3])),
            x_increment=float(parts[4]),
            x_origin=float(parts[5]),
            x_reference=float(parts[6]),
            y_increment=float(parts[7]),
            y_origin=float(parts[8]),
            y_reference=float(parts[9]),
        )
    except ValueError as exc:
        raise ValueError(f"malformed preamble field in {response!r}: {exc}") from exc
