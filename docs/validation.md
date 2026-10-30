# Historical Kali Everything validation

These are **example observations**, not minimum requirements, expected results for other users, or a guarantee that every Kali tool works. Recorded on Fedora 44 x86_64 in October 2026 with the Kali Rolling repositories available at the time. No VPN credentials, private addresses, lab files or full logs are included.

## In-place update check (October 2026)

An existing Everything container created before the English-language scripts was updated with `./kali.sh update` and then `./kali.sh update --packages`. The **container ID did not change**, `kali-linux-everything` remained installed (`2026.4.2`), and the remaining upgradable package (`libsemanage2`) was upgraded in place. No image was pulled and Everything was not reinstalled. Afterward, `dpkg --audit` was empty, `apt list --upgradable` showed no pending packages, the rootless/security audit and stop/start persistence checks passed, and `nmap --unprivileged -sT -Pn -p 1-5 127.0.0.1` succeeded. No external target or VPN was tested.

The first attempt exposed a legacy issue: the running `conmon` process had inherited the operation lock from an older script. After confirming no installation or shell was active, stopping the managed container released the lock; a later start/update with the corrected script left the lock free even while Kali remained running. Future maintenance refuses to interrupt detected active container processes. The stop timeout still caused Podman to send SIGKILL to the `sleep` main process; this did not lose installed packages, but stateful services must be shut down cleanly before stopping Kali. These results demonstrate an in-place update, not a promise that future APT Rolling upgrades will always be small.

## Ping, Nmap and VPN diagnostics (October 2026)

`iputils-ping` was missing even with Everything installed. It was installed into the existing container and `ping` succeeded both to loopback and to the active VPN gateway from Kali. ICMP reachability outside the container remains dependent on the rootless network and remote endpoint. Nmap itself was installed: default execution as container UID 0 tried raw sockets and failed with `Operation not permitted`, while `--unprivileged -sT -Pn` succeeded against loopback. Setting `NMAP_UNPRIVILEGED=1` made Nmap's ordinary TCP scan work without changing capabilities; `./kali.sh shell` now sets it for new interactive shells.

The active Fedora VPN had not been imported with `htb.sh`, so `./htb.sh status` could not manage or verify that connection. Fedora routed an authorized target over the VPN but a TCP connection to its stated port failed on **Fedora as well as Kali**; Nmap inside Kali reported the port filtered. A VPN gateway ping worked from both sides. A short Fedora `tracepath` returned `!H` (host unreachable) **from the VPN gateway** for the selected target. Even after the user confirmed the target and lab VPN, this test did **not** establish target reachability: the gateway or upstream lab path reported it unavailable. The target address and personal VPN details are not recorded here. Restarting Kali with the VPN connected did not restore target reachability; changing container privileges or Fedora routes cannot fix a gateway-reported unreachable target.

Testing `apt-get --assume-no ... install iputils-ping` exposed a planning bug: APT installed this small package without prompting. The plan path now uses **`apt-get --simulate`** instead and budgets from candidate package metadata; a simulation of an uninstalled package and `./kali.sh plan everything-full` were checked without installing it. The `sleep` PID 1 does not reap child processes, so `podman top` can show zombies after APT operations; the active-work guard skips zombie processes but still refuses to restart for live shells or services.

## Installation observed

The persistent `kali` container used the official base image pinned in `image.lock` and installed `kali-linux-everything` with APT recommendations (`./kali.sh install everything-full`). The metapackage version was `2026.4.2`; 4,315 packages were installed. The writable container layer was about 37.25 GiB. The preceding plan estimated 4,192 new packages, 12.76 GiB of downloads and 36.23 GiB of additional installed size. The base image was about 239 MiB. These numbers can change considerably with Kali Rolling, architecture, filesystems, snapshots and APT updates. Check your own `plan everything-full` and available storage before installation. The installer requires at least 20 GiB to remain free after its estimated budget.

Package post-install scripts were prevented from automatically starting services by checking `policy-rc.d`. An installed package does not mean its GUI, service, database, firmware or hardware works without extra setup.

## Checks performed

- `dpkg --audit` found no incomplete packages. Kali APT, DNS resolution and HTTPS with TLS verification to `kali.org` worked. `ip -brief link show lo` succeeded.
- The `kali-linux-everything` metapackage and representative commands including `nmap`, `tcpdump`, `tshark`, `sqlmap`, `nikto`, `gobuster`, `ffuf`, `gdb` and `yara` were present. This is not a test of every tool.
- Nmap's packaged file capabilities prevented running it under this container's `no-new-privileges` and limited capabilities. Removing only those capabilities from `/usr/lib/nmap/nmap` inside Kali allowed `nmap --unprivileged -sT -Pn -p 1-5 127.0.0.1` to run without raw sockets. The installer and `./kali.sh update` perform this adjustment; after manually upgrading Nmap, run `./kali.sh update` to reapply it (or use `podman exec kali setcap -r /usr/lib/nmap/nmap`).
- The container audit passed for the pinned image, rootless mapping, private namespaces, `pasta`, no published ports or extra devices, limited capabilities, SELinux, seccomp, `no-new-privileges`, and the private `lab/` bind mount.
- Stopping and restarting Kali preserved the metapackage and temporary files in both the container layer and `/lab`; test files were then removed. The test scan targeted only loopback, not an external host.

## Limits

`/usr/bin/sleep` as PID 1 did not respond to SIGTERM in this test; Podman used SIGKILL after its stop timeout. Shut down stateful services cleanly before stopping Kali. Raw sockets, network administration, host live capture, Wi-Fi monitor/injection, Bluetooth, SDR, GPU, GUI integration, Fedora reboot, export/import restoration, other architectures, and future Rolling rebuilds were not validated. A personal HTB VPN profile was not imported and real HTB connectivity was not tested.

For a local check on your own system:

```bash
./kali.sh status
./kali.sh inventory
podman --remote=false exec kali dpkg-query -W kali-linux-everything
podman --remote=false exec kali bash -c 'dpkg --audit'
podman --remote=false exec kali nmap --unprivileged -sT -Pn -p 1-5 127.0.0.1
```

Local plans, inventories and logs are kept under `.state/` and are not committed.
