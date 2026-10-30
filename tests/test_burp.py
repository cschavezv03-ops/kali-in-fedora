"""GUI relay safety checks; no real VNC server, display or Podman engine."""

from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lib import burp


class BurpGuiTests(unittest.TestCase):
    def test_help_does_not_touch_podman(self):
        result = subprocess.run([str(burp.ROOT / "burp.sh"), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--check", result.stdout)
        self.assertIn("--proxy", result.stdout)

    def test_check_and_proxy_cannot_be_combined(self):
        result = subprocess.run([str(burp.ROOT / "burp.sh"), "--check", "--proxy"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--check only tests the GUI", result.stderr)

    def test_pid_file_symlinks_and_invalid_pids_cannot_be_used_for_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(burp, "guest") as guest:
            path = Path(tmp) / "burp.pid"
            path.symlink_to(Path(tmp) / "other")
            self.assertFalse(burp.guest_alive(path, "java"))
            path.unlink()
            path.write_text("not a pid\n")
            self.assertFalse(burp.guest_alive(path, "java"))
            guest.assert_not_called()

    def test_cleanup_never_kills_a_process_without_verifying_executable(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(burp, "guest_alive", return_value=False), \
                patch.object(burp, "guest") as guest:
            burp.stop_guest(Path(tmp) / "vnc.pid", "Xtightvnc")
            guest.assert_not_called()

    def test_relays_connect_only_to_kali_loopback(self):
        client, peer = socket.socketpair()
        try:
            for port in (5999, 8080):
                with patch.object(burp, "relay_connection") as relay:
                    burp.make_handler(port)(peer, ("127.0.0.1", 1), object())
                    command = relay.call_args.args[1]
                    self.assertEqual(command, ["podman", "--remote=false", "exec", "-i", "kali",
                                               "socat", "STDIO", f"TCP4:127.0.0.1:{port}"])
        finally:
            client.close()
            peer.close()


if __name__ == "__main__":
    unittest.main()
