# Technical guide and recovery

This guide expands on the [README](../README.md). Use `./kali.sh help` for commands.

## Requirements

Fedora 44 with Bash, Python 3, coreutils (`stat`, `realpath`), `flock` (`util-linux`), `getenforce` (`libselinux-utils`), local rootless Podman, `pasta` (`passt`), `newuidmap`/`newgidmap` (`shadow-utils`), and at least 65,536 subordinate IDs in both `/etc/subuid` and `/etc/subgid` are required. SELinux must be Enforcing and seccomp enabled. Run the scripts as a regular Fedora user, **never with sudo**. You need network access to Docker Hub, Kali APT repositories, and `https://www.kali.org/`.

The script does not install host packages or fix subordinate ID ranges, networking, or host policy. If host packages are missing, install them yourself with administrator privileges from Fedora repositories:

```bash
sudo dnf --repo=fedora --repo=updates install podman passt shadow-utils fuse-overlayfs python3 util-linux libselinux-utils coreutils git
```

Inspect before setup with `./kali.sh inspect`. At least 22 GiB must be free in Podman storage initially (20 GiB to remain free plus a 2 GiB initial allowance). APT-based estimates are checked before installing; they are not quotas or guarantees. `slirp4netns` is not used.

## Image and reproducibility

The source is the [official Kali image](https://www.kali.org/docs/containers/official-kalilinux-docker-images/) `docker.io/kalilinux/kali-rolling:latest`; see also [Kali's Podman guide](https://www.kali.org/docs/containers/using-kali-podman-images/). The tracked `image.lock` contains its SHA-256 digest. The script checks the repository and digest format, uses that base for new containers, and does not silently update it on later `setup` runs. If the lock is absent, it pulls the official tag and generates a lock. The image must support your architecture; validation has been performed on x86_64, not every architecture.

The digest pins the base image, **not Kali Rolling APT packages**. APT versions and dependencies can change. `.state/packages.tsv` records versions installed locally, but does not lock package repositories. TLS and APT signature checks remain enabled. Do not commit private lab files, credentials, full logs, or images.

## Isolation and permissions

- Podman runs locally in rootless mode. Host root and environment overrides that redirect Podman are rejected.
- Container UID 0 can manage APT but maps to the regular Fedora user through `--userns=keep-id:uid=0,gid=0`; it is **not** host root.
- `pasta:--no-map-gw` supplies outbound networking without published ports or host networking. Accessible LAN hosts may also be reachable; this is not an outbound firewall.
- PID, IPC, UTS and cgroup namespaces are private; the container has a 512-process limit. Capabilities are dropped except `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `FSETID`, `SETGID`, `SETUID`, `SETFCAP`, and `SYS_CHROOT`. There is no `NET_ADMIN` or `NET_RAW`.
- `no-new-privileges`, default seccomp and SELinux remain in force; no privileged mode, extra devices, engine socket, or home-directory mounts are granted.
- The sole user bind mount is `./lab` to `/lab`, read/write with private SELinux `:Z` labeling. `lab/` and `.state/` must belong to the user, have mode `0700`, and not be symlinks. Files written in `/lab` are visible on Fedora; changes and deletions affect both sides.
- The container persists after Bash exits and is not started automatically on Fedora boot. The main-process log is capped at 10 MB.

The container shares Fedora's kernel. Do not treat it as a virtual machine or use it to run unknown malware. The script does not change host SELinux, firewalld, bootloader, kernel, drivers, NetworkManager or Fedora repositories.

## Modes, plans and package installation

`./kali.sh setup` creates or reuses the managed `kali` container, updates Kali's APT indexes and installs `ca-certificates`, `curl`, `iproute2`, `bind9-dnsutils` and `iputils-ping` plus required dependencies (`--no-install-recommends --no-remove`). Bash and APT come from the base image. Setup does **not** install a desktop, SSH server, broad tool metapackage or host dependencies. It validates HTTPS/DNS, isolation, permissions, packages and stop/start persistence. `validate` and the final validation in `setup` and `install` **restart the container**; close active work first.

## In-place updates

After updating the repository with `git pull --ff-only`, use `./kali.sh status` and `./kali.sh update` from the **original checkout path**, as the same regular Fedora user and Podman storage. A container created by a previous version (including the Spanish-language version) is reused if it passes the existing audit: the managed label, official image, `lab/` bind mount, security options and container name must match. It is never force-adopted or recreated. Review and back up important work before package changes. If you moved the checkout, restore the original path; `update` will not silently remount or replace the container.

Default `update` acquires the operation lock, starts the existing managed container if needed, refreshes APT indexes, plans/checks space for the current Minimum package list, installs only those packages and required dependencies, applies idempotent project fixes, and runs `validate` (which stops and restarts the container). Future container fixes and Minimum additions belong in this path, so an old Everything container receives them without reinstalling `kali-linux-everything`. The current fix removes Nmap's incompatible file capabilities **only when Nmap is installed**; this is checked on each update because package upgrades can restore them. Repeating `setup` or installing a profile also applies these fixes. New checkout scripts take effect as soon as pulled, without being copied into Kali.

`./kali.sh update --packages` additionally simulates and checks space for `apt-get upgrade`, then upgrades the **already installed** Kali APT packages with `--no-remove` in the same container. APT may hold back packages requiring removal. This can be a substantial download for Everything, but is not a reinstall of the metapackage. The APT plan is saved under `.state/plan-upgrade.*`, and each update is logged to `.state/update-*.log`; reports are local, not committed. Neither mode pulls an image, updates Fedora packages, grants capabilities, nor resets installed tool profiles or internal files. If newly selected optional tools are desired, rerun `./kali.sh install PROFILE` in place. Changes to container creation options that cannot be applied safely to an existing container require a separate, explicitly reviewed migration or backup/recreation; `update` must not pretend to apply them.

Older scripts could pass the operation-lock file descriptor to Podman. Its long-running `conmon` child then kept the lock after the script exited. The current script closes that descriptor before invoking Podman; this fix takes effect on the next container start. If a previously running container still holds the lock, check that no other package operation is running, close all Kali sessions/services, run `./kali.sh stop`, and retry `./kali.sh update`. Do not delete or replace the lock file to bypass a real operation. Before updating or validating, the script also checks `podman top` for processes besides PID 1 and refuses to stop a container with active work.

If you started with Minimum, add Everything **without removing the container**:

```bash
./kali.sh plan everything-full     # Estimate only; optional
./kali.sh install everything-full  # Add the full metapackage and recommendations
```

Other profiles are optional:

| Profile | What `plan` estimates / `install` installs |
| --- | --- |
| `everything-full` | `kali-linux-everything` and APT recommendations (the README's Everything mode) |
| `everything` | The same metapackage, without APT recommendations |
| `headless` | `kali-linux-headless` without recommendations |
| `cli` | Six topic-based `kali-tools-*` metapackages without recommendations (not guaranteed console-only) |

`plan` refreshes APT indexes and runs `apt-get --simulate --no-remove` to select packages **without downloading or installing them**, then calculates a conservative size budget using the simulated candidates' APT metadata. It counts each candidate's full installed size even for upgrades, so the estimate may exceed actual additional usage. The former `--assume-no` approach was unsafe: a real APT version performed a small install without prompting. Plan output is saved to `.state/plan-PROFILE.txt` and `.json`; indexes and reports still occupy disk space. If APT cannot simulate the plan or metadata is missing, the operation stops. `install` plans again, checks free space, confirms the image's `policy-rc.d` blocks automatic service starts, and installs with APT. The space budget adds estimated installed size, download size and 2 GiB headroom, leaving at least 20 GiB free in Podman storage. Actual usage can differ (snapshots, caches, downloads, and other processes may use more). Do not stop Kali or run `validate` during APT installation. `install` logs to `.state/install-*.log`.

Installed does not mean ready to use: graphical applications need a display, services/databases need their own setup, and some tools need accounts or API keys. `nmap --unprivileged -sT` supports TCP connect scanning; SYN scanning, raw sockets and live packet capture are restricted. Wi-Fi monitor/injection, Bluetooth, SDR, USB and GPU need additional access not provided here. Use tools only for authorized targets. See [Kali metapackages](https://www.kali.org/docs/general-use/metapackages/) and [Podman run options](https://docs.podman.io/en/latest/markdown/podman-run.1.html).

## Backup, recovery and removal

Stop the container and back up the internal layer and `lab/` separately; `podman export` **does not include bind-mounted `lab/`**. Exports can contain secrets. For example, from the repository directory, choose a new backup identifier rather than overwriting existing files:

```bash
./kali.sh stop
mkdir -m 700 backups  # Omit if already present; check its permissions
(umask 077; set -C; podman export kali > backups/kali-rootfs-IDENTIFIER.tar)
(umask 077; set -C; tar -czf backups/lab-IDENTIFIER.tar.gz -- lab)
(umask 077; set -C; tar -czf backups/state-IDENTIFIER.tar.gz -- .state)
```

Verify command exit statuses and archive contents before deleting anything. An export includes the container filesystem, not its full configuration. Recreating from the repository with `setup` gives a fresh Minimum container, **not** the old internal files or additional packages. Restoring an export requires a manually reviewed import and matching container configuration; this procedure is not automated or claimed to be tested.

```bash
./kali.sh stop
./kali.sh remove
```

`remove` re-audits the container, requires it to be stopped and an interactive terminal, prints the count of changes to its internal layer, and demands `REMOVE kali` followed by the full ID shown. It removes that container by ID without `--force`; the entire internal writable layer (including `/root`, `/var/tmp`, and installed tools) is lost. `lab/`, `.state/`, backups and the image remain. It does not remove other containers, images or volumes, and does not run `podman system prune`.

## Verification

```bash
bash -n kali.sh
python3 -m unittest discover -s tests -v
git diff --check
```

The regression tests use a fake Podman engine and do not download images or modify real Podman storage. Run them as a regular user on Fedora 44. They do not replace live container validation. Historical measurements are in [validation notes](validation.md).
