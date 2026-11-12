# Local Cast Recorder — experimental MVP

Native Apple Silicon macOS proof of concept using Google's Open Screen Cast
receiver. The user-approved scope is **same-Mac, loopback-only casting**; LAN
discovery is no longer required. The receiver binds to `127.0.0.1` by default.
An actual Chrome tab cast on loopback produced a finalized, playable H.264/AAC
MP4. Both tracks passed a full FFmpeg decode check after `./stop.sh`. Ctrl+C
finalization was also tested on an earlier audio-only session.

## Dependency setup and current status

Open Screen source revision `a79edfcb0028e6f175a871b123b48b31989a7803` was
downloaded over SSH. Its official `gclient` dependency workflow uses HTTPS Git
remotes, including Google-hosted build, BoringSSL, QUICHE, Rust and toolchain
support repositories. HTTPS is authorized for these Open Screen dependencies
and their build tools. This project's own Git remote remains SSH-only.

`./setup.sh` now installs Google's `depot_tools` and runs the official
`gclient sync --no-history` workflow inside `.deps/`. The Open Screen solution
is unmanaged, preserving its pinned source revision and the recorder overlay.
No configuration or remotes in the project's own Git repository are changed.

Dependency setup completed from a normal macOS terminal. The receiver then
built and linked successfully, and its development certificate was generated.
The installed Command Line Tools SDK works through a small, project-local
adapter for Chromium's SDK metadata probe, which normally insists on full
Xcode. The adapter reads the real CLT release and SDK build metadata; it changes
no system tools or Xcode selection. Full Xcode installations use the upstream
probe unchanged.

Open Screen's raw mDNS sender failed on this Mac, so discovery uses native
macOS Bonjour, registered only for local clients in the default loopback mode.
Chrome also gets an explicit loopback receiver through its development flag.
Bonjour registration must succeed before the launcher reports it is waiting.
Open Screen still handles Cast authentication, mirroring and media transport.
The agent sandbox blocks listening sockets and access to the Bonjour daemon;
run `./start.sh` from a normal terminal for real discovery and casting tests.

## Installation

Required host tools: Apple Silicon macOS, Xcode Command Line Tools (or full
Xcode), Homebrew, Git, Python 3.11 or later,
Google Chrome and FFmpeg with libx264/libvpx/libopus. Native execution; no Docker.

```bash
./setup.sh
```

Setup downloads Open Screen via `git@github.com:chromium/openscreen.git` if
absent, checks tools, installs FFmpeg through Homebrew if missing, and prepares
`recordings/`. It fetches pinned dependencies under `.deps/openscreen`,
overlays the recorder, builds the headless `cast_receiver`, and generates a
private development CA in
`.runtime/certs/`. It preserves existing credentials. The build and overlay
steps have now completed successfully using the installed CLT SDK. A failed
dependency sync is retried on the next setup run; existing source edits are
preserved.

Official source documentation:

