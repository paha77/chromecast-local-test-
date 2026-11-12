"""Control and download contract with a synthetic launcher, never Chrome."""
import http.client
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from api import Listener, handler


with tempfile.TemporaryDirectory() as directory:
    root = pathlib.Path(directory)
    (root / "recordings").mkdir()
    (root / "start.sh").write_text('''exec python3 -u -c '
import os, signal, sys, time
def stop(*args):
    print("Finalizing recording", flush=True)
    time.sleep(0.3)
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
print("Receiver supervisor ready")
print("Waiting for Cast connection")
print("Recording started")
print("Output filename: recordings/" + os.environ.get("CAST_RECORDING_FILENAME", "cast-ABC123.mp4"))
while True: time.sleep(0.01)
'
''')
    listener = Listener(root)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(listener, "test-token"))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()

    def request(method, path, token="test-token", body=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        connection.request(method, path, body=body, headers={"Authorization": f"Bearer {token}"})
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def wait_for(predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            assert time.monotonic() < deadline, listener.status()
            time.sleep(0.01)

    try:
        code, body = request("GET", "/openapi.json", "")
        spec = json.loads(body)
        assert code == 200 and spec["openapi"] == "3.0.3"
        assert spec["security"] == [{"bearerAuth": []}]
        assert spec["paths"]["/openapi.json"]["get"]["security"] == []
        assert set(spec["paths"]) == {
            "/openapi.json", "/v1/listener", "/v1/listener/start",
            "/v1/listener/stop", "/v1/recordings", "/v1/recordings/{id}"}
        access_log = io.StringIO()
        with contextlib.redirect_stdout(access_log):
            assert request("GET", "/v1/listener?token=query-secret", "wrong")[0] == 401
            assert request("GET", "/missing")[0] == 404
            assert request("GET", "/v1/listener")[0] == 200
            assert request("DELETE", "/v1/listener")[0] == 404
        entries = [json.loads(line) for line in access_log.getvalue().splitlines()]
        assert [entry["status"] for entry in entries] == [401, 404, 200, 404]
        assert entries[0]["path"] == "/v1/listener"
        assert entries[0]["method"] == "GET" and entries[0]["client"] == "127.0.0.1"
        assert entries[0]["time"].endswith("Z")
        assert datetime.fromisoformat(entries[0]["time"].replace("Z", "+00:00")).tzinfo == timezone.utc
        assert "query-secret" not in access_log.getvalue()
        assert "test-token" not in access_log.getvalue()
        assert "wrong" not in access_log.getvalue()
        assert request("POST", "/v1/listener/start")[0] == 202
        wait_for(lambda: listener.status()["state"] == "listening")
        pid = listener.process.pid
        code, body = request("POST", "/v1/listener/start", body='{"filename":"meeting.mp4"}')
        assert code == 409 and "stop it" in json.loads(body)["error"]
        assert listener.process.pid == pid
        request("POST", "/v1/listener/start")
        assert listener.process.pid == pid
        wait_for(lambda: listener.status()["recording"] is not None)
        assert request("POST", "/v1/listener/stop")[0] == 202
        assert request("POST", "/v1/listener/start")[0] == 409
        wait_for(lambda: listener.status()["recording"]["state"] == "finalizing")
        listener.wait()
        assert listener.status()["state"] == "stopped"
        request("POST", "/v1/listener/start")
        request("POST", "/v1/listener/stop")
        listener.wait()
        assert listener.status()["state"] == "stopped"
        for body in ('{"filename":"../escape.mp4"}', '{"filename":""}',
                     '{"filename":null}', '{"filename":42}', '[]', '{'):
            assert request("POST", "/v1/listener/start", body=body)[0] == 400
        custom = root / "recordings/meeting.mp4"
        custom.write_bytes(b"existing")
        assert request("POST", "/v1/listener/start", body='{"filename":"meeting.mp4"}')[0] == 409
        assert custom.read_bytes() == b"existing"
        custom.unlink()
        assert request("POST", "/v1/listener/start", body='{"filename":"meeting.mp4"}')[0] == 202
        wait_for(lambda: listener.status()["recording"] is not None)
        assert listener.status()["recording"]["id"] == "meeting.mp4"
        pid = listener.process.pid
        assert request("POST", "/v1/listener/start", body='{"filename":"meeting.mp4"}')[0] == 202
        assert request("POST", "/v1/listener/start", body='{"filename":"other.mp4"}')[0] == 409
        assert listener.process.pid == pid
        request("POST", "/v1/listener/stop")
        listener.wait()
        custom.write_bytes(b"custom recording")
        assert request("GET", "/v1/recordings/meeting.mp4") == (200, b"custom recording")
        recording = json.loads(request("GET", "/v1/recordings")[1])["recordings"][0]
        assert set(recording) == {"id", "bytes", "modifiedAt", "downloadUrl"}
        assert recording["id"] == "meeting.mp4"
        assert recording["downloadUrl"] == "/v1/recordings/meeting.mp4"
        assert recording["modifiedAt"].endswith("Z")
        modified = datetime.fromisoformat(recording["modifiedAt"].replace("Z", "+00:00"))
        assert abs(modified.timestamp() - custom.stat().st_mtime) < 0.000001
        schema = spec["components"]["schemas"]["Recording"]
        assert set(schema["required"]) == set(recording)
        assert schema["properties"]["modifiedAt"]["format"] == "date-time"
        assert "204" in spec["paths"]["/v1/recordings/{id}"]["delete"]["responses"]
        assert request("DELETE", "/v1/recordings/meeting.mp4", "wrong")[0] == 401
        assert custom.read_bytes() == b"custom recording"
        assert request("DELETE", "/v1/recordings/meeting.mp4") == (204, b"")
        assert not custom.exists()
        assert request("GET", "/v1/recordings/meeting.mp4")[0] == 404
        assert json.loads(request("GET", "/v1/recordings")[1]) == {"recordings": []}
        assert request("DELETE", "/v1/recordings/meeting.mp4")[0] == 404
        complete = root / "recordings/cast-ABC123.mp4"
        complete.write_bytes(b"test recording")
        assert request("GET", "/v1/recordings/cast-ABC123.mp4") == (200, b"test recording")
        pending = root / "recordings/cast-ABC123"
        pending.mkdir()
        assert request("GET", "/v1/recordings/cast-ABC123.mp4")[0] == 404
        assert json.loads(request("GET", "/v1/recordings")[1]) == {"recordings": []}
        assert request("GET", "/v1/recordings/../../src/api.py")[0] == 404
        assert request("DELETE", "/v1/recordings/cast-ABC123.mp4")[0] == 404
        assert complete.read_bytes() == b"test recording" and pending.is_dir()
        assert request("DELETE", "/v1/recordings/../start.sh")[0] == 404
        assert request("DELETE", "/v1/recordings/%2e%2e%2fstart.sh")[0] == 404
        pending.rmdir()
        complete.unlink()
        complete.symlink_to(root / "start.sh")
        assert request("GET", "/v1/recordings/cast-ABC123.mp4")[0] == 404
        assert request("DELETE", "/v1/recordings/cast-ABC123.mp4")[0] == 404
        assert complete.is_symlink() and (root / "start.sh").is_file()
        print("PASS: authentication, lifecycle, finalization, downloads, deletion and path confinement")
    finally:
        listener.stop()
        listener.wait()
        server.shutdown()
        server.server_close()
        thread.join()
