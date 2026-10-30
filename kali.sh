#!/usr/bin/env bash
# Kali CLI on Fedora 44. Run as your regular Fedora user, never with sudo.
set -Eeuo pipefail
umask 077

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
LAB="$ROOT/lab"
STATE="$ROOT/.state"
LOCK="$ROOT/image.lock"
NAME=kali
TAG=docker.io/kalilinux/kali-rolling:latest
LABEL=io.github.kali-in-fedora.managed
RESERVE=$((20 * 1024 * 1024 * 1024))
MINIMUM=(ca-certificates curl iproute2 bind9-dnsutils iputils-ping)
CAPS=(CHOWN DAC_OVERRIDE FOWNER FSETID SETGID SETUID SETFCAP SYS_CHROOT)

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
note() { printf '\n%s\n' "$*"; }
run() { printf '+'; printf ' %q' "$@"; printf '\n'; "$@"; }
# Never pass the operation-lock descriptor to Podman: its long-lived conmon
# child would otherwise keep the flock held after setup/update exits.
pm() { podman --remote=false "$@" 9>&-; }
trap 'printf "ERROR: operation failed at line %s; no container was removed.\n" "$LINENO" >&2' ERR

help_text() {
    cat <<'EOF'
Usage: ./kali.sh COMMAND
  inspect              Inspect the host without installing or creating containers
  setup                Create/reuse kali and install Minimum only
  install everything-full  Add Everything (including APT recommendations) to existing kali
  update [--packages]  Apply fixes in place; optionally upgrade APT packages; restarts kali
  validate             Check security, HTTPS and persistence; restarts kali
  start | stop         Start or stop kali
  shell                Enter Bash with unprivileged Nmap defaults; exit keeps kali running
  status | logs        Show status or the last 100 main-process log entries
  inventory            Save installed versions to .state/packages.tsv
  plan [PROFILE]      Refresh APT indexes and estimate WITHOUT installing (default: cli)
  install PROFILE      Install an explicitly selected profile after checking disk space
  remove               Remove a stopped kali after interactive confirmation
  help                 Show this help

Profiles: cli, headless, everything (no APT recommendations), everything-full
(with recommendations). setup installs only Minimum; install adds tools without
recreating kali. update never reinstalls Everything or replaces the container.
Podman and other Fedora packages are never installed automatically.
EOF
}

prerequisites() {
    (( EUID != 0 )) || die 'Run as your regular user, without sudo.'
    local bin
    for bin in python3 podman realpath stat getenforce; do
        command -v "$bin" >/dev/null || die "Missing $bin. See the README requirements."
    done
    [[ -r /etc/os-release ]] || die 'Cannot read /etc/os-release.'
    [[ $(python3 -c 'import platform; d=platform.freedesktop_os_release(); print(d.get("ID"),d.get("VERSION_ID"))') == 'fedora 44' ]] || die 'This script requires Fedora 44.'
    [[ "$ROOT" != *:* && "$ROOT" != *$'\n'* && "$ROOT" != *$'\r'* ]] || die 'The project path contains characters incompatible with volume mounts.'
    local var
    for var in CONTAINER_HOST CONTAINER_CONNECTION CONTAINERS_CONF CONTAINERS_STORAGE_CONF CONTAINERS_REGISTRIES_CONF PODMAN_USERNS; do
        [[ -z ${!var:-} ]] || die "Environment variable $var may override the container environment; review it before continuing."
    done
    pm info --format json | python3 -c '
import json,sys
s=json.load(sys.stdin)["host"]["security"]
if not all(s.get(k) is True for k in ("rootless","seccompEnabled","selinuxEnabled")):
  sys.exit("Local rootless Podman with seccomp and SELinux support is required.")
'
    [[ $(getenforce) == Enforcing ]] || die 'SELinux Enforcing is required; this script does not change its configuration.'
}

