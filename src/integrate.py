"""Apply the small recorder overlay to the pinned, disposable dependency tree."""
import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parent.parent
tree = root / ".deps/openscreen"
revision = "a79edfcb0028e6f175a871b123b48b31989a7803"
if subprocess.check_output(["git", "-C", str(tree), "rev-parse", "HEAD"], text=True).strip() != revision:
    sys.exit("Error: unexpected Open Screen revision; refusing to apply overlay")
target = tree / "cast/standalone_receiver"
updates = {}
for name in ("recording.h", "recording.cc", "streaming_playback_controller.h", "streaming_playback_controller.cc"):
    updates[name] = (root / "src" / name).read_text()
# Read originals from Git so repeated setup never duplicates edits.
def original(name):
    return subprocess.check_output(["git", "-C", str(tree), "show", f"HEAD:cast/standalone_receiver/{name}"], text=True)

build = original("BUILD.gn")
build = build.replace('    "cast_service.cc",', '    "cast_service.cc",\n    "recording.cc",\n    "recording.h",', 1)
build = build.replace('  if (have_ffmpeg && have_libsdl2) {',
                      '  include_dirs = ffmpeg_include_dirs\n  lib_dirs = external_lib_dirs + ffmpeg_lib_dirs\n  libs = ffmpeg_libs\n\n  if (have_ffmpeg && have_libsdl2) {', 1)
build = build.replace('  testonly = true',
                      '  remove_configs = [ "//build/config/compiler:no_exceptions" ]\n  configs = [ "//build/config/compiler:exceptions" ]\n  testonly = true', 1)
updates["BUILD.gn"] = build
app = original("mirroring_application.cc")
begin = app.index("  constraints.video_codecs =")
end = app.index("  constraints.enable_dscp", begin)
app = app[:begin] + "  constraints.video_codecs = {VideoCodec::kH264, VideoCodec::kVp8};\n  constraints.audio_codecs = {AudioCodec::kOpus};\n" + app[end:]
updates["mirroring_application.cc"] = app
# The dependency checkout might have user edits. Accept only pristine upstream
# files, the previous overlay we installed, or identical new content.
state = root / ".runtime/overlay"
for name, content in updates.items():
    path = target / name
    if not path.exists():
        continue
    current = path.read_text()
    tracked = subprocess.run(["git", "-C", str(tree), "show", f"HEAD:cast/standalone_receiver/{name}"],
                             text=True, capture_output=True)
    previous = state / name
    allowed = [content]
    if tracked.returncode == 0:
        allowed.append(tracked.stdout)
    if previous.exists():
        allowed.append(previous.read_text())
    if current not in allowed:
        sys.exit(f"Error: preserving local changes in {path}; reconcile them manually")
state.mkdir(parents=True, exist_ok=True)
for name, content in updates.items():
    (target / name).write_text(content)
    (state / name).write_text(content)

# Chromium's SDK probe insists on xcodebuild even for this headless C++
# executable. Use the installed CLT SDK when setup explicitly selects it.
build_tree = tree / "build"
sdk_file = "config/mac/mac_sdk.gni"
sdk_original = subprocess.check_output(
    ["git", "-C", str(build_tree), "show", f"HEAD:{sdk_file}"], text=True)
sdk_patched = sdk_original.replace(
    '_mac_sdk_result = exec_script(script_name, sdk_info_args, "scope")',
    'if (getenv("LOCAL_CAST_CLT") == "1") {\n'
    '  _mac_sdk_result = exec_script("//build_overrides/clt_sdk.py", [ "scope" ], "scope")\n'
    '} else {\n'
    '  _mac_sdk_result = exec_script(script_name, sdk_info_args, "scope")\n}'
).replace(
    '  find_sdk_lines =\n      exec_script("//build/mac/find_sdk.py", find_sdk_args, "list lines")',
    '  if (getenv("LOCAL_CAST_CLT") == "1") {\n'
    '    find_sdk_lines = exec_script("//build_overrides/clt_sdk.py", [ "lines" ], "list lines")\n'
    '  } else {\n'
    '    find_sdk_lines = exec_script("//build/mac/find_sdk.py", find_sdk_args, "list lines")\n  }'
)
sdk_path = build_tree / sdk_file
previous = state / "mac_sdk.gni"
allowed = [sdk_original, sdk_patched]
if previous.exists():
    allowed.append(previous.read_text())
