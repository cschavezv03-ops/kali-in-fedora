"""Safety regression tests; use a fake engine, never the real Podman storage."""
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "kali.sh"
APT_PLAN = SCRIPT.parent / "lib" / "apt_plan.py"


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
        (self.repo / "lib").mkdir()
        shutil.copy2(APT_PLAN, self.repo / "lib" / "apt_plan.py")
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
try: os.fstat(9)
except OSError: pass
else: sys.exit('operation lock descriptor leaked to Podman')
if a[:1]==['info']:
 if a[-1]=='json': print(json.dumps({'host':{'security':{'rootless':True,'seccompEnabled':True,'selinuxEnabled':True}},'store':{}}))
 elif a[-1]=='{{.Store.GraphRoot}}': print(os.environ['TEST_ROOT'])
 else: sys.exit(90)
elif a[:2]==['container','exists']:
 sys.exit(1 if os.environ.get('TEST_MISSING') else 0)
elif a[:1]==['inspect'] and '--format' not in a:
  print('['+open(os.environ['TEST_FIXTURE']).read()+']')
elif a[:1]==['inspect'] and os.environ.get('TEST_UPDATE'):
  assert a[2]=='{{.State.Status}}'
  print('running')
elif a[:1]==['top'] and os.environ.get('TEST_UPDATE'):
  assert a[1:]==['kali','pid','state','tty','comm']
  print('PID STATE TTY COMMAND\\n1 S ? sleep\\n66 Z ? sh')
  if os.environ.get('TEST_ACTIVE'): print('19 S pts/0 bash')