private_dir() {
    local path=$1
    [[ ! -L "$path" ]] || die 'A working directory cannot be a symbolic link.'
    if [[ -e "$path" ]]; then
        [[ -d "$path" && $(stat -c %u "$path") == "$EUID" && $(stat -c %a "$path") == 700 ]] ||
            die 'The existing directory must belong to this user and have mode 0700; permissions are not changed automatically.'
    else
        mkdir -m 700 -- "$path"
    fi
}

container_exists() {
    local rc=0
    pm container exists "$NAME" || rc=$?
    case $rc in 0) return 0;; 1) return 1;; *) die 'Could not check whether kali exists.';; esac
}

space() {
    pm info --format '{{.Store.GraphRoot}}' | python3 -c '
import pathlib, shutil, sys
p=pathlib.Path(sys.stdin.read().strip())
while not p.exists(): p=p.parent
u=shutil.disk_usage(p)
print(u.free)
'
}

check_space() {
    local needed=${1:-0} free
    free=$(space)
    python3 -c 'import sys; f,n,r=map(int,sys.argv[1:]); print(f"Free in Podman storage: {f/2**30:.2f} GiB; required remaining space: {r/2**30:.0f} GiB; estimated additional space: {n/2**30:.2f} GiB"); sys.exit(0 if f-n>=r else 1)' "$free" "$needed" "$RESERVE" ||
        die 'Not enough free space to keep 20 GiB after the estimated installation.'
}

inspect_host() {
    note 'Host inspection (does not install packages or create containers)'
    python3 - <<'PY'
import os, pathlib, platform, pwd, shutil, subprocess
d=dict(l.split('=',1) for l in pathlib.Path('/etc/os-release').read_text().splitlines() if '=' in l)
print('System:', d.get('PRETTY_NAME','').strip('"')); print('Kernel:',platform.release()); print('Architecture:',platform.machine())
print('Unprivileged user:', os.geteuid()!=0)
u=shutil.disk_usage('.');print(f'Project filesystem: {u.free/2**30:.2f} GiB free')
user=pwd.getpwuid(os.getuid()).pw_name
for name in ('subuid','subgid'):
  try:
   found=any(l.split(':')[0] in (user,str(os.getuid())) and int(l.strip().split(':')[2])>=65536 for l in pathlib.Path('/etc/'+name).read_text().splitlines())
   print(name+': range of at least 65536 IDs:',found)
  except (OSError,ValueError,IndexError): print(name+': could not verify')
for args in (['podman','--version'],['getenforce'],['systemctl','is-active','firewalld']):
 if shutil.which(args[0]):
   r=subprocess.run(args,capture_output=True,text=True); print(' '.join(args)+':',r.stdout.strip() or 'could not verify', '(exit='+str(r.returncode)+')')
for key in ('CONTAINER_HOST','CONTAINER_CONNECTION','CONTAINERS_CONF','CONTAINERS_STORAGE_CONF','CONTAINERS_REGISTRIES_CONF','PODMAN_USERNS'):
 if os.environ.get(key): print('Environment override to review (value hidden):',key)
for base in (pathlib.Path('/etc/containers'),pathlib.Path.home()/'.config/containers'):
 for name in ('containers.conf','storage.conf','registries.conf','mounts.conf'):
   print(('System' if str(base).startswith('/etc') else 'User')+'/'+name+':', (base/name).exists())
PY
    if command -v podman >/dev/null; then
        pm info --format json | python3 -c '
import json,sys
d=json.load(sys.stdin); h=d["host"]; s=d["store"]
print("Podman:", {"rootless": h["security"]["rootless"], "SELinux": h["security"]["selinuxEnabled"], "seccomp":h["security"]["seccompEnabled"], "network":h["networkBackend"], "rootless_network":h.get("rootlessNetworkCmd"), "storage":s["graphDriverName"]})
'
        if container_exists; then
            pm inspect "$NAME" | python3 -c '
import json,sys
d=json.load(sys.stdin)[0];h=d["HostConfig"]
print("kali already exists; it will not be overwritten:", {"status":d["State"]["Status"],"image":d.get("ImageName"),"privileged":h.get("Privileged"),"network":h.get("NetworkMode"),"mounts":len(d.get("Mounts",[])),"published_ports":len(h.get("PortBindings") or {})})
'
        else
            printf 'No container named kali exists.\n'
        fi
    else
        printf 'Podman is not installed. Manual host setup: sudo dnf --repo=fedora --repo=updates install podman passt shadow-utils fuse-overlayfs python3 util-linux libselinux-utils coreutils git\n'
    fi
}

