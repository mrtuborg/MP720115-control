"""Offline lifecycle tests for the dependency-free VXI-11 transport."""

import struct
import unittest
from unittest import mock

from mp72_control.vxi11_raw_transport import (
    VXI11RawTransport,
    _CORE_PROG,
    _CORE_VERS,
    _PROC_DESTROY_LINK,
)


class FakeSocket:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class VXI11Lifecycle(unittest.TestCase):
    def make_transport(self, recv_error=None):
        transport = object.__new__(VXI11RawTransport)
        transport._sock = FakeSocket()
        transport._lid = 123
        transport._xid = 10
        calls = []
        transport._rpc_call = lambda *args: calls.append(args)

        def recv(sock):
            if recv_error:
                raise recv_error
            return b""

        transport._rpc_recv = recv
        return transport, calls

    def test_reconnect_destroys_old_logical_link_before_opening_new_one(self):
        transport, calls = self.make_transport()
        old_socket = transport._sock
        opened = []
        transport._open_link = lambda: opened.append(True)
        with mock.patch("mp72_control.vxi11_raw_transport.time.sleep") as sleep:
            transport.reconnect()
        self.assertTrue(old_socket.closed)
        self.assertEqual((transport._sock, transport._lid), (None, None))
        self.assertEqual(opened, [True])
        self.assertEqual(calls[0][2:5], (_CORE_PROG, _CORE_VERS, _PROC_DESTROY_LINK))
        self.assertEqual(struct.unpack(">I", calls[0][5])[0], 123)
        sleep.assert_called_once()

    def test_close_still_releases_socket_when_destroy_reply_fails(self):
        transport, calls = self.make_transport(RuntimeError("broken reply"))
        old_socket = transport._sock
        transport.close()
        self.assertEqual(len(calls), 1)
        self.assertTrue(old_socket.closed)
        self.assertEqual((transport._sock, transport._lid), (None, None))


if __name__ == "__main__":
    unittest.main()
