"""Own the receiver and Bonjour publisher, including graceful signal handling."""
import pathlib
import signal
import subprocess
import sys
import threading
import time


def supervise(receiver_command, bonjour_command, lock):
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)
    print("Receiver supervisor ready", flush=True)
    receiver = publisher = None
    receiver_log = reader = None
    try:
        # Separate process groups keep terminal Ctrl+C away from the children
        # while retaining the launching terminal's session on macOS.
        receiver_log = (lock.parent / "receiver.log").open("a", buffering=1)
        receiver = subprocess.Popen(receiver_command, process_group=0,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1)

        def copy_output():
            for line in receiver.stdout:
                receiver_log.write(line)
                try:
                    print(line, end="", flush=True)
                except OSError:
                    pass  # A closed terminal must not interrupt finalization.

        reader = threading.Thread(target=copy_output, daemon=True)
        reader.start()
        (lock / "receiver.pid").write_text(str(receiver.pid))
        time.sleep(0.2)
        if receiver.poll() is not None:
            raise RuntimeError(f"receiver exited ({receiver.returncode})")
        if stopping:
            return 0
        publisher = subprocess.Popen(bonjour_command, process_group=0)
        (lock / "bonjour.pid").write_text(str(publisher.pid))
        deadline = time.monotonic() + 10
        announced = False
        while not stopping:
            if receiver.poll() is not None:
                raise RuntimeError(f"receiver exited ({receiver.returncode})")
            if publisher.poll() is not None:
                raise RuntimeError(f"Bonjour publisher exited ({publisher.returncode})")
            if not announced:
                if (lock / "bonjour.ready").exists():
                    print("Receiver started\nWaiting for Cast connection", flush=True)
                    announced = True
                elif time.monotonic() >= deadline:
                    raise RuntimeError("Bonjour registration timed out")
            time.sleep(0.1)
        return 0
    except (OSError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        print("Stopping receiver; waiting for MP4 finalization...", flush=True)
        # Keep the handler installed while waiting: repeated Ctrl+C must not
        # interrupt trailer writing or kill the FFmpeg conversion.
        for process in (publisher, receiver):
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
        if publisher is not None:
            publisher.wait()
        if receiver is not None:
            receiver.wait()
        if reader is not None:
            reader.join()
        if receiver_log is not None:
            receiver_log.close()
        for name in ("pid", "receiver.pid", "bonjour.pid", "bonjour.ready"):
            (lock / name).unlink(missing_ok=True)
        lock.rmdir()
        print("Receiver stopped", flush=True)


if __name__ == "__main__":
    import hashlib
    root = pathlib.Path(__file__).resolve().parent.parent
    lock = root / ".runtime/receiver.lock"
    cert = root / ".runtime/certs/generated_root_cast_receiver.crt"
    receiver_id = hashlib.sha256(cert.read_bytes()).hexdigest()[:32]
    print(f"Starting receiver on {sys.argv[1]} (TCP 8010, native Bonjour)", flush=True)
    sys.exit(supervise(
        [str(root / ".deps/openscreen/out/Recorder/cast_receiver"),
         "-d", str(cert), "-p", str(cert.with_suffix(".key")),
         "-f", "Local Recorder", "-x", "-q", sys.argv[1]],
        [str(root / ".runtime/bonjour"), sys.argv[1], receiver_id,
         str(lock / "bonjour.ready")], lock))
