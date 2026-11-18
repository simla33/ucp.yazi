"""Ephemeral loopback OpenSSH server exposing only a private filesystem.

No host SSH configuration, credentials, agent, or authorized_keys are used.
The caller owns process teardown and the temporary directory (IntegrationTest).
"""
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import subprocess
import unittest

from .integration import eventually


class LocalSftp:
    def __init__(self, test):
        missing = [name for name in ("sshd", "ssh-keygen", "bwrap") if not shutil.which(name)]
        if missing:
            raise unittest.SkipTest("real SFTP requires " + ", ".join(missing))
        self.test = test
        if os.getuid() == 0:
            raise unittest.SkipTest("private SFTP fixture requires an unprivileged account")
        self.root = test.root / "sftp"
        self.root.mkdir(mode=0o700)
        self.files = self.root / "files"
        self.files.mkdir(mode=0o700)
        self.log = self.root / "sshd.log"
        self.user = pwd.getpwuid(os.getuid()).pw_name
        self.host_key = self.root / "host_key"
        self.client_key = self.root / "client_key"
        for key in (self.host_key, self.client_key):
            result = subprocess.run(
                [shutil.which("ssh-keygen"), "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                env=test.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
            )
            if result.returncode:
                raise AssertionError("ephemeral SSH key generation failed: " + result.stderr.decode(errors="replace"))
        authorized = self.root / "authorized_keys"
        authorized.write_bytes(b'restrict,from="127.0.0.1" ' + self.client_key.with_suffix(".pub").read_bytes())
        authorized.chmod(0o600)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = reservation.getsockname()[1]
        config = self.root / "sshd_config"
        config.write_text("\n".join([
            "ListenAddress 127.0.0.1", "Port %d" % self.port,
            "HostKey " + str(self.host_key), "PidFile " + str(self.root / "sshd.pid"),
            "AuthorizedKeysFile " + str(authorized), "AllowUsers " + self.user,
            "AuthenticationMethods publickey", "PubkeyAuthentication yes", "StrictModes yes",
            "PasswordAuthentication no", "KbdInteractiveAuthentication no", "UsePAM no",
            "PermitRootLogin no", "PermitUserRC no", "PermitUserEnvironment no",
            "DisableForwarding yes", "X11Forwarding no", "PrintMotd no", "PrintLastLog no",
            "LogLevel VERBOSE", "Subsystem sftp internal-sftp",
            "ForceCommand internal-sftp -e -l INFO -d " + str(self.files), "",
        ]))
        # A minimal mount tree hides every host home, /root, /run and /tmp.
        # Networking is shared only to reach this loopback-bound random port.
        argv = [shutil.which("bwrap"), "--die-with-parent", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
                "--ro-bind", "/usr", "/usr",
                "--dir", "/etc", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
                "--dir", "/run/sshd", "--dir", "/var/empty",
                "--dir", pwd.getpwuid(os.getuid()).pw_dir,
                "--bind", str(test.root), str(test.root)]
        for name in ("passwd", "group", "nsswitch.conf", "ld.so.cache", "localtime"):
            path = Path("/etc") / name
            if path.exists():
                argv += ["--ro-bind", str(path), str(path)]
        for name in ("bin", "lib", "lib64"):
            path = Path("/") / name
            if path.is_symlink():
                argv += ["--symlink", os.readlink(path), str(path)]
            elif path.is_dir():
                argv += ["--ro-bind", str(path), str(path)]
        argv += ["--", shutil.which("sshd"), "-D", "-e", "-f", str(config)]
        env = dict(test.env)
        env.pop("SSH_AUTH_SOCK", None)
        env.pop("SSH_AGENT_PID", None)
        with self.log.open("wb") as output:
            self.server = test.processes.spawn(argv, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=output)

        def ready():
            if self.server.poll() is not None:
                raise RuntimeError(self.diagnostics())
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2) as connection:
                    return connection.recv(128).startswith(b"SSH-2.0-OpenSSH")
            except OSError:
                return False
        try:
            eventually(ready, "private SFTP server did not start", timeout=5)
        except RuntimeError as exc:
            raise unittest.SkipTest("isolated sshd unavailable: " + str(exc)) from exc
        except AssertionError as exc:
            raise AssertionError(str(exc) + "\n" + self.diagnostics()) from exc
        # Explicit key_file avoids consulting the user's SSH agent or config.
        test.env.pop("SSH_AUTH_SOCK", None)
        test.env.pop("SSH_AGENT_PID", None)
        (test.config / "vfs.toml").write_text(
            '[sftp.ucp-real]\nhost = "127.0.0.1"\nuser = %s\nport = %d\nkey_file = %s\n'
            % (json.dumps(self.user), self.port, json.dumps(str(self.client_key))))
        ssh_home = Path(test.env["HOME"]) / ".ssh"
        ssh_home.mkdir(mode=0o700)
        public = self.host_key.with_suffix(".pub").read_text().split()
        (ssh_home / "known_hosts").write_text("[127.0.0.1]:%d %s %s\n" % (self.port, public[0], public[1]))

    @property
    def url(self):
        return "sftp://ucp-real/" + str(self.files)

    def diagnostics(self):
        return self.log.read_text(errors="replace") if self.log.exists() else "no sshd log"

    def assert_authenticated(self):
        self.test.assertIn("Accepted publickey for " + self.user, self.diagnostics())
        self.test.assertIn("internal-sftp", self.diagnostics())