elif a[:1]==['exec'] and os.environ.get('TEST_UPDATE'):
  if 'apt-get' in a:
   command=a[a.index('apt-get')+1:]
   if '--simulate' in command:
    if 'upgrade' in command: print('1 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.')
    else: print('0 upgraded, 1 newly installed, 0 to remove and 0 not upgraded.')
    print('Inst iputils-ping (3:20250605-1 kali-rolling [amd64])')
    sys.exit(0)
   assert '-o' in command or '--yes' in command
  elif 'apt-cache' in a:
   assert a[a.index('apt-cache')+1]=='show'
   print('Package: iputils-ping\\nVersion: 3:20250605-1\\nInstalled-Size: 193\\nSize: 52000')
  elif 'bash' in a:
   if '-ceu' in a and 'getcap' in a[-1]: sys.exit(77) # stop before validation
  else: sys.exit('Unexpected exec: '+repr(a))
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

    def invoke(self, command, *args):
        self.fixture.write_text(json.dumps(self.data))
        result = subprocess.run(["bash", str(self.repo / "kali.sh"), command, *args],
                                capture_output=True, text=True, env=self.env,
                                cwd=self.repo)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def no_mutations(self):
        calls = [json.loads(line)[1:] for line in self.calls.read_text().splitlines()]
        self.assertTrue(all(call[0] in ("info", "inspect", "container") for call in calls), calls)

    def test_help_explains_minimum_to_everything_upgrade(self):
        result = subprocess.run(["bash", str(self.repo / "kali.sh"), "help"],
                                capture_output=True, text=True, env=self.env, cwd=self.repo)
        self.assertEqual(result.returncode, 0)
        self.assertIn("setup                Create/reuse kali and install Minimum only", result.stdout)
        self.assertIn("install everything-full", result.stdout)
        self.assertIn("without\nrecreating kali", result.stdout)
        self.assertFalse(self.calls.exists())

    def test_embedded_python_blocks_compile(self):
        source = SCRIPT.read_text()
        blocks = [m.group(1) for m in re.finditer(r"python3 -c '(.*?)'", source, re.S)]
        blocks += [m.group(1) for m in re.finditer(r"python3 - <<'PY'\n(.*?)\nPY", source, re.S)]
        self.assertGreater(len(blocks), 5)
        for number, block in enumerate(blocks):
            compile(block, f"embedded Python block {number}", "exec")

    def test_update_reuses_existing_container_without_reinstalling_everything(self):
        self.env["TEST_UPDATE"] = "1"
        result = self.invoke("update")
        self.assertIn("Updating the existing kali container in place", result.stdout)
        calls = [json.loads(line)[1:] for line in self.calls.read_text().splitlines()]
        self.assertFalse(any(call[0] in ("pull", "create", "rm", "stop") for call in calls))
        apt = [call[call.index("apt-get") + 1:] for call in calls if "apt-get" in call]
        self.assertEqual(sum("--yes" in call for call in apt), 1)
        self.assertTrue(any("--simulate" in call for call in apt))
        self.assertFalse(any("--assume-no" in call for call in apt))
        self.assertTrue(any("--no-install-recommends" in call and "ca-certificates" in call for call in apt))
        self.assertFalse(any("kali-linux-everything" in call for call in apt))

    def test_plan_simulates_without_installing_packages(self):
        self.env["TEST_UPDATE"] = "1"
        self.fixture.write_text(json.dumps(self.data))
        result = subprocess.run(["bash", str(self.repo / "kali.sh"), "plan", "headless"],
                                capture_output=True, text=True, env=self.env, cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = [json.loads(line)[1:] for line in self.calls.read_text().splitlines()]
        apt = [call[call.index("apt-get") + 1:] for call in calls if "apt-get" in call]
        self.assertTrue(any("--simulate" in call for call in apt))
        self.assertFalse(any("--yes" in call or "--assume-no" in call for call in apt))
        self.assertFalse(any(call[0] in ("pull", "create", "rm", "stop") for call in calls))

    def test_shell_uses_unprivileged_nmap_defaults(self):
        self.env["TEST_UPDATE"] = "1"
        self.fixture.write_text(json.dumps(self.data))
        result = subprocess.run(["bash", str(self.repo / "kali.sh"), "shell"],
                                capture_output=True, text=True, env=self.env, cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = [json.loads(line)[1:] for line in self.calls.read_text().splitlines()]
        guest = next(call for call in calls if call[0] == "exec")
        self.assertIn("NMAP_UNPRIVILEGED=1", guest)
        self.assertFalse(any(call[0] in ("pull", "create", "rm", "stop") for call in calls))

    def test_package_upgrade_is_opt_in_and_uses_no_remove(self):
        self.env["TEST_UPDATE"] = "1"
        self.invoke("update", "--packages")
        calls = [json.loads(line)[1:] for line in self.calls.read_text().splitlines()]
        apt = [call[call.index("apt-get") + 1:] for call in calls if "apt-get" in call]
        self.assertTrue(any("--simulate" in call and "upgrade" in call for call in apt))
        self.assertTrue(any("--yes" in call and "--no-remove" in call and "upgrade" in call for call in apt))
        self.assertFalse(any(call[0] in ("pull", "create", "rm", "stop") for call in calls))
        self.assertFalse(any("kali-linux-everything" in call for call in apt))

    def test_update_rejects_foreign_container_before_package_changes(self):
        self.env["TEST_UPDATE"] = "1"
        self.data["Config"]["Labels"] = {}
        self.assertIn("FAIL managed_by_project", self.invoke("update").stdout)
        self.no_mutations()

    def test_update_does_not_interrupt_an_interactive_shell(self):
        self.env["TEST_UPDATE"] = "1"
        self.env["TEST_ACTIVE"] = "1"
        self.assertIn("Active Kali processes: PID 19 (bash)", self.invoke("update").stdout)
        calls = [json.loads(line)[1:] for line in self.calls.read_text().splitlines()]
        self.assertFalse(any(call[0] in ("exec", "stop", "rm", "create") for call in calls))

    def test_update_never_creates_a_missing_container(self):
        self.env["TEST_UPDATE"] = "1"
        self.env["TEST_MISSING"] = "1"
        self.assertIn("Run ./kali.sh setup first", self.invoke("update").stdout)
        self.no_mutations()

    def test_update_does_not_bypass_an_existing_operation_lock(self):
        self.env["TEST_UPDATE"] = "1"
        state = self.repo / ".state"
        state.mkdir(mode=0o700)
        with (state / "operation.lock").open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.invoke("update")
        self.assertIn("Do not delete the lock file", result.stderr)
        self.no_mutations()

    def test_update_rejects_unknown_options(self):
        self.env["TEST_UPDATE"] = "1"
        self.assertIn("Usage: ./kali.sh update [--packages]", self.invoke("update", "--force").stdout)
        calls = [json.loads(line)[1:] for line in self.calls.read_text().splitlines()]
        self.assertFalse(any(call[0] in ("pull", "create", "rm", "exec") for call in calls))

    def test_foreign_container_is_not_adopted_or_stopped(self):
        self.data["Config"]["Labels"] = {}
        result = self.invoke("setup")
        self.assertIn("FAIL managed_by_project", result.stdout)
        self.no_mutations()

    def test_extra_mount_blocks_access(self):
        self.data["Mounts"].append({"Source": "/unexpected", "Destination": "/extra"})
        result = self.invoke("shell")
        self.assertIn("FAIL only_lab_mounted", result.stdout)
        self.no_mutations()

    def test_host_network_blocks_access(self):
        self.data["HostConfig"]["NetworkMode"] = "host"
        self.assertIn("FAIL pasta_network", self.invoke("start").stdout)
        self.no_mutations()

    def test_remove_without_interactive_confirmation_never_deletes(self):
        self.assertIn("interactive terminal", self.invoke("remove").stderr)
        self.no_mutations()

    def test_lab_symlink_blocks_reuse(self):
        self.lab.rmdir()
        target = self.base / "other"
        target.mkdir(mode=0o700)
        self.lab.symlink_to(target, target_is_directory=True)
        self.assertIn("FAIL private_lab", self.invoke("start").stdout)
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
        self.assertIn("keep 20 GiB", self.invoke("setup").stdout)
        self.no_mutations()


if __name__ == "__main__":
    unittest.main()
