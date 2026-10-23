"""
Dependency-free ONC-RPC/VXI-11 transport implementing the subset of the
PyVISA ``MessageBasedResource`` API used by :class:`mp72_control.controller
.MP720115`: ``write()``, ``query()``, ``read_raw()``, a settable ``timeout``
(milliseconds) and ``close()``.

It is the fallback for hosts where PyVISA cannot be installed. One link stays
open for the life of the connection. The firmware sets the VXI-11 END reason
bit on every device_read chunk, so once an IEEE 488.2 block header is seen the
reader trusts the declared length instead of END. See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import socket
import struct
import time
from typing import Optional

from .exceptions import TransportError
from .scpi_blocks import header_total_len, peek_declared_length

_CORE_PROG = 395183  # 0x0607AF - VXI-11 "Core" device_core RPC program
_CORE_VERS = 1
_PROC_CREATE_LINK = 10
_PROC_DEVICE_WRITE = 11
_PROC_DEVICE_READ = 12
_PROC_DESTROY_LINK = 23
_RECONNECT_SETTLE_SECONDS = 0.5

# VXI-11 device_read "reason" bitmask (VXI-11.1 spec, section device_read):
_REASON_REQCNT = 0x01  # requested byte count has been transferred
_REASON_CHR = 0x02     # a termination character was received
_REASON_END = 0x04     # END indicator was read (last of the message)


def _xdr_uint(v: int) -> bytes:
    return struct.pack(">I", v & 0xFFFFFFFF)


def _xdr_string(s: bytes) -> bytes:
    n = len(s)
    pad = (4 - n % 4) % 4
    return _xdr_uint(n) + s + b"\x00" * pad


class VXI11RawTransport:
    """Minimal PyVISA-resource-like adapter over a hand-rolled VXI-11 link.

    Parameters
    ----------
    host:
        Instrument IP address/hostname.
    device:
        VXI-11 logical device name (``inst0`` is standard/default).
    timeout:
        Initial I/O timeout in milliseconds (PyVISA convention).
    connect_timeout:
        TCP connect + portmapper + create_link timeout in seconds.
    """

    def __init__(
        self,
        host: str,
        device: str = "inst0",
        timeout: int = 5000,
        connect_timeout: float = 5.0,
    ):
        self.host = host
        self.device = device
        self.timeout = timeout  # milliseconds, PyVISA-style public attribute
        self._connect_timeout = connect_timeout
        self._xid = 1
        self._sock: Optional[socket.socket] = None
        self._lid: Optional[int] = None
        self._max_recv = 1024  # conservative default; updated after create_link

        self._open_link()

    def _open_link(self) -> None:
        port = self._get_core_port(self._connect_timeout)
        if port == 0:
            raise TransportError(
                f"VXI-11 core RPC program not registered via portmapper on {host}"
            )
        self._sock = socket.create_connection(
            (self.host, port), timeout=self._connect_timeout
        )
        self._create_link(self._connect_timeout)

    def reconnect(self) -> None:
        """Release a possibly poisoned link and create a fresh one.

        A TCP close alone is not sufficient on the tested instrument: the
        server retains the logical VXI-11 link for a while and eventually
        rejects ``create_link`` with device error 3. Always make a best-effort
        ``destroy_link`` call before replacing the socket.
        """
        self._close_link()
        # Firmware 5.00.0.19 may reject an immediate create_link with error 3
        # even after acknowledging destroy_link. Give its server a bounded
        # settling interval before opening the replacement logical link.
        time.sleep(_RECONNECT_SETTLE_SECONDS)
        self._open_link()

    def _close_link(self) -> None:
        """Best-effort VXI-11 destroy_link followed by an unconditional close."""
        if self._lid is not None and self._sock is not None:
            try:
                self._rpc_call(
                    self._sock, self._next_xid(), _CORE_PROG, _CORE_VERS,
                    _PROC_DESTROY_LINK, _xdr_uint(self._lid),
                )
                self._rpc_recv(self._sock)
            except Exception:
                pass
            finally:
                self._lid = None
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    # -- internal RPC plumbing -------------------------------------------------

    def _next_xid(self) -> int:
        self._xid += 1
        return self._xid

    def _get_core_port(self, connect_timeout: float) -> int:
        s = socket.create_connection((self.host, 111), timeout=connect_timeout)
        try:
            params = _xdr_uint(_CORE_PROG) + _xdr_uint(_CORE_VERS) + _xdr_uint(6) + _xdr_uint(0)
            self._rpc_call(s, self._next_xid(), 100000, 2, 3, params)
            result = self._rpc_recv(s)
            return struct.unpack(">I", result[0:4])[0]
        finally:
            s.close()

    @staticmethod
    def _rpc_call(sock: socket.socket, xid: int, prog: int, vers: int, proc: int, params: bytes) -> None:
        cred = _xdr_uint(0) + _xdr_uint(0)
        verf = _xdr_uint(0) + _xdr_uint(0)
        body = (
            _xdr_uint(xid) + _xdr_uint(0) + _xdr_uint(2)
            + _xdr_uint(prog) + _xdr_uint(vers) + _xdr_uint(proc)
            + cred + verf + params
        )
        frame = struct.pack(">I", 0x80000000 | len(body)) + body
        sock.sendall(frame)

    @staticmethod
    def _recv_exact(sock: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise TransportError("VXI-11 socket closed unexpectedly")
            buf += chunk
        return buf

    @classmethod
    def _rpc_recv(cls, sock: socket.socket) -> bytes:
        header = cls._recv_exact(sock, 4)
        marker = struct.unpack(">I", header)[0]
        length = marker & 0x7FFFFFFF
        last = bool(marker & 0x80000000)
        data = cls._recv_exact(sock, length)
        while not last:
            header = cls._recv_exact(sock, 4)
            marker = struct.unpack(">I", header)[0]
            length = marker & 0x7FFFFFFF
            last = bool(marker & 0x80000000)
            data += cls._recv_exact(sock, length)
        xid, mtype, reply_stat = struct.unpack(">III", data[0:12])
        off = 12
        verf_flavor, verf_len = struct.unpack(">II", data[off:off + 8])
        off += 8 + verf_len
        accept_stat = struct.unpack(">I", data[off:off + 4])[0]
        off += 4
        if reply_stat != 0 or accept_stat != 0:
            raise TransportError(f"RPC reply_stat={reply_stat} accept_stat={accept_stat}")
        return data[off:]

    def _create_link(self, connect_timeout: float) -> None:
        params = _xdr_uint(1) + _xdr_uint(0) + _xdr_uint(int(connect_timeout * 1000)) + _xdr_string(self.device.encode())
        self._rpc_call(self._sock, self._next_xid(), _CORE_PROG, _CORE_VERS, _PROC_CREATE_LINK, params)
        result = self._rpc_recv(self._sock)
        error, lid, abort_port, max_recv = struct.unpack(">IIII", result[0:16])
        if error != 0:
            raise TransportError(f"VXI-11 create_link failed with device error {error}")
        self._lid = lid
        self._max_recv = max_recv if max_recv > 0 else 1024

    # -- public PyVISA-like API -------------------------------------------------

    def write(self, command: str) -> int:
        """Write a SCPI command (terminator appended automatically)."""
        if self._lid is None:
            raise TransportError("transport is closed")
        data = command.encode("ascii") if not command.endswith("\n") else command.encode("ascii")
        if not data.endswith(b"\n"):
            data += b"\n"
        io_timeout_ms = max(int(self.timeout), 1000)
        payload = (
            _xdr_uint(self._lid) + _xdr_uint(io_timeout_ms) + _xdr_uint(io_timeout_ms)
            + _xdr_uint(0) + _xdr_string(data)
        )
        self._rpc_call(self._sock, self._next_xid(), _CORE_PROG, _CORE_VERS, _PROC_DEVICE_WRITE, payload)
        result = self._rpc_recv(self._sock)
        werr, wsize = struct.unpack(">II", result[0:8])
        if werr != 0:
            raise TransportError(f"VXI-11 device_write failed with device error {werr}")
        return wsize

    def _device_read_once(self, request_size: int, io_timeout_ms: int):
        payload = (
            _xdr_uint(self._lid) + _xdr_uint(request_size) + _xdr_uint(io_timeout_ms)
            + _xdr_uint(io_timeout_ms) + _xdr_uint(0) + _xdr_uint(0)
        )
        self._rpc_call(self._sock, self._next_xid(), _CORE_PROG, _CORE_VERS, _PROC_DEVICE_READ, payload)
        result = self._rpc_recv(self._sock)
        rerr, reason = struct.unpack(">II", result[0:8])
        dlen = struct.unpack(">I", result[8:12])[0]
        data = result[12:12 + dlen]
        if rerr != 0:
            raise TransportError(f"VXI-11 device_read failed with device error {rerr}")
        return reason, data

    def read_raw(self, max_total: int = 2_000_000) -> bytes:
        """Read one complete instrument response, transparently looping
        over multiple VXI-11 ``device_read`` calls.  The read request is
        conservatively sized to the link's advertised ``maxRecvSize``
        (1024 bytes here), although that field formally constrains writes,
        until the END reason bit is seen or an IEEE 488.2
        block's declared length has been fully consumed.

        LIVE FIRMWARE FINDING (tested unit, firmware 5.00.0.19):
        this instrument's VXI-11 stack sets the END reason bit (0x4) on
        EVERY ``device_read`` reply, including intermediate chunks of a
        multi-chunk IEEE 488.2 block response that is far longer than one
        ``maxRecvSize`` (1024-byte) chunk (observed directly: reading a
        ~1.15 MB ``:DISPlay:DATA?`` BMP returns ``reason=4`` already on
        the very first 1024-byte chunk). Therefore END is NOT a reliable
        "last chunk" signal for multi-chunk block transfers on this
        device and is used here only as a fallback for plain
        (non-block) ASCII responses. Once an IEEE 488.2 block header has
        been recognised, this loop keeps reading additional chunks until
        the declared payload length is fully satisfied, regardless of
        the reason flags on intermediate chunks.
        """
        if self._lid is None:
            raise TransportError("transport is closed")
        io_timeout_ms = max(int(self.timeout), 1000)
        buf = b""
        declared_total: Optional[int] = None  # header_len + payload length, once known
        while True:
            reason, chunk = self._device_read_once(self._max_recv, io_timeout_ms)
            buf += chunk
            if declared_total is None and buf[:1] == b"#":
                hlen = header_total_len(buf)
                if hlen is not None and len(buf) >= hlen:
                    plen = peek_declared_length(buf)
                    if plen is not None:
                        declared_total = hlen + plen
            if declared_total is not None:
                if len(buf) >= declared_total:
                    break
                # Known-length block still incomplete: ignore the (here
                # unreliable) END reason bit and keep reading more chunks.
            elif reason & _REASON_END:
                # Not a recognised block (or not enough bytes yet to tell) -
                # trust END for plain ASCII/short replies as usual.
                break
            if len(buf) >= max_total:
                raise TransportError(
                    f"read_raw exceeded max_total={max_total} bytes without END/length match"
                )
            if not chunk:
                # Defensive: avoid infinite loop on a misbehaving/stalled link,
                # whether or not we have identified a declared block length yet.
                raise TransportError(
                    "VXI-11 device_read returned no data before the response "
                    "was complete (declared_total="
                    f"{declared_total}, received={len(buf)} bytes)"
                )
        return buf

    def query(self, command: str) -> str:
        self.write(command)
        raw = self.read_raw()
        return raw.decode("ascii", errors="replace").strip()

    def close(self) -> None:
        self._close_link()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