audit() {
    pm inspect "$NAME" | python3 -c '
import json,os,sys
d=json.load(sys.stdin)[0];h=d["HostConfig"];c=d["Config"]; lab=sys.argv[1]
mounts=d.get("Mounts",[]); allowed={"CAP_CHOWN","CAP_DAC_OVERRIDE","CAP_FOWNER","CAP_FSETID","CAP_SETGID","CAP_SETUID","CAP_SETFCAP","CAP_SYS_CHROOT"}
caps=set(d.get("EffectiveCaps",[]))
checks={
  "managed_by_project":c.get("Labels",{}).get("io.github.kali-in-fedora.managed")=="1",
  "official_image":d.get("ImageName", "").startswith("docker.io/kalilinux/kali-rolling@sha256:"),
  "not_privileged":h.get("Privileged") is False,
  "persistent":h.get("AutoRemove") is False,
  "pasta_network":h.get("NetworkMode","").split(":")[0]=="pasta",
  "no_published_ports":not h.get("PortBindings"),
  "no_extra_devices":not h.get("Devices") and not h.get("DeviceRequests"),
  "private_PID":h.get("PidMode") not in ("host",),
  "private_IPC":h.get("IpcMode") not in ("host",),
  "no_new_privileges":"no-new-privileges" in h.get("SecurityOpt",[]),
  "limited_capabilities":bool(caps) and caps<=allowed,
  "only_lab_mounted":len(mounts)==1 and mounts[0].get("Type")=="bind" and mounts[0].get("Source")==lab and mounts[0].get("Destination")=="/lab" and mounts[0].get("RW") is True,
  "SELinux_enabled":not any(x.startswith("label=disable") or x.startswith("label:disable") for x in h.get("SecurityOpt",[])),
  "seccomp_enabled":not any("seccomp=unconfined" in x or "seccomp:unconfined" in x for x in h.get("SecurityOpt",[])),
  "private_lab":not os.path.islink(lab) and os.stat(lab).st_uid==os.getuid() and os.stat(lab).st_mode & 0o777==0o700,
}
for k,v in checks.items(): print(("OK " if v else "FAIL ")+k)
if not all(checks.values()): sys.exit("Incompatible container: it will not be reused or modified.")
' "$LAB"
}

require_managed() {
    container_exists || die 'No kali container exists. Run ./kali.sh setup first.'
    audit
}

start_container() {
    if [[ $(pm inspect --format '{{.State.Status}}' "$NAME") != running ]]; then
        run pm start "$NAME"
    fi
}

ensure_no_active_work() {
    pm top "$NAME" pid state tty comm | python3 -c '
import sys
rows=sys.stdin.read().splitlines()
if len(rows)<2: sys.exit("Cannot verify container processes; refusing to interrupt active work.")
active=[]
for row in rows[1:]:
 fields=row.split(None,3)
 if len(fields)!=4 or not fields[0].isdigit(): sys.exit("Unexpected Podman process list; refusing to interrupt active work.")
 # The sleep PID 1 does not reap children; zombies cannot be interrupted or do work.
 if fields[0]!="1" and not fields[1].startswith("Z"): active.append(f"PID {fields[0]} ({fields[3]})")
if active: sys.exit("Active Kali processes: "+", ".join(active)+". Close shells and services before an update or validation; nothing was stopped.")
'
}

