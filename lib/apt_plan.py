"""Budget an APT simulation without ever running an install as part of planning."""

import json
from pathlib import Path
import re
import subprocess
import sys


SUMMARY = re.compile(r"^(\d+) upgraded, (\d+) newly installed, (\d+) to remove", re.M)
INSTALL = re.compile(r"^Inst (\S+) (?:\[.*?\] )?\((\S+)\s", re.M)


def estimate(text, metadata):
    summary = SUMMARY.search(text)
    if not summary or int(summary[3]):
        raise ValueError("APT simulation did not produce a safe plan without removals")
    planned = INSTALL.findall(text)
    if len(planned) != int(summary[1]) + int(summary[2]):
        raise ValueError("APT simulation package count does not match its summary")
    records = {}
    for stanza in re.split(r"\n\s*\n", metadata):
        fields = dict(re.findall(r"^(Package|Version|Size|Installed-Size): (.+)$", stanza, re.M))
        if set(fields) == {"Package", "Version", "Size", "Installed-Size"}:
            key = fields["Package"], fields["Version"]
            size, installed_size = int(fields["Size"]), int(fields["Installed-Size"])
            records[key] = (max(size, records.get(key, (0, 0))[0]),
                            max(installed_size, records.get(key, (0, 0))[1]))
    download = installed = 0
    for name, version in planned:
        key = name.split(":", 1)[0], version
        if key not in records:
            raise ValueError(f"No size metadata for APT candidate {name} {version}")
        size, installed_size = records[key]
        download += size
        # Charge the whole installed size even for upgrades (a safe overestimate).
        installed += installed_size * 1024
    return download, installed


def main():
    raw, report, profile = sys.argv[1:]
    text = Path(raw).read_text()
    summary = SUMMARY.search(text)
    if not summary or int(summary[3]):
        raise ValueError("APT simulation failed or would remove packages; see the plan report")
    candidates = INSTALL.findall(text)
    metadata = ""
    if candidates:
        packages = sorted({name for name, _ in candidates})
        result = subprocess.run(["podman", "--remote=false", "exec", "kali", "apt-cache", "show", *packages],
                                capture_output=True, text=True, check=True)
        metadata = result.stdout
    download, installed = estimate(text, metadata)
    budget = download + installed + 2 * 2**30
    result = {"download_bytes": download, "additional_installed_bytes": installed,
              "budget_bytes": budget, "reserve_bytes": 20 * 2**30,
              "recommends": profile == "everything-full"}
    Path(report).write_text(json.dumps(result, indent=2) + "\n")
    def size(value):
        return f"{value / 2**20:.2f} MiB" if value < 0.01 * 2**30 else f"{value / 2**30:.2f} GiB"

    print(f"APT simulation: up to {size(download)} of downloads and "
          f"{size(installed)} of installed packages; "
          f"budget with 2 GiB headroom: {budget / 2**30:.2f} GiB")
    print("Metadata counts full installed sizes, including upgrades; the estimate is conservative, not a quota.")
    print(summary[0] + ". Full package list is in the local, untracked plan report.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"APT planning failed: {exc}")