if sdk_path.read_text() not in allowed:
    sys.exit(f"Error: preserving local changes in {sdk_path}")
sdk_path.write_text(sdk_patched)
previous.write_text(sdk_patched)
(tree / "build_overrides/clt_sdk.py").write_text((root / "src/clt_sdk.py").read_text())

# Upstream ignores Bind() failures and calls Listen() on the closed socket,
# triggering a fatal state check when the port is already occupied.
tls_file = "platform/impl/tls_connection_factory_posix.cc"
tls_original = subprocess.check_output(
    ["git", "-C", str(tree), "show", f"HEAD:{tls_file}"], text=True)
tls_patched = tls_original.replace(
    '  socket->Bind();\n  socket->Listen(options.backlog_size);\n'
    '  if (socket->state() == TcpSocketState::kClosed) {\n'
    '    DispatchError(Error::Code::kSocketListenFailure);\n'
    '    TRACE_SET_RESULT(Error::Code::kSocketListenFailure);',
    '  Error error = socket->Bind();\n'
    '  if (error.ok()) {\n'
    '    error = socket->Listen(options.backlog_size);\n  }\n'
    '  if (!error.ok()) {\n'
    '    OSP_LOG_ERROR << "Cannot listen on " << local_address << ": " << error;\n'
    '    DispatchError(error);\n'
    '    TRACE_SET_RESULT(error);'
)
tls_patched = tls_patched.replace(
    '      OSP_DVLOG << "SSL_accept failed with error: " << error;',
    '      OSP_LOG_ERROR << "TLS handshake failed: " << error;')
tls_patched = tls_patched.replace(
    '      if (accepted.error().code() != Error::Code::kAgain) {',
    '      if (accepted.error().code() != Error::Code::kAgain) {\n'
    '        OSP_LOG_ERROR << "TCP accept failed: " << accepted.error();')
tls_patched = tls_patched.replace(
    '  TRACE_SCOPED(TraceCategory::kSsl,\n               "TlsConnectionFactoryPosix::OnSocketAccepted");',
    '  TRACE_SCOPED(TraceCategory::kSsl,\n               "TlsConnectionFactoryPosix::OnSocketAccepted");\n'
    '  OSP_LOG_INFO << "TCP peer accepted: " << socket->remote_address().value();')
tls_patched = tls_patched.replace(
    '  if (connection_result.is_error()) {\n    DispatchError(connection_result.error());',
    '  if (connection_result.is_error()) {\n'
    '    OSP_LOG_ERROR << "Cannot create TLS connection: " << connection_result.error();\n'
    '    DispatchError(connection_result.error());')
tls_patched = tls_patched.replace(
    '  if (!SSL_set_fd(ssl.get(), connection->socket_->socket_handle().fd)) {',
    '  if (!SSL_set_fd(ssl.get(), connection->socket_->socket_handle().fd)) {\n'
    '    OSP_LOG_ERROR << "Cannot attach TLS socket: " << SSLErrorCodeToError(SSL_ERROR_SSL);')
tls_patched = tls_patched.replace(
    '  connection->ssl_.swap(ssl);',
    '  OSP_LOG_INFO << "TLS configured on fd " << connection->socket_->socket_handle().fd\n'
    '               << ", TCP state " << static_cast<int>(connection->socket_->state());\n'
    '  connection->ssl_.swap(ssl);')
accept_begin = tls_patched.index('void TlsConnectionFactoryPosix::Accept(')
tls_patched = tls_patched[:accept_begin] + tls_patched[accept_begin:].replace(
    '  if (connection->socket_->state() == TcpSocketState::kClosed) {\n    return;',
    '  if (connection->socket_->state() == TcpSocketState::kClosed) {\n'
    '    OSP_LOG_ERROR << "TLS connection socket was closed before handshake";\n    return;', 1).replace(
    '  ErrorOr<std::vector<uint8_t>> der_peer_cert =',
    '  OSP_LOG_INFO << "TLS handshake completed";\n'
    '  ErrorOr<std::vector<uint8_t>> der_peer_cert =', 1).replace(
    '          self->Accept(std::move(conn));\n        }',
    '          self->Accept(std::move(conn));\n        } else {\n'
    '          OSP_LOG_ERROR << "TLS handshake cancelled: factory expired";\n        }', 1)