prepare_image() {
    if [[ -e "$LOCK" || -L "$LOCK" ]]; then
        [[ -f "$LOCK" && ! -L "$LOCK" ]] || die 'image.lock must be a regular file.'
        IMAGE=$(cat -- "$LOCK")
        [[ "$IMAGE" =~ ^docker\.io/kalilinux/kali-rolling@sha256:[a-f0-9]{64}$ ]] || die 'image.lock does not contain a valid official image digest.'
        run pm pull --policy=missing "$IMAGE"
    else
        run pm pull "$TAG"
        IMAGE=$(pm image inspect "$TAG" --format '{{index .RepoDigests 0}}')
        [[ "$IMAGE" =~ ^docker\.io/kalilinux/kali-rolling@sha256:[a-f0-9]{64}$ ]] || die 'Could not obtain an official image digest.'
        (set -C; printf '%s\n' "$IMAGE" > "$LOCK")
    fi
    printf 'Pinned base image: %s\n' "$IMAGE"
}

apt_update() {
    run pm exec --env LC_ALL=C --env DEBIAN_FRONTEND=noninteractive "$NAME" \
        apt-get -o APT::Update::Error-Mode=any update
}

apt_plan() {
    local profile=$1
    shift
    local raw="$STATE/plan-$profile.txt"
    local recommend_flag=--no-install-recommends
    local apt_args=(--simulate --no-remove)
    if [[ "$profile" == everything-full ]]; then
        recommend_flag=--install-recommends
    fi
    if [[ "$profile" == upgrade ]]; then
        apt_args+=(upgrade)
    else
        apt_args+=("$recommend_flag" install "$@")
    fi
    pm exec --env LC_ALL=C --env DEBIAN_FRONTEND=noninteractive "$NAME" \
        apt-get "${apt_args[@]}" > "$raw" 2>&1 || { cat -- "$raw"; die 'APT simulation failed; nothing was installed by the plan.'; }
    python3 "$ROOT/lib/apt_plan.py" "$raw" "$STATE/plan-$profile.json" "$profile"
}

inventory() {
    private_dir "$STATE"
    pm exec "$NAME" dpkg-query -W '-f=${binary:Package}\t${Version}\t${Architecture}\n' > "$STATE/packages.tsv"
    printf 'Local package inventory: .state/packages.tsv\n'
}

validate() {
    note 'Validating Kali (stops and restarts the container; does not delete files)'
    require_managed
    start_container
    ensure_no_active_work
    pm exec "$NAME" bash -ceu '
        . /etc/os-release
        test "$ID" = kali
        printf "Kali: %s\n" "$PRETTY_NAME"
        apt-get --version | head -n 1
        test -z "$(dpkg --audit)"
        for p in ca-certificates curl iproute2 bind9-dnsutils iputils-ping; do
            test "$(dpkg-query -W -f="\${Status}" "$p")" = "install ok installed"
        done
        curl --fail --silent --show-error --location --connect-timeout 15 --max-time 60 --output /dev/null https://www.kali.org/
        printf "External HTTPS and DNS resolution: OK\n"
        ip -brief link show lo
        if ping -n -c 1 -W 2 127.0.0.1 >/dev/null 2>&1; then
            printf "ICMP ping to loopback: OK\n"
        else
            printf "ICMP ping to loopback unavailable; check rootless network/kernel permissions.\n"
        fi
        test -n "$(dig +short www.kali.org A)"
        test "$(id -u)" = 0
        grep -q "^NoNewPrivs:[[:space:]]*1" /proc/self/status
        grep -q "^Seccomp:[[:space:]]*2" /proc/self/status
        printf "Container UID 0, seccomp and NoNewPrivs: OK\n"
    '
    # Read from the host namespace: inside a nested user namespace the parent
    # mapping may legitimately display 0 -> 0, despite being rootless on Fedora.
    local pid
    pid=$(pm inspect --format '{{.State.Pid}}' "$NAME")
    python3 -c '
import os,pathlib,sys
rows=[tuple(map(int,l.split())) for l in (pathlib.Path("/proc")/sys.argv[1]/"uid_map").read_text().splitlines()]
if not any(inside==0 and outside==os.getuid()!=0 and size>=1 for inside,outside,size in rows):
  sys.exit("Container UID 0 is not mapped to the expected Fedora user.")
print("Verified from Fedora: container UID 0 maps to the unprivileged Fedora user.")
' "$pid"
    local token inside shared
    token=$(python3 -c 'import secrets;print(secrets.token_hex(16))')
    inside="/var/tmp/kali-in-fedora-$token"
    shared=".persistence-$token"
    pm exec "$NAME" bash -ceu 'umask 077; printf "%s" "$1" > "$2"; printf "%s" "$1" > "/lab/$3"' bash "$token" "$inside" "$shared"
    [[ $(cat -- "$LAB/$shared") == "$token" && $(stat -c %u "$LAB/$shared") == "$EUID" && $(stat -c %a "$LAB/$shared") == 600 ]] || die 'Shared files do not have the expected owner and permissions.'
    run pm stop --time 10 "$NAME"
    run pm start "$NAME"
    pm exec "$NAME" bash -ceu 'test "$(cat -- "$2")" = "$1"; test "$(cat -- "/lab/$3")" = "$1"; for p in ca-certificates curl iproute2 bind9-dnsutils iputils-ping; do dpkg-query -W "$p"; done' bash "$token" "$inside" "$shared"
    # Only files created by this validation are removed, inside the container.
    pm exec "$NAME" bash -ceu 'test "$(cat -- "$2")" = "$1"; test "$(cat -- "/lab/$3")" = "$1"; unlink -- "$2"; unlink -- "/lab/$3"' bash "$token" "$inside" "$shared"
    inventory
    note 'Validated: HTTPS/DNS, packages, loopback, rootless UID, seccomp, permissions and persistence across stop/start.'
    printf 'Not tested: Fedora reboot, all tools, desktop integration, Wi-Fi, GPU or other hardware.\n'
    check_space
}

