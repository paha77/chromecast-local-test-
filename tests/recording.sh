#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
PREFIX="$(brew --prefix ffmpeg)"
mkdir -p .runtime
TEST_DIR="$(mktemp -d "$ROOT/.runtime/recorder-test.XXXXXX")"
echo "Testing in $TEST_DIR"
clang++ -std=c++17 -Wall -Wextra -Werror -I"$PREFIX/include" -Isrc \
  src/recording.cc tests/replay.cc -L"$PREFIX/lib" \
  -lavformat -lavcodec -lavutil -o "$TEST_DIR/replay"
for MODE in av video h264; do
  mkdir "$TEST_DIR/$MODE"
  cd "$TEST_DIR/$MODE"
  if [[ "$MODE" == h264 ]]; then
    ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=320x240:rate=10:duration=2 \
      -f lavfi -i sine=frequency=440:sample_rate=48000:duration=2 \
      -c:v libx264 -bf 0 -g 10 -c:a libopus -ac 2 fixture.mkv
  elif [[ "$MODE" == av ]]; then
    ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=320x240:rate=10:duration=2 \
      -f lavfi -i sine=frequency=440:sample_rate=48000:duration=2 \
      -c:v libvpx -g 10 -c:a libopus -ac 2 fixture.mkv
  else
    ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=320x240:rate=10:duration=2 \
      -c:v libvpx -g 10 -an fixture.mkv
  fi
  "$TEST_DIR/replay" fixture.mkv
  for FILE in recordings/*.mp4; do
    ffprobe -v error -show_streams -show_format -of json "$FILE" > probe.json
    python3 - "$MODE" <<'PY'
import json
import sys
with open('probe.json') as f:
    info = json.load(f)
video = [s for s in info['streams'] if s['codec_type'] == 'video']
audio = [s for s in info['streams'] if s['codec_type'] == 'audio']
assert len(video) == 1 and video[0]['codec_name'] == 'h264', info
assert 1.8 < float(info['format']['duration']) < 2.8, info
if sys.argv[1] in ('av', 'h264'):
    assert len(audio) == 1 and audio[0]['codec_name'] == 'aac', info
    delta = float(video[0]['start_time']) - float(audio[0]['start_time'])
    assert 0.20 < delta < 0.31, delta
else:
    assert not audio, info
PY
    ffmpeg -hide_banner -loglevel error -i "$FILE" -f null -
  done
done
echo 'PASS: video-only and synchronized audio/video MP4 mux/finalization and decoding'