accept_begin = tls_patched.index('void TlsConnectionFactoryPosix::Accept(')
tls_patched = tls_patched[:accept_begin] + tls_patched[accept_begin:].replace(
    '  const int connection_status = SSL_accept(connection->ssl_.get());',
    '  const int connection_status = SSL_accept(connection->ssl_.get());\n'
    '  const int syscall_error = errno;\n'
    '  if (connection_status != 1 && syscall_error == ENOTCONN) {\n'
    '    OSP_LOG_ERROR << "macOS disconnected the accepted TCP socket before TLS (ENOTCONN)";\n'
    '    DispatchConnectionFailed(connection->GetRemoteEndpoint());\n'
    '    return;\n  }', 1).replace(
    '    if (error.code() == Error::Code::kAgain) {',
    '    if (error.code() == Error::Code::kAgain) {\n'
    '      static int pending_logs = 0;\n'
    '      if (pending_logs < 3) {\n'
    '        ++pending_logs;\n'
    '        OSP_LOG_WARN << "TLS pending: socket fd " << connection->socket_->socket_handle().fd\n'
    '                     << ", BIO fd " << SSL_get_fd(connection->ssl_.get())\n'
    '                     << ", errno " << syscall_error << " (" << strerror(syscall_error) << ")";\n'
    '      }', 1)
tls_path = tree / tls_file
previous = state / "tls_connection_factory_posix.cc"
allowed = [tls_original, tls_patched]
if previous.exists():
    allowed.append(previous.read_text())
if tls_path.read_text() not in allowed:
    sys.exit(f"Error: preserving local changes in {tls_path}")
tls_path.write_text(tls_patched)
previous.write_text(tls_patched)

# On a termination signal, leave queued I/O callbacks for the second run loop,
# where main.cc destroys the service before draining. Flushing first can spin
# forever while still-live socket readers keep posting callbacks.
runner_file = "platform/impl/task_runner.cc"
runner_original = subprocess.check_output(
    ["git", "-C", str(tree), "show", f"HEAD:{runner_file}"], text=True)
runner_patched = runner_original.replace(
    '  while (GrabMoreRunnableTasks()) {',
    '  while (g_signal_state != kSignaled && GrabMoreRunnableTasks()) {', 1)
runner_path = tree / runner_file
previous = state / "task_runner.cc"
allowed = [runner_original, runner_patched]
if previous.exists():
    allowed.append(previous.read_text())
if runner_path.read_text() not in allowed:
    sys.exit(f"Error: preserving local changes in {runner_path}")
runner_path.write_text(runner_patched)
previous.write_text(runner_patched)

# Log codec metadata only: the full Cast OFFER contains session encryption keys.
session_file = "cast/streaming/public/receiver_session.cc"
session_original = subprocess.check_output(
    ["git", "-C", str(tree), "show", f"HEAD:{session_file}"], text=True)
session_patched = session_original.replace(
    '                                    PendingOffer* properties) {',
    '                                    PendingOffer* properties) {\n'
    '  OSP_LOG_INFO << "Cast offer: " << offer.video_streams.size()\n'
    '               << " video streams, " << offer.audio_streams.size() << " audio streams";\n'
    '  for (const auto& stream : offer.video_streams) {\n'
    '    OSP_LOG_INFO << "Offered video codec: " << CodecToString(stream.codec);\n'
    '  }', 1)
session_path = tree / session_file
previous = state / "receiver_session.cc"
allowed = [session_original, session_patched]
if previous.exists():
    allowed.append(previous.read_text())
if session_path.read_text() not in allowed:
    sys.exit(f"Error: preserving local changes in {session_path}")
session_path.write_text(session_patched)
previous.write_text(session_patched)
