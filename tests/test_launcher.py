"""Exercise real process/session and lock behavior with an isolated dummy GUI."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


class Launcher(unittest.TestCase):
    def test_detaches_survives_shell_hangup_and_prevents_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(Path(__file__).resolve().parents[1] / 'launcher.py', root / 'launcher.py')
            (root / 'main.py').write_text(
                "import os,json,time\n"
                "from pathlib import Path\n"
                "Path('identity.json').write_text(json.dumps({'pid':os.getpid(),'sid':os.getsid(0)}))\n"
                "time.sleep(60)\n")
            environment = {**os.environ, 'XDG_CACHE_HOME': str(root / 'cache')}
            child_pid = None
            with (root / 'shell.log').open('w') as output:
                shell = subprocess.Popen(
                    ['bash', '-c', '"$1" "$2"; sleep 60', 'launcher-test', sys.executable, str(root / 'launcher.py')],
                    stdout=output, stderr=output, env=environment, start_new_session=True)
            try:
                deadline = time.monotonic() + 5
                identity = root / 'identity.json'
                while not identity.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                info = json.loads(identity.read_text())
                child_pid = info['pid']
                self.assertNotEqual(info['sid'], shell.pid)
                os.killpg(shell.pid, signal.SIGHUP)
                shell.wait(timeout=3)
                os.kill(child_pid, 0)  # The detached GUI remains alive.
                duplicate = subprocess.run([sys.executable, str(root / 'launcher.py')],
                    env=environment, capture_output=True, text=True, timeout=3)
                self.assertEqual(duplicate.returncode, 0)
                self.assertIn('已在后台运行', duplicate.stdout)
                self.assertEqual(json.loads(identity.read_text())['pid'], child_pid)
            finally:
                if shell.poll() is None:
                    os.killpg(shell.pid, signal.SIGTERM)
                    shell.wait(timeout=3)
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass


if __name__ == '__main__':
    unittest.main()