setup() {
    note 'Minimum mode: five basic Kali packages and their required dependencies; no broad tool metapackage'
    printf 'Pinned official image; persistent kali; only lab/:Z mounted; pasta network; no published ports; keep 20 GiB free.\n'
    note 'Checking existing rootless Podman (host packages are not installed automatically)'
    prerequisites
    check_space $((2 * 1024 * 1024 * 1024))
    if container_exists; then
        require_managed
    else
        private_dir "$LAB"
        private_dir "$STATE"
        prepare_image
        local cap args=()
        for cap in "${CAPS[@]}"; do args+=(--cap-add="$cap"); done
        note 'Creating persistent kali container'
        run pm create --name "$NAME" --label "$LABEL=1" \
            --hostname kali --userns=keep-id:uid=0,gid=0 --user=0:0 \
            --network=pasta:--no-map-gw --pid=private --ipc=private --uts=private --cgroupns=private \
            --cap-drop=all "${args[@]}" --security-opt=no-new-privileges \
            --pids-limit=512 --restart=no --stop-timeout=10 \
            --log-driver=k8s-file --log-opt=max-size=10mb \
            --volume "$LAB:/lab:rw,Z" --workdir=/lab \
            "$IMAGE" /usr/bin/sleep infinity
        audit
    fi
    private_dir "$STATE"
    start_container
    ensure_no_active_work
    apt_update
    apt_plan minimum "${MINIMUM[@]}"
    local budget
    budget=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["budget_bytes"])' "$STATE/plan-minimum.json")
    check_space "$budget"
    note 'Installing Minimum inside Kali (ca-certificates, curl, iproute2, bind9-dnsutils, iputils-ping)'
    run pm exec --env LC_ALL=C --env DEBIAN_FRONTEND=noninteractive "$NAME" \
        apt-get --yes --no-remove --no-install-recommends install "${MINIMUM[@]}"
    apply_container_fixes
    validate
}

profile_packages() {
    case "$1" in
        cli) PACKAGES=(kali-tools-information-gathering kali-tools-web kali-tools-sniffing-spoofing kali-tools-forensics kali-tools-crypto-stego kali-tools-reverse-engineering);;
        headless) PACKAGES=(kali-linux-headless);;
        everything|everything-full) PACKAGES=(kali-linux-everything);;
        *) die 'Unknown profile; use cli, headless, everything or everything-full.';;
    esac
}

