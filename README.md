# Kali in Fedora (rootless Podman)

Run a persistent, command-line Kali Linux environment inside a rootless Podman container on Fedora 44. This project creates a container named `kali` from the official Kali Rolling image; it does **not** replace Fedora, install a second kernel, or create a virtual machine. Kali packages and files inside the container persist across stops and starts. Only `lab/` is shared with Fedora.

## Choose an installation mode

Both modes start with `setup`. Run these commands from the cloned repository as your **regular user, without sudo**:

| Mode | Commands | What is installed inside Kali |
| --- | --- | --- |
| **Minimum** | `./kali.sh setup` | The Kali base image plus `ca-certificates`, `curl`, `iproute2`, `bind9-dnsutils` and their required dependencies. No broad Kali tool collection. |
| **Everything** | `./kali.sh setup` then `./kali.sh install everything-full` | The minimum, then the official `kali-linux-everything` metapackage, its dependencies **and APT recommendations**. This is a large installation; it does not make every tool usable without further configuration. |

**Already installed Minimum?** From the same repository directory, run `./kali.sh install everything-full`. The script reuses the existing `kali` container and keeps its installed packages and internal files; do **not** remove or recreate it to upgrade. It refreshes APT indexes, estimates and checks disk space, then installs the additional packages. `install` also restarts the container during final validation: finish any work inside it first. A repeated `setup` refreshes the minimum packages rather than resetting the container. An existing unrelated or incompatible container named `kali` is rejected, not overwritten.

`everything-full` includes APT recommendations. The optional `everything` profile installs the same metapackage **without** recommendations; `headless` and `cli` are other optional tool selections. None of these profiles is installed by `setup`. `./kali.sh plan everything-full` estimates without installing; only `install` installs tools.

## Requirements and first run

- Fedora **44**, Bash, Python 3, Git (to clone), local **rootless Podman**, `pasta` (Fedora package `passt`), `newuidmap`/`newgidmap` with at least 65,536 subordinate UIDs and GIDs, `flock` (`util-linux`), `getenforce` (`libselinux-utils`), and coreutils (`stat` and `realpath`). SELinux must be Enforcing, with seccomp enabled. The script checks these prerequisites and stops if they are not met; it does **not** install Fedora packages or change host configuration.
- Internet access to Docker Hub, Kali APT repositories, and `https://www.kali.org/` for validation. The pinned image in `image.lock` must support your architecture; the project has been validated on x86_64.
- At least **22 GiB free in Podman storage** to begin Minimum. The script estimates additional APT download and installed size before each installation, adds 2 GiB of headroom, and requires 20 GiB to remain free. Everything can need **many tens of GiB more**; check the plan on your own machine. This is a preflight check, not a disk quota.

If host packages are missing, install them **manually** from Fedora's official repositories (this is the only step here requiring administrator privileges):

```bash
sudo dnf --repo=fedora --repo=updates install podman passt shadow-utils fuse-overlayfs python3 util-linux libselinux-utils coreutils git
```

Your system may already include some of them. Ensure subordinate ID ranges and rootless Podman work before continuing; see [host requirements](docs/technical-guide.md#requirements). Do not run `kali.sh` with `sudo`.

```bash
git clone https://github.com/cschavezv03-ops/kali-in-fedora.git
cd kali-in-fedora
./kali.sh inspect                 # Read-only host inspection; installs nothing
./kali.sh setup                   # Minimum mode; downloads the image and installs Kali packages
./kali.sh shell                   # Enter Kali; type exit to leave it running
```

For Everything, after `setup` (either immediately or months later):

```bash
./kali.sh plan everything-full    # Optional: estimate only, no tool installation
./kali.sh install everything-full # Install Everything in the existing container
./kali.sh shell
```

The official `docker.io/kalilinux/kali-rolling` base is pinned by digest in `image.lock`. This pins the image, **not** future Kali Rolling APT package versions. Installation can take a long time. Do not stop the container during an installation. APT and operation logs are stored locally under `.state/` (for example `.state/setup-*.log` and `.state/install-*.log`). `lab/`, `.state/`, and `backups/` are excluded from Git.

## Everyday use and cleanup

| Action | Command |
| --- | --- |
| Enter Kali (start it if stopped) | `./kali.sh shell` |
| Leave the shell without stopping Kali | `exit` |
| Start / stop the container | `./kali.sh start` / `./kali.sh stop` |
| Check container status and free space | `./kali.sh status` |
| Check connectivity, isolation, and persistence | `./kali.sh validate` |
| Save installed package versions | `./kali.sh inventory` |
| Show main-process logs / help | `./kali.sh logs` / `./kali.sh help` |

`setup`, `install`, and `validate` stop and restart Kali during validation; **do not run them while work is active inside the container**. Stopping it does not uninstall anything, and it does not start automatically when Fedora boots. `logs` may be empty because the main process is `sleep`; use `.state/` for setup and installation logs.

To permanently remove the container, back up anything you need **first**, then run:

```bash
./kali.sh stop
./kali.sh remove
```

`remove` requires an interactive terminal and asks you to type **`REMOVE kali` followed by the full container ID shown in the prompt**. This deletes all installed Kali packages and files in the container's internal writable layer (including `/root` and `/var/tmp`). It **does not** delete `lab/`, `.state/`, backups, or the downloaded image. It refuses to remove a running or incompatible container. To use the project again afterward, run `./kali.sh setup` (and, if desired, `./kali.sh install everything-full`). For separate backups of the container and `lab/`, see [backup and recovery](docs/technical-guide.md#backup-recovery-and-removal). Do not move the checkout while the container exists: the bind mount uses its absolute path.

## Boundaries and optional VPN support

Inside Kali you appear as root for package management, **not** as Fedora's root user. `/lab` maps to this repository's `lab/` with private permissions; edits and deletions there affect both sides. There are no published ports or extra devices, no desktop integration, and no automatic `systemd` services. The container has outbound networking (including potentially reachable LAN hosts), but no `NET_RAW` or `NET_ADMIN`; SYN scans, live capture, Wi-Fi injection, and hardware-dependent tools may not work. Installing Everything does not automatically configure GUIs, services, accounts, or hardware. Use tools only on authorized targets. See the [technical guide](docs/technical-guide.md) for isolation, plans, and limitations.

Optional Hack The Box support keeps the VPN **on Fedora**, not in Kali. It requires Fedora `openvpn` and `NetworkManager-openvpn`, a private personal VPN profile, and (for the provided scan/relay commands) Everything or an appropriate tool profile. It is **not** installed or connected by `setup`. See [HTB VPN setup and commands](docs/htb.md). Historical test results, which are not requirements or promises for other machines, are in [validation notes](docs/validation.md).
