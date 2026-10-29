"""VPN parser and relay tests: no real credentials, VPN, firewall or target."""
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from lib import htb


PROFILE = "client\ndev tun\nremote-cert-tls server\nremote example.invalid 1194\n"


class ProfileTests(unittest.TestCase):
    def test_inline_material_is_not_parsed_as_commands(self):
        htb.validate_profile(PROFILE + "<key>\nopaque ' secret\nup /not-a-command\n</key>\n")

    def test_scripts_includes_full_tunnels_and_external_keys_are_rejected(self):
        for directive in ("up /tmp/script", "plugin /tmp/plugin.so", "config other.ovpn",
                          "script-security 2", "redirect-gateway def1", "key /tmp/key",
                          "auth-user-pass /tmp/password", "management 127.0.0.1 1234"):
            with self.subTest(directive=directive), self.assertRaises(RuntimeError):
                htb.validate_profile(PROFILE + directive + "\n")

    def test_server_identity_verification_is_required(self):
        with self.assertRaises(RuntimeError):
            htb.validate_profile("client\ndev tun\n")

    def test_unterminated_inline_block_is_rejected(self):
        with self.assertRaises(RuntimeError):
            htb.validate_profile(PROFILE + "<key>\nopaque\n")

    def test_import_inside_repository_is_rejected_before_nmcli(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = root / "private.ovpn"
            profile.write_text(PROFILE)
            profile.chmod(0o600)
            with patch.object(htb, "ROOT", root), patch.object(htb, "output") as command:
                with self.assertRaises(RuntimeError):
                    htb.import_profile(str(profile))
                command.assert_not_called()

    def test_only_explicit_private_ipv4_targets_are_allowed(self):
        self.assertEqual(htb.target_ip("10.129.1.2"), "10.129.1.2")
        for address in ("127.0.0.1", "8.8.8.8", "0.0.0.0", "::1"):
            with self.subTest(address=address), self.assertRaises((RuntimeError, ValueError)):
                htb.target_ip(address)

    def test_non_vpn_route_is_rejected(self):
        def command(args):
            if args[0] == "nmcli":
                return "selected-uuid"
            if "address" in args:
                return json.dumps([{"ifname": "tun0", "addr_info": [{"local": "10.10.14.7"}]}])
            return json.dumps([{"dev": "physical0", "prefsrc": "192.168.1.5"}])

        def field(name, uuid):
            return "10.10.14.7/24" if name == "IP4.ADDRESS" else "yes"

        with patch.object(htb, "selected", return_value={"uuid": "selected-uuid", "configured": True}), \
                patch.object(htb, "output", side_effect=command), patch.object(htb, "nm_get", side_effect=field):
            with self.assertRaisesRegex(RuntimeError, "no atraviesa"):
                htb.vpn_context("10.129.1.2")


class RelayTests(unittest.TestCase):
    def test_binary_transparency_and_half_close(self):
        client, peer = socket.socketpair()
        errors = []
        script = "import os\nwhile b:=os.read(0,65536):\n v=memoryview(b)\n while v:v=v[os.write(1,v):]\n"

        def serve():
            try:
                htb.relay_connection(peer, [sys.executable, "-u", "-c", script])
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        payload = bytes(range(256)) * 400
        with client:
            client.settimeout(10)
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
            received = bytearray()
            while part := client.recv(65536):
                received.extend(part)
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(received, payload)


if __name__ == "__main__":
    unittest.main()