- [Build and dependency setup](https://github.com/chromium/openscreen/blob/a79edfcb0028e6f175a871b123b48b31989a7803/README.md)
- [Receiver use and Chrome development certificate](https://github.com/chromium/openscreen/blob/a79edfcb0028e6f175a871b123b48b31989a7803/cast/docs/USING.md)
- [macOS and Apple Silicon library paths](https://github.com/chromium/openscreen/blob/a79edfcb0028e6f175a871b123b48b31989a7803/cast/docs/external_libraries.md)

## Start and cast (after a successful build)

Run in one terminal and leave it open:

```bash
./start.sh
```

The receiver uses `lo0`, TCP `127.0.0.1:8010` for Cast TLS and loopback UDP
2344 for Cast streaming. The native Bonjour helper publishes a local-only
service with friendly name `Local Recorder`, without a LAN advertisement.

Launch a separate Chrome instance from a second terminal:

```bash
./chrome.sh
```

This uses `.runtime/chrome-profile/` and the absolute development certificate
path with Chrome's documented `--cast-developer-certificate-path` flag.
It does not modify your normal Chrome profile or globally disable certificate
checks. Close this isolated instance before regenerating its credentials.

For loopback it also uses `--media-router-cast-device-ips=127.0.0.1:8010` and
`--enable-features=CastAllowAllIPs`, because Chrome normally excludes loopback
Cast destinations. These apply only to the isolated instance; Cast certificate
and challenge verification remain enabled. Chrome's fixed-IP development
entry is named **Receiver-127_0_0_1**. Select that if **Local Recorder** is absent.

Chrome may show an “unsupported command-line flag” banner for
`--cast-developer-certificate-path`. Chromium deliberately warns when this
development trust setting is enabled; the banner does not mean the flag was
ignored. Keep it enabled in the isolated profile for this receiver.

For this development build, `chrome.sh` writes focused Cast handshake logs to
`.runtime/chrome-cast.log`. Its flags take effect only when the isolated Chrome
process is freshly launched. Receiver output is also saved in
`.runtime/receiver.log`. Neither log is committed to Git.

1. Allow local-network access and incoming connections if macOS prompts.
2. Open a tab with ordinary, unprotected video/audio and start playback.
3. Open Chrome's menu → **Cast, save, and share** → **Cast…** (wording may vary).
4. Under **Sources**, select **Cast tab**, then **Local Recorder** or
   **Receiver-127_0_0_1**.
5. Enable tab audio if Chrome offers that choice. Do not use a website's media
   Cast button: the recorder supports tab mirroring, not URL-based playback.
6. Confirm the terminal reports a negotiated connection and `Recording started`.

## Stop and output

```bash
./stop.sh
```

Stopping casting in Chrome also finalizes that session's recording, leaving the
receiver available for another session. `stop.sh` gracefully terminates the
receiver and waits for final conversion. Keep the start terminal open until
the output filename is printed. Conversion can take time; do not force-kill it.

**Ctrl+C in the start terminal performs the same graceful shutdown.** The
launcher keeps the receiver and FFmpeg in separate process groups, withdraws
the Bonjour advertisement, forwards one termination request, and waits for
MP4 finalization before exiting. Repeated Ctrl+C during conversion is handled
without interrupting FFmpeg. Closing the terminal (SIGHUP) also requests shutdown.

Final output is `./recordings/cast-XXXXXX.mp4`, with a unique random session
name. While recording, incoming encoded packets are stream-copied to
`recordings/cast-XXXXXX/incoming.mkv`. On session teardown, FFmpeg copies H.264
video (or converts VP8 to H.264) and converts Opus to AAC, preserving packet timestamps and A/V offsets and
writing a finalized MP4 with faststart. The intermediate file is deleted only
after successful conversion. On failure it is retained for recovery.

Example recovery, choosing an unused MP4 filename:

```bash
ffmpeg -i recordings/cast-XXXXXX/incoming.mkv -c:v libx264 -c:a aac -movflags +faststart recordings/recovered.mp4
```

If the process was force-killed and the lock remains, inspect
`.runtime/receiver.lock/{pid,receiver.pid,bonjour.pid}` and confirm those processes are gone
before manually deleting that lock directory. The scripts do not automatically
kill processes from an unverified stale PID.

If TCP port 8010 is still occupied, `start.sh` reports the listener and exits.
Stop the existing receiver first; removing its lock does not stop its process.
The receiver overlay also checks TCP bind failures before calling `Listen`,
avoiding an upstream assertion on a closed socket.

## Feasibility findings and implementation

The inspected upstream revision documents Linux and macOS receivers, including
Apple Silicon Homebrew paths and an ARM64 compiler download. Its standalone
receiver already implements mDNS/DNS-SD, Cast TLS/device authentication and the
mirroring application IDs. Its developer guide documents Chrome stable
connecting with a trusted development CA. No authentication/DRM bypass is used.

The receiver exposes decrypted, reassembled encoded frames through
`Receiver::Consumer` / `ConsumeNextFrame`, before decoding. Each frame carries
a common `reference_time` intended for synchronization across audio and video.
The overlay replaces only the demo playback controller and restricts
negotiation to H.264 or VP8 video and Opus audio. Discovery uses macOS Bonjour; Cast negotiation,
authentication, encryption and transport code remains upstream. The MP4 conversion is needed for broad player
compatibility; VP8 is preserved without decoding in the intermediate MKV.

## Validation

Run the independent recorder test:

```bash
./tests/recording.sh
```

It compiles the actual muxer against installed FFmpeg, generates encoded
VP8/Opus and H.264/Opus fixtures, replays their packets with a deliberately offset common
clock, finalizes MP4s and checks H.264/AAC streams, duration, preserved A/V
start offset and complete decoding. It also checks video-only input. Test
artifacts stay under `.runtime/recorder-test.*/`. These are synthetic input
tests, **not Chrome Cast tests**.

`python3 tests/shutdown.py` verifies process-group Ctrl+C delivery, repeated
Ctrl+C during simulated child finalization, child exit and lock cleanup. A
direct rebuilt-receiver SIGTERM test also exited cleanly after initialization
(its TCP bind was blocked by the agent sandbox). A real audio-only Cast session
also finalized successfully with Ctrl+C; the MP4 contains 27 seconds of stereo AAC.

Verify discovery and the real receiver manually:

```bash
dns-sd -B _googlecast._tcp local.
lsof -nP -iTCP:8010 -sTCP:LISTEN
# While a mirroring session is active:
lsof -nP -iUDP:2344
```

Check a resulting recording:

```bash
ffprobe -v error -show_streams -show_format recordings/cast-XXXXXX.mp4
```

The `dns-sd` browse keeps running until Ctrl-C. Check discovery, selection,
visible changing tab content, audible recorded sound, and graceful stop in the
real Chrome flow. Earlier LAN-mode startup and Bonjour discovery were observed,
and loopback authentication and A/V recording have passed. The actual Chrome
test produced an approximately 25-second MP4 with H.264
video and 48kHz stereo AAC audio. `./stop.sh` finalized the MP4
and the receiver exited cleanly. `ffprobe` confirmed both tracks and a full
FFmpeg decode completed without errors. Video starts at 0.030 seconds and
audio at 0.000 seconds; subjective lip-sync and long-duration drift were not tested.

## Local control API

Run the persistent native service (Python standard library, no additional dependencies):

```bash
python3 src/api.py
```

It listens on `127.0.0.1:8020`, initially with the Cast listener stopped.
Keep the process running; stopping the listener leaves the API available.
SIGTERM or Ctrl+C stops both and waits for recording finalization. This command
does not install a login/background service. Use the API as the single owner
of the listener; do not run `start.sh` separately at the same time.

On first launch it creates `.runtime/api.token` with mode 0600. Supply its
contents in `Authorization: Bearer <token>` on every request. Treat this token
as a credential: it permits listener control and access to all completed recordings.
No browser CORS access is enabled.

Machine-readable OpenAPI 3.0 documentation is available at
**http://127.0.0.1:8020/openapi.json** (no authentication required for the
documentation). Give this URL to a coding agent, or import it into Swagger
Editor/Postman. For a remote agent, provide the contents of
[`src/openapi.json`](src/openapi.json); localhost URLs refer to the agent's own
machine. Supply the bearer token separately through the client's secret
configuration. The contract includes authentication, schemas, lifecycle polling,
errors and binary downloads. Restart an already running API process to enable
this endpoint. No hosted Swagger UI or external assets are required.

JSON response fields use camelCase. Recording metadata includes `modifiedAt`
as an ISO 8601 UTC string (ending in `Z`) and `downloadUrl` as a relative path.
Access log timestamps also use ISO 8601 UTC.

| Method | Path | Result |
| --- | --- | --- |
| GET | `/v1/listener` | Listener state, current recording ID/state/bytes, last error |
| POST | `/v1/listener/start` | Start listening; 202, poll status for readiness |
| POST | `/v1/listener/stop` | Graceful stop; 202, poll until stopped |
| GET | `/v1/recordings` | Completed recordings with sizes, ISO 8601 UTC modification times and download URLs |
| GET | `/v1/recordings/{id}` | Download a completed MP4 to the requesting application |

Start accepts an optional JSON body, for example `{"filename":"meeting.mp4"}`.
The filename must have 1–120 ASCII letters, digits, underscores or hyphens
before `.mp4`, starting with a letter or digit. Files stay under `recordings/`.
Omitting it keeps the random filename. Invalid bodies/names return 400; an
existing filename or session directory returns 409. Later Cast sessions in the
same listener run use `meeting-2.mp4`, etc., skipping existing names. Starting
an already running listener leaves its filename choice unchanged. Posting a
different filename returns 409; stop the listener before choosing a new filename.

Listener states are `stopped`, `starting`, `listening`, `stopping`, `failed`.
The current recording is null or has state `recording`, `finalizing`, `failed`;
bytes describes the intermediate MKV size on disk, which may lag buffered packets.
Starting an already starting/listening listener and stopping a stopped listener
are idempotent. Starting during shutdown returns 409. Startup failures are
reported asynchronously in status. Failed or incomplete conversions are excluded
from downloads. Polling once per second is sufficient; there is no live media API.

```bash
TOKEN="$(cat .runtime/api.token)"
curl -H "Authorization: Bearer $TOKEN" -X POST http://127.0.0.1:8020/v1/listener/start
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8020/v1/listener
curl -H "Authorization: Bearer $TOKEN" -X POST http://127.0.0.1:8020/v1/listener/stop
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8020/v1/recordings
curl -H "Authorization: Bearer $TOKEN" -o copy.mp4 http://127.0.0.1:8020/v1/recordings/cast-XXXXXX.mp4
```

For Docker Desktop clients, try `http://host.docker.internal:8020` with the same
token. VM networking varies: `--host <host-side-private-interface-address>` and
`--port 8020` let you bind to a host-only interface when loopback forwarding is
unavailable. Keep the binding restricted to this Mac and its guests; binding
to `0.0.0.0` also exposes the plaintext API to reachable network interfaces.
The API binding does not change loopback-only Cast transport. Container/VM
connectivity requires testing in your actual runtime.
See [Docker's host connectivity documentation](https://docs.docker.com/desktop/features/networking/networking-how-tos/).

`python3 tests/api.py` checks the API against a synthetic launcher, including
authentication, stop/finalization, download eligibility and path confinement.
After changing the C++ recording overlay, run `./setup.sh` to rebuild the receiver;
the finalizing event requires that rebuilt binary.

## Known limitations

- The tested flow uses the isolated Chrome and its fixed-IP device entry,
  `Receiver-127_0_0_1`. The friendly name `Local Recorder` is advertised locally
  but is not the device label used in the successful test.
- LAN support is not required and is not working on this Mac: a native plain
  TCP test failed with `ENOTCONN` on its LAN address, including as root. This
  happens below TLS and also affects the receiver. Loopback avoids that path.
- Supports H.264/VP8 video and 48kHz mono/stereo Opus only; no media URL casting,
  remoting, DRM capture, GUI or live preview.
- Audio exists only if Chrome supplies it. Synthetic timing-offset tests pass;
  the actual Cast recording contains both tracks, but subjective lip-sync and
  long-term clock drift have not been assessed.
- Codec/resolution renegotiation creates separate recordings. Dynamic VP8
  resolution changes within one negotiation have not been validated.
- Final conversion consumes CPU and temporarily needs space for MKV and MP4.
- The receiver and recordings are experimental. Packet loss, abrupt process
  termination and filesystem errors can leave incomplete files.
- Local network/firewall settings, VPNs or multicast isolation may prevent
  discovery. This project does not change those settings automatically.

### mDNS reports “No route to host”

This error concerns the multicast announcement; the TCP receiver can still be
listening. On the tested Mac, TCP 8010 was listening even
while these errors appeared. Switching interfaces is not automatically needed.

If launching from iTerm2 or an IDE terminal, check **System Settings → Privacy
& Security → Local Network** and enable access for that application. Stop the
receiver, quit and reopen the terminal app, then run `./start.sh` again. If the
application is not listed, try launching the same script from Apple Terminal.
Also allow Google Chrome local-network access when prompted.

Apple documents automatic local-network access for command-line tools launched
from Terminal or SSH; other responsible applications can require permission.
See [Apple's local network privacy guidance](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy).
This is a diagnostic step, not a confirmed diagnosis of every multicast error.
