"""Host VPN helpers. Never run as root or import credentials into Kali/Git."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".state"
META = STATE / "htb-vpn.json"
PM = ["podman", "--remote=false"]


def fail(message):
    raise RuntimeError(message)


def output(args):
    p = subprocess.run(args, capture_output=True, text=True, env=dict(os.environ, LC_ALL="C"))
    if p.returncode:
        fail(f"{args[0]} failed (exit {p.returncode}); check local status. Secrets are not displayed.")
    return p.stdout.strip()


def private_dir(path):
    if path.is_symlink():
        fail("Private directories cannot be symbolic links.")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    s = path.stat()
    if s.st_uid != os.getuid() or s.st_mode & 0o777 != 0o700:
        fail("The existing private directory must belong to this user and have mode 0700.")


def save_meta(data):
    private_dir(STATE)
    if META.is_symlink():
        fail("The state file cannot be a symbolic link.")
    with META.open("w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def nm_get(field, uuid):
    return output(["nmcli", "--colors", "no", "-g", field, "connection", "show", "uuid", uuid])


def selected():
    if not META.is_file() or META.is_symlink():
        fail("No project VPN profile has been imported. Run ./htb.sh import /private/path/htb.ovpn.")
    data = json.loads(META.read_text())
    uuid = data.get("uuid", "")
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", uuid):
        fail("Invalid profile UUID.")
    if nm_get("connection.type", uuid) != "vpn" or nm_get("vpn.service-type", uuid) != "org.freedesktop.NetworkManager.openvpn":
        fail("The saved profile is not an OpenVPN profile.")
    if nm_get("connection.id", uuid) != data.get("name"):
        fail("The profile was replaced or renamed; it will not be modified.")
    return data


def validate_profile(text):
    """Parse directives without printing inline certificates or private keys."""
    block = None
    directives = {}
    forbidden = {"config", "plugin", "up", "down", "route-up", "route-pre-down",
                 "ipchange", "tls-verify", "client-connect", "client-disconnect",
                 "learn-address", "daemon", "log", "log-append", "status", "writepid",
                 "cd", "chroot", "redirect-gateway", "redirect-private"}
    for line in text.splitlines():
        line = line.strip()
        tag = re.fullmatch(r"<(/?)([\w-]+)>", line)
        if block:
            if tag and tag[1] == "/" and tag[2] == block:
                block = None
            continue
        if tag:
            if tag[1]:
                fail("Invalid inline block in the profile.")
            if tag[2] not in {"ca", "cert", "key", "tls-auth", "tls-crypt", "tls-crypt-v2"}:
                fail("The profile contains a disallowed inline block.")
            block = tag[2]
            continue
        words = shlex.split(line, comments=True)
        if not words:
            continue
        key, values = words[0].removeprefix("--"), words[1:]
        if key in forbidden or key.startswith("management"):
            fail("Scripts, external management and full-tunnel directives are not allowed.")
        if key == "script-security" and values != ["1"]:
            fail("External scripts are not allowed in the profile.")
        if key in {"ca", "cert", "key", "tls-auth", "tls-crypt", "tls-crypt-v2"} and (not values or values[0] != "[inline]"):
            fail("Use a self-contained HTB profile without external key or certificate paths.")
        if key == "auth-user-pass" and values:
            fail("External password files are not supported; use the NetworkManager prompt.")
        directives.setdefault(key, []).append(values)
    if block:
        fail("Unclosed inline block.")
    if "client" not in directives or ["tun"] not in directives.get("dev", []):
        fail("An HTB client profile with dev tun is required.")
    if ["server"] not in directives.get("remote-cert-tls", []):
        fail("remote-cert-tls server is required; server verification must stay enabled.")


def import_profile(filename):
    source = Path(filename).expanduser()
    if source.is_symlink() or not source.is_file():
        fail("Select a regular profile file, not a symbolic link.")
    source = source.resolve()
    if source.is_relative_to(ROOT):
        fail("Keep the profile outside the repository and lab/.")
    s = source.stat()
    if s.st_uid != os.getuid() or s.st_mode & 0o077:
        fail("The profile must belong to this user and have private permissions (chmod 600).")
    raw = source.read_bytes()
    validate_profile(raw.decode("utf-8-sig"))
    digest = hashlib.sha256(raw).hexdigest()
    name = "kali-in-fedora-htb-" + digest[:12]
    if META.exists():
        data = selected()
        if data.get("sha256") != digest:
            fail("A different project profile already exists; it will not be replaced or deleted automatically.")
        uuid = data["uuid"]
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kali-in-fedora"
        if base.resolve().is_relative_to(ROOT):
            fail("Private configuration must be outside the repository.")
        private_dir(base)
        private_dir(base / "vpn")
        target = base / "vpn" / (name + ".ovpn")
        if target.exists() or target.is_symlink():
            if target.is_symlink() or target.stat().st_mode & 0o077 or target.read_bytes() != raw:
                fail("The existing private copy differs; it will not be overwritten.")
        else:
            with target.open("xb") as f:
                f.write(raw)
        imported = output(["nmcli", "--colors", "no", "connection", "import", "type", "openvpn", "file", str(target)])
        found = re.search(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", imported)
        if not found:
            fail("Could not identify the imported profile; check NetworkManager before retrying.")
        uuid = found[0]
        # Save recovery metadata before any further mutation; import does not activate.
        actual_name = nm_get("connection.id", uuid)
        save_meta({"uuid": uuid, "name": actual_name, "sha256": digest, "configured": False})
    user = __import__("pwd").getpwuid(os.getuid()).pw_name
    output(["nmcli", "connection", "modify", "uuid", uuid,
            "connection.id", name, "connection.autoconnect", "no",
            "connection.permissions", "user:" + user,
            "ipv4.never-default", "yes", "ipv6.never-default", "yes",
            "ipv4.ignore-auto-dns", "yes", "ipv6.ignore-auto-dns", "yes"])
    save_meta({"uuid": uuid, "name": name, "sha256": digest, "configured": True})
    print("Private profile imported but not activated; no autoconnect or default route. Keep credentials out of Git and Kali.")


def target_ip(value):
    addr = ipaddress.IPv4Address(value)
    if not any(addr in ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
        fail("Specify a private IPv4 address of your authorized HTB target.")
    return str(addr)


def port(value):
    number = int(value)
    if not 1024 <= number <= 65535:
        fail("The rootless listener needs a port between 1024 and 65535.")
    return number


def vpn_context(target):
    data = selected()
    if not data.get("configured"):
        fail("Import did not finish; retry with the same profile.")
    uuid = data["uuid"]
    active = output(["nmcli", "-t", "-f", "UUID", "connection", "show", "--active"]).splitlines()
    if uuid not in active:
        fail("The project VPN is not active. Run ./htb.sh up.")
    for field in ("ipv4.never-default", "ipv6.never-default"):
        if nm_get(field, uuid) != "yes":
            fail("The profile no longer has the expected split-tunnel settings.")
    addrs = {str(ipaddress.ip_interface(s).ip) for s in nm_get("IP4.ADDRESS", uuid).splitlines() if s}
    links = json.loads(output(["ip", "-j", "address", "show"]))
    interfaces = {x["ifname"] for x in links if any(a.get("local") in addrs for a in x.get("addr_info", []))}
    route = json.loads(output(["ip", "-j", "route", "get", target]))[0]
    if route.get("dev") not in interfaces:
        fail("The Fedora route to the target does not use this VPN; scan/listener will not start.")
    source = route.get("prefsrc")
    if source not in addrs:
        fail("The source IP does not match the address assigned by this VPN.")
    return source, route["dev"]


def managed():
    p = subprocess.run([str(ROOT / "kali.sh"), "start"], capture_output=True, text=True)
    if p.returncode:
        fail("Kali failed its audit or cannot start; check ./kali.sh status.")


def probe(target, tcp_port):
    script = "import socket,sys; s=socket.create_connection((sys.argv[1],int(sys.argv[2])),10);s.close()"
    host = subprocess.run([sys.executable, "-c", script, target, str(tcp_port)], capture_output=True)
    guest = subprocess.run(PM + ["exec", "kali", "python3", "-c", script, target, str(tcp_port)], capture_output=True)
    print("TCP from Fedora:", "OK" if host.returncode == 0 else "not connected")
    print("TCP from Kali:", "OK" if guest.returncode == 0 else "not connected")
    if host.returncode or guest.returncode:
        fail("The open port could not be verified on both sides. Check the port, VPN and routes before using the lab.")


def relay_connection(peer, command):
    """Binary transparent TCP <-> fixed process stdio, including half-close."""
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def incoming():
        try:
            while chunk := peer.recv(65536):
                child.stdin.write(chunk)
                child.stdin.flush()
        except (OSError, ValueError):
            pass
        finally:
            try:
                child.stdin.close()
            except OSError:
                pass

    thread = threading.Thread(target=incoming, daemon=True)
    thread.start()
    try:
        while chunk := os.read(child.stdout.fileno(), 65536):
            peer.sendall(chunk)
        if child.wait(timeout=5):
            fail("The relay exited with an error; check the listener inside Kali.")
    finally:
        try:
            peer.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        peer.close()
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        thread.join(timeout=2)
        child.stdout.close()


def relay(bind_ip, listen_port, target, guest_port):
    managed()
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((bind_ip, listen_port))
        listener.listen(4)
        print(f"TCP relay {bind_ip}:{listen_port} -> Kali 127.0.0.1:{guest_port}; accepting only {target}.", flush=True)
        print("firewalld is unchanged. Use firewall-hint if inbound traffic is blocked. Ctrl+C closes the relay.", flush=True)
        while True:
            peer, addr = listener.accept()
            if addr[0] != target:
                peer.close()
                continue
            relay_connection(peer, PM + ["exec", "-i", "kali", "socat", "STDIO", f"TCP4:127.0.0.1:{guest_port}"])


def firewall_hint(bind_ip, device, target, tcp_port):
    result = subprocess.run(["firewall-cmd", "--get-zone-of-interface", device], capture_output=True, text=True)
    zone = result.stdout.strip()
    if result.returncode or not zone or zone == "no zone":
        zone = output(["firewall-cmd", "--get-default-zone"])
    rule = f'rule family="ipv4" source address="{target}/32" destination address="{bind_ip}/32" port port="{tcp_port}" protocol="tcp" accept'
    print("The firewall has not been changed. This optional rule requires administrator access and allows only the selected TCP port from the specified target for one hour:")
    print(shlex.join(["sudo", "firewall-cmd", "--zone=" + zone, "--add-rich-rule=" + rule, "--timeout=1h"]))
    print("To remove the rule early:")
    print(shlex.join(["sudo", "firewall-cmd", "--zone=" + zone, "--remove-rich-rule=" + rule]))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog="./htb.sh", description="HTB VPN on Fedora, tools in rootless Kali. Does not use sudo or change firewalld automatically.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("import", help="Import a private profile into NetworkManager without activating it")
    p.add_argument("profile")
    for command in ("up", "down", "status"):
        sub.add_parser(command)
    p = sub.add_parser("check", help="Check the VPN route and a known open TCP port from both sides")
    p.add_argument("target", type=target_ip)
    p.add_argument("port", type=int)
    p = sub.add_parser("scan", help="TCP connect scan of your target through the VPN")
    p.add_argument("target", type=target_ip)
    p.add_argument("ports", nargs="?", default="1-1024")
    for command in ("relay", "firewall-hint"):
        p = sub.add_parser(command)
        p.add_argument("target", type=target_ip)
        p.add_argument("port", type=port)
        if command == "relay":
            p.add_argument("--container-port", type=port)
    args = parser.parse_args()
    if os.geteuid() == 0:
        fail("Run as a regular user, never with sudo.")
    for binary in ("nmcli", "ip", "podman"):
        if not shutil.which(binary):
            fail("Missing " + binary + "; see docs/htb.md.")
    if args.command == "import":
        import_profile(args.profile)
    elif args.command in {"up", "down", "status"}:
        data = selected()
        if args.command == "status":
            active = output(["nmcli", "-t", "-f", "UUID", "connection", "show", "--active"]).splitlines()
            print("Project VPN:", "active" if data["uuid"] in active else "inactive")
        elif args.command == "up" and not data.get("configured"):
            fail("The profile is not configured safely; retry import.")
        else:
            if args.command == "up":
                # Reject modified full-tunnel profiles before activation.
                for field in ("ipv4.never-default", "ipv6.never-default"):
                    if nm_get(field, data["uuid"]) != "yes":
                        fail("The profile is no longer split-tunnel; it will not be activated.")
            sys.exit(subprocess.call(["nmcli", "--ask", "connection", args.command, "uuid", data["uuid"]]))
    else:
        bind_ip, device = vpn_context(args.target)
        if args.command == "check":
            if not 1 <= args.port <= 65535:
                fail("Invalid TCP port.")
            managed()
            print("Target route verified through the selected VPN.")
            probe(args.target, args.port)
        elif args.command == "scan":
            if not re.fullmatch(r"[0-9]+(?:-[0-9]+)?(?:,[0-9]+(?:-[0-9]+)?)*", args.ports):
                fail("Invalid ports; use 1-65535 or a list such as 22,80,443.")
            for item in args.ports.split(","):
                numbers = [int(x) for x in item.split("-")]
                if not all(1 <= x <= 65535 for x in numbers) or numbers != sorted(numbers):
                    fail("Invalid port range.")
            managed()
            sys.exit(subprocess.call(PM + ["exec", "kali", "nmap", "--unprivileged", "-sT", "-Pn", "-sV", "--reason", "-p", args.ports, args.target]))
        elif args.command == "relay":
            relay(bind_ip, args.port, args.target, args.container_port or args.port)
        else:
            firewall_hint(bind_ip, device, args.target, args.port)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print("ERROR:", exc, file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nOperation stopped; the VPN is not disconnected and the container is not removed.")
        sys.exit(130)
