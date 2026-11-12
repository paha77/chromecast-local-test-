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
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from api import Listener, handler


with tempfile.TemporaryDirectory() as directory:
    root = pathlib.Path(directory)
    (root / "recordings").mkdir()
    (root / "start.sh").write_text('''exec python3 -u -c '
import signal, sys, time
def stop(*args):
    print("Finalizing recording", flush=True)
    time.sleep(0.3)
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
print("Receiver supervisor ready")
print("Waiting for Cast connection")
print("Recording started")
print("Output filename: recordings/cast-ABC123.mp4")
while True: time.sleep(0.01)
'
''')
    listener = Listener(root)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(listener, "test-token"))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()

    def request(method, path, token="test-token"):
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        connection.request(method, path, headers={"Authorization": f"Bearer {token}"})
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
            assert request("DELETE", "/v1/listener")[0] == 501
        entries = [json.loads(line) for line in access_log.getvalue().splitlines()]
        assert [entry["status"] for entry in entries] == [401, 404, 200, 501]
        assert entries[0]["path"] == "/v1/listener"
        assert entries[0]["method"] == "GET" and entries[0]["client"] == "127.0.0.1"
        assert entries[0]["time"]
        assert "query-secret" not in access_log.getvalue()
        assert "test-token" not in access_log.getvalue()
        assert "wrong" not in access_log.getvalue()
        assert request("POST", "/v1/listener/start")[0] == 202
        wait_for(lambda: listener.status()["state"] == "listening")
        pid = listener.process.pid
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
        complete = root / "recordings/cast-ABC123.mp4"
        complete.write_bytes(b"test recording")
        assert request("GET", "/v1/recordings/cast-ABC123.mp4") == (200, b"test recording")
        pending = root / "recordings/cast-ABC123"
        pending.mkdir()
        assert request("GET", "/v1/recordings/cast-ABC123.mp4")[0] == 404
        assert json.loads(request("GET", "/v1/recordings")[1]) == {"recordings": []}
        assert request("GET", "/v1/recordings/../../src/api.py")[0] == 404
        pending.rmdir()
        complete.unlink()
        complete.symlink_to(root / "start.sh")
        assert request("GET", "/v1/recordings/cast-ABC123.mp4")[0] == 404
        print("PASS: authentication, lifecycle, finalization, downloads and path confinement")
    finally:
        listener.stop()
        listener.wait()
        server.shutdown()
        server.server_close()
        thread.join()
