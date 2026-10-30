"""Run Burp in Kali's virtual display without sharing Fedora's X11/Wayland sockets."""

import argparse
import fcntl
import os
from pathlib import Path
import secrets
import signal
import socket
import socketserver
import stat
import string
import subprocess
import sys
import tempfile
import threading
import time

if __package__:
    from .htb import relay_connection
else:
    from htb import relay_connection


ROOT = Path(__file__).resolve().parents[1]
PM = ["podman", "--remote=false"]
DISPLAY_NUMBER = 99
VNC_PORT = 5900 + DISPLAY_NUMBER


def guest(*args, **kwargs):
    return subprocess.run([*PM, "exec", "kali", *args], **kwargs)


def guest_alive(pidfile, executable):
    if not pidfile.is_file() or pidfile.is_symlink():
        return False
    try:
        pid = pidfile.read_text().strip()
    except OSError:
        return False
    if not pid.isdecimal():
        return False
    result = guest("bash", "-c",
                   'test "$(basename "$(readlink "/proc/$1/exe" 2>/dev/null)")" = "$2" '
                   '&& tr "\\000" "\\n" < "/proc/$1/cmdline" | grep -Fq -- "$3"',
                   "bash", pid, executable, pidfile.parent.name, capture_output=True)
    return result.returncode == 0


def stop_guest(pidfile, executable):
    if guest_alive(pidfile, executable):
        pid = pidfile.read_text().strip()
        guest("kill", "-TERM", pid, capture_output=True)
        for _ in range(30):
            if not guest_alive(pidfile, executable):
                return
            time.sleep(0.1)
        if guest_alive(pidfile, executable):
            guest("kill", "-KILL", pid, capture_output=True)


