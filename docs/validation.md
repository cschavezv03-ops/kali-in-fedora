# Historical Kali Everything validation

These are **example observations**, not minimum requirements, expected results for other users, or a guarantee that every Kali tool works. Recorded on Fedora 44 x86_64 in October 2026 with the Kali Rolling repositories available at the time. No VPN credentials, private addresses, lab files or full logs are included.

## Installation observed

The persistent `kali` container used the official base image pinned in `image.lock` and installed `kali-linux-everything` with APT recommendations (`./kali.sh install everything-full`). The metapackage version was `2026.4.2`; 4,315 packages were installed. The writable container layer was about 37.25 GiB. The preceding plan estimated 4,192 new packages, 12.76 GiB of downloads and 36.23 GiB of additional installed size. The base image was about 239 MiB. These numbers can change considerably with Kali Rolling, architecture, filesystems, snapshots and APT updates. Check your own `plan everything-full` and available storage before installation. The installer requires at least 20 GiB to remain free after its estimated budget.

Package post-install scripts were prevented from automatically starting services by checking `policy-rc.d`. An installed package does not mean its GUI, service, database, firmware or hardware works without extra setup.

## Checks performed

- `dpkg --audit` found no incomplete packages. Kali APT, DNS resolution and HTTPS with TLS verification to `kali.org` worked. `ip -brief link show lo` succeeded.
- The `kali-linux-everything` metapackage and representative commands including `nmap`, `tcpdump`, `tshark`, `sqlmap`, `nikto`, `gobuster`, `ffuf`, `gdb` and `yara` were present. This is not a test of every tool.
- Nmap's packaged file capabilities prevented running it under this container's `no-new-privileges` and limited capabilities. Removing only those capabilities from `/usr/lib/nmap/nmap` inside Kali allowed `nmap --unprivileged -sT -Pn -p 1-5 127.0.0.1` to run without raw sockets. The installer performs this adjustment; manually updating Nmap may require `podman exec kali setcap -r /usr/lib/nmap/nmap` again.
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