plan() {
    local profile=${1:-cli}
    profile_packages "$profile"
    require_managed
    private_dir "$STATE"
    start_container
    apt_update
    note "Planning $profile; this does NOT install tools (APT indexes are refreshed)"
    printf 'Metapackages:'; printf ' %s' "${PACKAGES[@]}"; printf '\n'
    apt_plan "$profile" "${PACKAGES[@]}"
    local budget
    budget=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["budget_bytes"])' "$STATE/plan-$profile.json")
    check_space "$budget"
    printf 'This plan does not install tools or guarantee compatibility. Run install explicitly to add this profile.\n'
}

ensure_service_policy() {
    # Prevent package postinst scripts from starting network services by default.
    # The official image already ships policy-rc.d returning 101; never replace it.
    pm exec "$NAME" bash -c '
        if test ! -e /usr/sbin/policy-rc.d; then
            (set -C; printf "#!/bin/sh\nexit 101\n" > /usr/sbin/policy-rc.d) || exit
            chmod 755 /usr/sbin/policy-rc.d
        fi
        /usr/sbin/policy-rc.d kali-in-fedora start >/dev/null 2>&1
        test "$?" -eq 101
    ' || die 'The service-start policy does not block automatic starts; review it before installing.'
}

apply_container_fixes() {
    # Keep fixes idempotent: this runs on existing Minimum and Everything containers
    # after updates, including containers created with older versions of this script.
    # Never rewrite an unrelated container or reset its installed package selection.
    # Kali's Nmap file capabilities conflict with no-new-privileges. An APT update
    # may restore them, so check on every run rather than only after first install.
    pm exec "$NAME" bash -ceu '
        if test -e /usr/lib/nmap/nmap; then
            if test -n "$(getcap /usr/lib/nmap/nmap)"; then
                setcap -r /usr/lib/nmap/nmap
            fi
            test -z "$(getcap /usr/lib/nmap/nmap)"
            nmap --unprivileged --version | head -n 1
        fi
    '
}

update_container() {
    local option=${1:-}
    [[ -z "$option" || "$option" == --packages ]] || die 'Usage: ./kali.sh update [--packages]'
    require_managed
    private_dir "$STATE"
    note 'Updating the existing kali container in place; no image pull, container replacement or Everything reinstall'
    start_container
    ensure_no_active_work
    check_space $((2 * 1024 * 1024 * 1024))
    apt_update
    apt_plan minimum "${MINIMUM[@]}"
    local budget
    budget=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["budget_bytes"])' "$STATE/plan-minimum.json")
    check_space "$budget"
    ensure_service_policy
    run pm exec --env LC_ALL=C --env DEBIAN_FRONTEND=noninteractive "$NAME" \
        apt-get --yes --no-remove --no-install-recommends install "${MINIMUM[@]}"
    if [[ "$option" == --packages ]]; then
        note 'Upgrading installed Kali APT packages in place; no package removals or Everything reinstall'
        apt_plan upgrade
        budget=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["budget_bytes"])' "$STATE/plan-upgrade.json")
        check_space "$budget"
        run pm exec --env LC_ALL=C --env DEBIAN_FRONTEND=noninteractive "$NAME" \
            apt-get --yes --no-remove -o Dpkg::Use-Pty=0 upgrade
    else
        note 'Other installed APT packages were not upgraded; use update --packages to upgrade them explicitly.'
    fi
    apply_container_fixes
    validate
    note 'In-place update complete; installed tool profiles and container files were kept.'
}

