#!/usr/bin/env bash
# VPN on Fedora; tools remain inside rootless Kali.
set -Eeuo pipefail
umask 077
task_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
exec python3 "$task_root/lib/htb.py" "$@"
