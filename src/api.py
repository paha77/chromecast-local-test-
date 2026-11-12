"""Local authenticated control service; the existing launcher owns Cast."""
import argparse
import hmac
import json
import os
import pathlib
import re
import secrets
import shutil
import signal
import subprocess
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parent.parent
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}\.mp4\Z")


class Listener:
    def __init__(self, root=ROOT):
        self.root = root
        self.lock = threading.RLock()
        self.process = None
        self.state = "stopped"
        self.recording = None
        self.error = None
        self.ready = False
        self.filename = None

    def status(self):
        with self.lock:
            current = dict(self.recording) if self.recording else None
            if current:
                path = self.root / "recordings" / current["id"].removesuffix(".mp4") / "incoming.mkv"
                try:
                    current["bytes"] = path.stat().st_size
                except FileNotFoundError:
                    current["bytes"] = 0
            return {"state": self.state, "recording": current, "error": self.error}

    def start(self, filename=None):
        if filename is not None and (not isinstance(filename, str) or not NAME.fullmatch(filename)):
            raise ValueError("filename must be an MP4 basename with 1-120 letters, digits, underscores or hyphens")
        with self.lock:
            if self.state in ("starting", "listening"):
                if filename is not None and filename != self.filename:
                    raise RuntimeError("Listener is already running with a different filename; stop it before choosing a new filename")
                return
            if self.state == "stopping":
                raise RuntimeError("Listener is still finalizing; wait for stopped")
            if filename and any(os.path.lexists(self.root / "recordings" / name)
                                for name in (filename, filename.removesuffix(".mp4"))):
                raise RuntimeError("Recording filename already exists")
            env = dict(os.environ)
            env.pop("CAST_RECORDING_FILENAME", None)
            if filename:
                env["CAST_RECORDING_FILENAME"] = filename
            self.process = subprocess.Popen(
                ["/bin/bash", str(self.root / "start.sh")], cwd=self.root,
                env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, start_new_session=True)
            self.state, self.error, self.recording = "starting", None, None
            self.filename = filename
            self.ready = False
            self.watcher = threading.Thread(target=self._watch, args=(self.process,), daemon=True)
            self.watcher.start()

    def _watch(self, process):
        last = None
        started = False
        for line in process.stdout:
            try:
                print(line, end="", flush=True)
            except OSError:
                pass
            with self.lock:
                last = line.strip()
                if last == "Receiver supervisor ready":
                    self.ready = True
                    if self.state == "stopping" and process.poll() is None:
                        process.terminate()
                if line.strip() == "Recording started":
                    started = True
                if "Waiting for Cast connection" in line and self.state == "starting":
                    self.state = "listening"
                match = re.fullmatch(r'Output filename: "?recordings/([^"\n]+)"?', line.strip())
                if match and NAME.fullmatch(match.group(1)) and started:
                    started = False
                    name = match.group(1)
                    self.recording = {"id": name, "state": "recording"}
                if "Finalizing recording" in line and self.recording:
                    self.recording["state"] = "finalizing"
                if "Error recording:" in line or "Error finalizing MP4" in line:
                    self.error = last
                    if self.recording:
                        self.recording["state"] = "failed"
                if "Recording stopped" in line:
                    self.recording = None
        code = process.wait()
        process.stdout.close()
        with self.lock:
            self.state = "stopped" if code == 0 else "failed"
            if code:
                self.error = last or f"Launcher exited with status {code}"
            self.recording = None

    def stop(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None and self.state != "stopping":
                self.state = "stopping"
                if self.ready:
                    try:
                        self.process.terminate()
                    except ProcessLookupError:
                        pass

    def wait(self):
        if self.process:
            self.watcher.join()

    def recordings(self):
        items = []
        for path in sorted((self.root / "recordings").glob("*.mp4")):
            if self.completed(path.name):
                info = path.stat()
                items.append({"id": path.name, "bytes": info.st_size,
                              "modifiedAt": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
                              "downloadUrl": f"/v1/recordings/{path.name}"})
        return items

    def completed(self, name):
        path = self.root / "recordings" / name
        return bool(NAME.fullmatch(name) and not path.is_symlink() and path.is_file()
                    and not (path.parent / name.removesuffix(".mp4")).exists())


def handler(listener, token):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def reply(self, code, value):
            data = json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
                self.reply(401, {"error": "Bearer token required"})
                return False
            return True

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/openapi.json":
                self.reply(200, json.loads((ROOT / "src/openapi.json").read_text()))
                return
            if not self.authorized():
                return
            if path == "/v1/listener":
                self.reply(200, listener.status())
            elif path == "/v1/recordings":
                self.reply(200, {"recordings": listener.recordings()})
            elif path.startswith("/v1/recordings/"):
                name = path.removeprefix("/v1/recordings/")
                if not listener.completed(name):
                    self.reply(404, {"error": "Completed recording not found"})
                    return
                with (listener.root / "recordings" / name).open("rb") as source:
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp4")
                    self.send_header("Content-Length", str(source.seek(0, 2)))
                    self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                    self.end_headers()
                    source.seek(0)
                    shutil.copyfileobj(source, self.wfile)
            else:
                self.reply(404, {"error": "Unknown endpoint"})

        def do_POST(self):
            if not self.authorized():
                return
            path = urlsplit(self.path).path
            try:
                if path == "/v1/listener/start":
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 0 or length > 4096:
                        raise ValueError("Request body must be at most 4096 bytes")
                    body = json.loads(self.rfile.read(length)) if length else {}
                    if not isinstance(body, dict):
                        raise ValueError("Request body must be a JSON object")
                    if "filename" in body and body["filename"] is None:
                        raise ValueError("filename must be a string")
                    listener.start(body.get("filename"))
                elif path == "/v1/listener/stop":
                    listener.stop()
                else:
                    self.reply(404, {"error": "Unknown endpoint"})
                    return
                self.reply(202, listener.status())
            except (ValueError, UnicodeError) as error:
                self.reply(400, {"error": str(error)})
            except (OSError, RuntimeError) as error:
                self.reply(409, {"error": str(error)})

        def log_request(self, code="-", size="-"):
            print(json.dumps({"time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                              "client": self.client_address[0],
                              "method": self.command,
                              "path": urlsplit(self.path).path,
                              "status": code}), flush=True)

        def log_message(self, format, *args):
            pass  # Never log authorization headers or client-supplied paths.

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8020)
    args = parser.parse_args()
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    token_path = runtime / "api.token"
    try:
        with open(token_path, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as output:
            output.write(secrets.token_urlsafe(32) + "\n")
    except FileExistsError:
        pass
    token = token_path.read_text().strip()
    if not token:
        raise RuntimeError(f"Empty token file: {token_path}")
    listener = Listener()
    server = ThreadingHTTPServer((args.host, args.port), handler(listener, token))
    server.daemon_threads = True

    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)
    print(f"Control API listening on {args.host}:{args.port}; token file: {token_path}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        listener.stop()
        listener.wait()


if __name__ == "__main__":
    main()
