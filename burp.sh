#!/usr/bin/env bash
# Optional Burp GUI in Kali via a private virtual display and a local VNC relay.
set -Eeuo pipefail
umask 077
task_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
exec python3 "$task_root/lib/burp.py" "$@"
