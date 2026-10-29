# Hack The Box with rootless Kali

The optional OpenVPN connection runs **on Fedora** under NetworkManager; tools run in Kali. This avoids granting Kali `NET_ADMIN`, `NET_RAW`, TUN devices or host networking. It is not installed or activated by `./kali.sh setup`.

## Prepare and connect

1. Download your personal profile from [Connect to HTB](https://help.hackthebox.com/en/articles/5185687-introduction-to-lab-access). Keep it outside this repository and `lab/`.
2. Fedora needs `openvpn` and `NetworkManager-openvpn`. If missing, install them yourself from Fedora repositories (administrator privileges required):

   ```bash
   sudo dnf --repo=fedora --repo=updates install openvpn NetworkManager-openvpn
   ```

3. Import and connect as a regular user:

   ```bash
   chmod 600 /private/path/htb.ovpn
   ./htb.sh import /private/path/htb.ovpn
   ./htb.sh up
   ./htb.sh status
   ```

The importer requires a `dev tun` client profile with server identity verification. It rejects scripts, plugins, includes, external key files and full-tunnel options. It does not print certificates or keys. It places a private copy under `$XDG_CONFIG_HOME/kali-in-fedora/vpn/` (usually inside `~/.config/`) and creates a user-scoped NetworkManager profile without activating it, autoconnecting or changing default routes and global DNS. Server-provided private routes may be added on connection. NetworkManager/Polkit may prompt for authorization. Metadata is kept in the untracked `.state/htb-vpn.json`; do not delete it to bypass profile protection. To rotate a profile, review existing NetworkManager profiles first.

To disconnect only this project's profile, run `./htb.sh down`. Other VPN connections are not changed.

## Check the route and scan

Replace `TARGET_IP` with a private IPv4 address assigned to your authorized lab target; choose a TCP port known to be open for `check`:

```bash
./htb.sh check TARGET_IP 80
./htb.sh scan TARGET_IP 22,80,443
./htb.sh scan TARGET_IP 1-65535
```

`check` requires the selected VPN to be active and Fedora's route to the target to use its interface and VPN source address. It attempts a TCP connection to that port from both Fedora and Kali; a closed or filtered port cannot establish connectivity. `scan` checks the Fedora route and runs `nmap --unprivileged -sT -Pn -sV --reason` inside Kali. Install Everything or a suitable tool profile first; Minimum does not include Nmap. TCP connect works without raw sockets. `-Pn` skips ping discovery and does not prove a target is up; SYN/UDP scans and OS detection are not requested. Route checks are not a kill switch; keep the VPN active. If Fedora connects but Kali cannot, inspect `pasta`, routes and MTU. Restarting Kali after connecting may refresh copied networking but interrupts work and is not an automatic fix.

## Return connections: TCP relay

Outbound VPN access does not publish listeners in Kali. The helper offers a limited host-side relay:

```text
HTB target -> Fedora VPN address:4444 -> unprivileged relay
           -> podman exec -i -> socat in Kali -> 127.0.0.1:4444
```

1. Start a listener inside Kali (after installing appropriate tools):

   ```bash
   ./kali.sh shell
   nc -lvnp 4444
   ```

2. In another Fedora terminal, while the VPN is active:

   ```bash
   ./htb.sh relay TARGET_IP 4444
   ```

   Advertise the **Fedora VPN address** shown by the helper to your target, not the LAN address visible inside Kali. Listen inside Kali on loopback or `0.0.0.0`. `--container-port 5555` can direct traffic to a different internal port.

3. If the firewall blocks inbound traffic, print an optional narrowly scoped rule:

   ```bash
   ./htb.sh firewall-hint TARGET_IP 4444
   ```

   This only **prints** add/remove commands. Applying the rule is an explicit administrator decision; it permits only that source `/32`, VPN destination `/32` and TCP port for one hour. Do not disable firewalld or add broad rules.

The relay binds only to the local VPN address, accepts only the selected target, serves one TCP connection at a time and does not preserve the peer IP inside Kali. It does not support UDP, concurrent callbacks or multi-port protocols. Ctrl+C closes the relay without disconnecting the VPN or removing Kali.

## Kernel, namespaces and IP addresses

Fedora and Kali share **one Linux kernel**. Kali supplies programs and libraries in a separate filesystem; user, process, mount and network namespaces isolate processes. Container root maps to a regular Fedora user and cannot manage Fedora's physical interfaces. The `pasta` network copies addresses/routes into Kali's network namespace and translates outgoing TCP/UDP via Fedora sockets. Equal numerical IP addresses do not mean the namespaces share interfaces or sockets, nor does Kali get a separate router address. Routing and VPN behavior can vary, so test a real open port from both sides rather than inferring reachability from an IP alone.

```mermaid
flowchart LR
    K["Kali tools"] -->|"connect()"| N["Kali network namespace"]
    N -->|"TAP"| P["pasta: Fedora sockets"]
    P --> R["Fedora kernel: route to target"]
    R --> T["Fedora VPN TUN"]
    T --> O["OpenVPN encrypts traffic"]
    O --> I["Wi-Fi / Ethernet: Internet"]
    I --> H["HTB VPN server"]
    H --> M["Authorized lab target"]
```

OpenVPN creates TUN and private routes on Fedora; packets are encrypted and delivered to HTB through the host connection. The VPN does not need to appear as `tun0` inside Kali for outgoing connections to use host routing through `pasta`. Outgoing access does not make Kali listeners reachable: use the explicit relay. Actual HTB connectivity requires your own profile and target and has not been validated as part of this repository.

References: [HTB](https://help.hackthebox.com/en/articles/5185687-introduction-to-lab-access), [Podman](https://docs.podman.io/en/latest/markdown/podman-run.1.html), [pasta](https://passt.top/builds/latest/web/passt.1.html), [NetworkManager](https://networkmanager.dev/docs/api/latest/nmcli.html), [Nmap](https://nmap.org/book/man-misc-options.html), [OpenVPN](https://openvpn.net/community-docs/community-articles/openvpn-2-6-manual/).
