"""Exercise real process-group Ctrl+C delivery while a child finalizes."""
import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time

root = pathlib.Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(dir=root / ".runtime") as directory:
    work = pathlib.Path(directory)
    lock = work / "lock"
    lock.mkdir()
    receiver = work / "receiver.py"
    receiver.write_text('''import pathlib, signal, time, sys
p = pathlib.Path(sys.argv[1])
def stop(*args):
    (p / 'finalizing').touch()
    time.sleep(0.7)
    (p / 'finalized').touch()
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
(p / 'receiver-ready').touch()
while True: time.sleep(0.1)
''')
    publisher = work / "publisher.py"
    publisher.write_text('''import pathlib, signal, time, sys
signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))
pathlib.Path(sys.argv[1]).touch()
while True: time.sleep(0.1)
''')
    command = (
        'import sys, pathlib; sys.path.insert(0, sys.argv[1]); '
        'from run import supervise; '
        'sys.exit(supervise([sys.executable, sys.argv[2], sys.argv[4]], '
        '[sys.executable, sys.argv[3], sys.argv[5] + "/bonjour.ready"], '
        'pathlib.Path(sys.argv[5])))')
    process = subprocess.Popen(
        [sys.executable, "-c", command, str(root / "src"), str(receiver),
         str(publisher), str(work), str(lock)],
        start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True)

    def wait_for(path):
        deadline = time.monotonic() + 5
        while not path.exists():
            assert process.poll() is None, "supervisor exited prematurely"
            assert time.monotonic() < deadline, f"timed out waiting for {path}"
            time.sleep(0.02)

    try:
        wait_for(lock / "bonjour.ready")
        # Send to the terminal process group, not merely the supervisor PID.
        os.killpg(process.pid, signal.SIGINT)
        wait_for(work / "finalizing")
        os.killpg(process.pid, signal.SIGINT)
        output, _ = process.communicate(timeout=5)
        assert process.returncode == 0, output
        assert (work / "finalized").exists(), output
        assert not lock.exists(), output
        print("PASS: Ctrl+C, repeated Ctrl+C during finalization, child exit and lock cleanup")
    finally:
        if process.poll() is None:
            for name in ("receiver.pid", "bonjour.pid"):
                path = lock / name
                if path.exists():
                    try:
                        os.kill(int(path.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            process.kill()
            process.wait()
