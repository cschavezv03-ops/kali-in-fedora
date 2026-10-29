"""Safety regression tests; use a fake engine, never the real Podman storage."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "kali.sh"


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kali-safety-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "project"
        self.repo.mkdir()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        shutil.copy2(SCRIPT, self.repo / "kali.sh")
        self.lab = self.repo / "lab"
        self.lab.mkdir(mode=0o700)
        self.calls = self.base / "calls.jsonl"
        # Keep tests independent of disk capacity (e.g. /tmp mounted as tmpfs).
        (self.base / "sitecustomize.py").write_text(
            "import os, shutil\n"
            "shutil.disk_usage=lambda _: shutil._ntuple_diskusage(200*2**30,100*2**30,int(os.environ.get('TEST_FREE_BYTES',100*2**30)))\n"
        )
        self.fixture = self.base / "inspect.json"
        self.data = {
            "Config": {"Labels": {"io.github.kali-in-fedora.managed": "1"}},
            "HostConfig": {
                "Privileged": False, "AutoRemove": False, "NetworkMode": "pasta",
                "SecurityOpt": ["no-new-privileges"], "PidMode": "private",
                "IpcMode": "private",
            },
            "State": {"Status": "exited"},
            "ImageName": "docker.io/kalilinux/kali-rolling@sha256:" + "a" * 64,
            "EffectiveCaps": ["CAP_CHOWN"],
            "Mounts": [{"Type": "bind", "Source": str(self.lab),
                        "Destination": "/lab", "RW": True}],
        }
        engine = self.bin / "podman"
        engine.write_text("""#!/usr/bin/env python3
import json,os,sys
a=sys.argv[1:]
with open(os.environ['TEST_CALLS'],'a') as f: f.write(json.dumps(a)+'\\n')
assert a.pop(0)=='--remote=false', 'must use local engine'
if a[:1]==['info']:
 if a[-1]=='json': print(json.dumps({'host':{'security':{'rootless':True,'seccompEnabled':True,'selinuxEnabled':True}},'store':{}}))
 elif a[-1]=='{{.Store.GraphRoot}}': print(os.environ['TEST_ROOT'])
 else: sys.exit(90)
elif a[:2]==['container','exists']:
 sys.exit(1 if os.environ.get('TEST_MISSING') else 0)
elif a[:1]==['inspect'] and '--format' not in a:
 print('['+open(os.environ['TEST_FIXTURE']).read()+']')
elif a[:1]==['pull']: pass
elif a[:1]==['create']: sys.exit(77) # stop before any simulated setup mutation
else: sys.exit('Unexpected mutation or command: '+repr(a))
""")
        engine.chmod(0o700)
        enforcing = self.bin / "getenforce"
        enforcing.write_text("#!/bin/sh\nprintf 'Enforcing\\n'\n")
        enforcing.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        TEST_CALLS=str(self.calls), TEST_FIXTURE=str(self.fixture),
                        TEST_ROOT=str(self.base), PYTHONPATH=str(self.base))
        for key in ("CONTAINER_HOST", "CONTAINER_CONNECTION", "CONTAINERS_CONF",
                    "CONTAINERS_STORAGE_CONF", "CONTAINERS_REGISTRIES_CONF", "PODMAN_USERNS"):
            self.env.pop(key, None)

    def invoke(self, command):
        self.fixture.write_text(json.dumps(self.data))
        result = subprocess.run(["bash", str(self.repo / "kali.sh"), command],
                                capture_output=True, text=True, env=self.env,
                                cwd=self.repo)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def no_mutations(self):
        calls = [json.loads(line)[1:] for line in self.calls.read_text().splitlines()]
        self.assertTrue(all(call[0] in ("info", "inspect", "container") for call in calls), calls)

    def test_foreign_container_is_not_adopted_or_stopped(self):
        self.data["Config"]["Labels"] = {}
        result = self.invoke("setup")
        self.assertIn("FALLO propiedad_proyecto", result.stdout)
        self.no_mutations()

    def test_extra_mount_blocks_access(self):
        self.data["Mounts"].append({"Source": "/unexpected", "Destination": "/extra"})
        result = self.invoke("shell")
        self.assertIn("FALLO solo_lab", result.stdout)
        self.no_mutations()

    def test_host_network_blocks_access(self):
        self.data["HostConfig"]["NetworkMode"] = "host"
        self.assertIn("FALLO red_pasta", self.invoke("start").stdout)
        self.no_mutations()

    def test_remove_without_interactive_confirmation_never_deletes(self):
        self.assertIn("terminal interactiva", self.invoke("remove").stderr)
        self.no_mutations()

    def test_lab_symlink_blocks_reuse(self):
        self.lab.rmdir()
        target = self.base / "other"
        target.mkdir(mode=0o700)
        self.lab.symlink_to(target, target_is_directory=True)
        self.assertIn("FALLO lab_privado", self.invoke("start").stdout)
        self.no_mutations()

    def test_os_release_cannot_replace_kali_container_name(self):
        self.env["TEST_MISSING"] = "1"
        (self.repo / "image.lock").write_text(self.data["ImageName"] + "\n")
        self.invoke("setup")
        calls = [json.loads(line)[1:] for line in self.calls.read_text().splitlines()]
        create = next(call for call in calls if call[0] == "create")
        self.assertEqual(create[create.index("--name") + 1], "kali")

    def test_insufficient_disk_does_not_create_container(self):
        self.env["TEST_MISSING"] = "1"
        self.env["TEST_FREE_BYTES"] = str(21 * 2**30)
        self.assertIn("reserva de 20 GiB", self.invoke("setup").stdout)
        self.no_mutations()


if __name__ == "__main__":
    unittest.main()