install_tools() {
    local profile=${1:-}
    [[ -n "$profile" ]] || die 'Specify a profile: install everything-full, everything, headless or cli.'
    require_managed
    start_container
    ensure_no_active_work
    plan "$profile"
    note "Installing $profile inside the existing kali container; this keeps your current packages and files"
    ensure_service_policy
    pm exec -i "$NAME" debconf-set-selections <<'EOF'
wireshark-common wireshark-common/install-setuid boolean false
macchanger macchanger/automatically_run boolean false
EOF
    local recommend_flag=--no-install-recommends
    [[ "$profile" != everything-full ]] || recommend_flag=--install-recommends
    run pm exec --env LC_ALL=C --env DEBIAN_FRONTEND=noninteractive "$NAME" \
        apt-get --yes --no-remove "$recommend_flag" -o Dpkg::Use-Pty=0 install "${PACKAGES[@]}"
    apply_container_fixes
    inventory
    validate
    pm exec "$NAME" dpkg-query -W "${PACKAGES[@]}"
    if [[ "$profile" == everything || "$profile" == everything-full ]]; then
        pm exec "$NAME" bash -ceu '
            test -z "$(dpkg --audit)"
            for tool in nmap tcpdump tshark sqlmap nikto gobuster ffuf python3 gdb yara; do
                command -v "$tool" >/dev/null
                printf "Available: %s\n" "$tool"
            done
        '
    fi
    note 'Installation complete. Services, graphical tools and hardware-dependent tools may need separate setup.'
}

remove_container() {
    require_managed
    [[ -t 0 ]] || die 'Removal requires confirmation in an interactive terminal.'
    [[ $(pm inspect --format '{{.State.Status}}' "$NAME") != running ]] || die 'Stop the container first; review and back up your data.'
    local cid answer
    cid=$(pm inspect --format '{{.Id}}' "$NAME")
    note 'ALL packages and files in the container writable layer will be lost, including /root and /var/tmp.'
    printf 'lab/, .state/, backups and the image are kept. Back up your data first.\n'
    pm diff "$cid" | python3 -c 'import sys;print("Changed entries in the container writable layer:",sum(1 for _ in sys.stdin))'
    printf 'To confirm, type exactly REMOVE kali %s: ' "$cid"
    IFS= read -r answer
    [[ "$answer" == "REMOVE kali $cid" ]] || die 'Confirmation did not match; nothing was removed.'
    run pm rm "$cid"
}

command=${1:-help}
case "$command" in
    help|-h|--help) help_text; exit 0;;
    inspect) inspect_host; exit 0;;
esac
prerequisites
case "$command" in
    setup|validate|plan|install|update)
        command -v flock >/dev/null || die 'Missing flock (util-linux).'
        private_dir "$STATE"
        [[ ! -L "$STATE/operation.lock" ]] || die 'The operation lock cannot be a symbolic link.'
        exec 9>"$STATE/operation.lock"
        flock -n 9 || die 'The operation lock is held. If another installation or validation is running, wait for it. If no operation is running, an older conmon may have inherited the lock: close active Kali sessions, run ./kali.sh stop, then retry. Do not delete the lock file.'
        ;;
esac
case "$command" in
    setup) private_dir "$STATE"; setup 2>&1 | tee "$STATE/setup-$(date -u +%Y%m%dT%H%M%SZ).log";;
    validate) private_dir "$STATE"; validate 2>&1 | tee "$STATE/validate-$(date -u +%Y%m%dT%H%M%SZ).log";;
    start) require_managed; start_container;;
    stop) require_managed; run pm stop --time 10 "$NAME";;
    shell) require_managed; start_container; pm exec -it --env NMAP_UNPRIVILEGED=1 --workdir /lab "$NAME" bash -c 'umask 077; exec bash -i';;
    status) require_managed; pm inspect --format 'Status={{.State.Status}} Image={{.ImageName}} ID={{.Id}}' "$NAME"; check_space;;
    logs) require_managed; pm logs --tail 100 "$NAME";;
    inventory) require_managed; start_container; inventory;;
    plan) plan "${2:-cli}";;
    install) install_tools "${2:-}" 2>&1 | tee "$STATE/install-$(date -u +%Y%m%dT%H%M%SZ).log";;
    update) [[ $# -le 2 ]] || die 'Usage: ./kali.sh update [--packages]'; update_container "${2:-}" 2>&1 | tee "$STATE/update-$(date -u +%Y%m%dT%H%M%SZ).log";;
    remove) remove_container;;
    *) help_text; die 'Unknown command.';;
esac