class Relay(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        print("A local VNC connection ended unexpectedly.", file=sys.stderr)


def make_handler(port):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            relay_connection(self.request, [*PM, "exec", "-i", "kali", "socat",
                                            "STDIO", f"TCP4:127.0.0.1:{port}"])

    return Handler


def ensure_requirements():
    if os.geteuid() == 0:
        raise RuntimeError("Run as your regular Fedora user, never with sudo.")
    result = subprocess.run([str(ROOT / "kali.sh"), "start"], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Kali failed its audit or cannot start; check ./kali.sh status.")
    for binary in ("burpsuite", "Xtightvnc", "vncpasswd", "socat", "ss", "java"):
        result = guest("sh", "-c", 'command -v "$1" >/dev/null', "sh", binary, capture_output=True)
        if result.returncode:
            raise RuntimeError(f"Missing {binary} inside Kali. Install it explicitly before using ./burp.sh.")
    if guest("test", "-e", f"/tmp/.X11-unix/X{DISPLAY_NUMBER}").returncode == 0:
        raise RuntimeError(f"Kali display :{DISPLAY_NUMBER} is already in use; nothing was changed.")


def start_display(directory, password):
    name = directory.name
    auth = directory / "passwd"
    encrypted = subprocess.run([*PM, "exec", "-i", "kali", "vncpasswd", "-f"],
                               input=(password + "\n").encode(), capture_output=True, check=True).stdout
    if len(encrypted) != 8:
        raise RuntimeError("VNC password generation did not return the expected credential.")
    with auth.open("xb") as output:
        output.write(encrypted)
    auth.chmod(0o600)
    guest_dir = f"/lab/{name}"
    result = subprocess.run([*PM, "exec", "-d", "kali", "sh", "-c",
                             'umask 077; printf "%s\n" "$$" > "$1"; '
                             'exec Xtightvnc :99 -localhost -rfbport 5999 '
                             '-rfbauth "$2" -geometry 1440x900 -depth 24 -ac',
                             "sh", f"{guest_dir}/vnc.pid", f"{guest_dir}/passwd"],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Could not start Kali's virtual display; no host display was shared.")
    for _ in range(40):
        if (directory / "vnc.pid").exists() and guest("test", "-S", "/tmp/.X11-unix/X99").returncode == 0:
            break
        time.sleep(0.15)
    if not guest_alive(directory / "vnc.pid", "Xtightvnc"):
        raise RuntimeError("Kali's virtual display did not start.")
    listeners = guest("ss", "-lnt", capture_output=True, text=True, check=True).stdout.splitlines()
    matching = [line for line in listeners if f":{VNC_PORT} " in line]
    if len(matching) != 1 or "127.0.0.1:" + str(VNC_PORT) not in matching[0]:
        raise RuntimeError("The Kali VNC listener is not restricted to loopback; refusing to expose the GUI.")


def check_relay(server):
    with socket.create_connection(server.server_address, timeout=8) as sock:
        sock.settimeout(8)
        if sock.recv(12) != b"RFB 003.008\n":
            raise RuntimeError("The local VNC relay did not reach Kali's display.")


def start_burp(directory):
    guest_dir = f"/lab/{directory.name}"
    log = directory / "burp.log"
    with log.open("xb") as output:
        proc = subprocess.Popen([*PM, "exec", "--env", "DISPLAY=:99", "kali",
                                 "sh", "-c", 'umask 077; printf "%s\n" "$$" > "$1"; '
                                 'exec java "-Dkali.burp.session=$2" -jar /usr/share/burpsuite/burpsuite.jar '
                                 '--suppress-jre-check --disable-check-for-updates-dialog',
                                 "sh", f"{guest_dir}/burp.pid", directory.name], stdin=subprocess.DEVNULL,
                                stdout=output, stderr=subprocess.STDOUT)
    for _ in range(40):
        if proc.poll() is not None:
            raise RuntimeError("Burp exited during startup: " + "\n".join(log.read_text().splitlines()[-15:]))
        if guest_alive(directory / "burp.pid", "java"):
            return proc
        time.sleep(0.15)
    raise RuntimeError("Burp did not start in Kali; check its local startup log.")


def main():
    parser = argparse.ArgumentParser(prog="./burp.sh", description="Run Burp inside Kali using a private virtual display and Fedora-local VNC viewer.")
    parser.add_argument("--check", action="store_true", help="test the isolated VNC bridge without starting Burp")
    args = parser.parse_args()
    os.umask(0o077)
    state = ROOT / ".state"
    lab = ROOT / "lab"
    if (not state.is_dir() or state.is_symlink() or state.stat().st_uid != os.getuid()
            or state.stat().st_mode & 0o777 != 0o700):
        raise RuntimeError("The private .state directory is missing or unsafe; run ./kali.sh setup first.")
    if (not lab.is_dir() or lab.is_symlink() or lab.stat().st_uid != os.getuid()
            or lab.stat().st_mode & 0o777 != 0o700):
        raise RuntimeError("The private lab directory is missing or unsafe; run ./kali.sh setup first.")
    lock_path = state / "burp-gui.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o777 != 0o600:
            raise RuntimeError("The existing GUI lock is not a private user-owned file.")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another Burp GUI session is already active.") from None
        ensure_requirements()
        password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(8))
        directory = Path(tempfile.mkdtemp(prefix=".burp-gui-", dir=lab))
        server = thread = proc = None
        try:
            start_display(directory, password)
            server = Relay(("127.0.0.1", 0), make_handler(VNC_PORT))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            check_relay(server)
            if args.check:
                print("Kali's virtual display and Fedora-local VNC relay work; no Burp session was left running.")
                return
            proc = start_burp(directory)
            print("Burp is running inside Kali on a virtual display (no Fedora X11/Wayland socket shared).", flush=True)
            print("In Fedora GNOME Connections, add VNC address: "
                  f"vnc://127.0.0.1:{server.server_address[1]}", flush=True)
            print(f"Temporary VNC password (8 characters): {password}", flush=True)
            print("Keep this terminal open. Close Burp or press Ctrl+C to end the session and remove its credentials.", flush=True)
            proc.wait()
            if proc.returncode:
                log = (directory / "burp.log").read_text().splitlines()
                raise RuntimeError("Burp exited with an error: " + "\n".join(log[-15:]))
        finally:
            stop_guest(directory / "burp.pid", "java")
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            if server:
                server.shutdown()
                server.server_close()
            if thread:
                thread.join(timeout=3)
            stop_guest(directory / "vnc.pid", "Xtightvnc")
            for name in ("passwd", "burp.log", "burp.pid", "vnc.pid"):
                path = directory / name
                if path.is_file() and not path.is_symlink():
                    path.unlink()
            directory.rmdir()


def interrupt(*_):
    raise KeyboardInterrupt


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupt)
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError, TimeoutError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nBurp GUI session closed; Kali and the Fedora VPN remain running.")
        sys.exit(130)
