#include "recording.h"

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/channel_layout.h>
#include <libavutil/mem.h>
}

extern char** environ;
namespace {
void Check(int result, const char* operation) {
  if (result >= 0) return;
  char error[AV_ERROR_MAX_STRING_SIZE];
  av_strerror(result, error, sizeof(error));
  throw std::runtime_error(std::string(operation) + ": " + error);
}
}

Recording::Recording(bool video, bool audio, int channels, int sample_rate, bool h264)
    : video_(video), audio_(audio), channels_(channels), sample_rate_(sample_rate), h264_(h264) {
  std::filesystem::create_directories("recordings");
  // mkdtemp makes a unique session directory, including across simultaneous runs.
  char path[] = "recordings/cast-XXXXXX";
  const char* created = mkdtemp(path);
  if (!created) throw std::runtime_error("Cannot create recording directory");
  directory_ = created;
  intermediate_ = directory_ / "incoming.mkv";
  output_ = directory_.string() + ".mp4";
  if (const char* filename = std::getenv("CAST_RECORDING_FILENAME")) {
    const std::string name(filename);
    const auto stem = name.size() > 4 ? name.substr(0, name.size() - 4) : "";
    if (stem.empty() || stem.size() > 120 || name.substr(name.size() - 4) != ".mp4" ||
        std::string("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789").find(stem[0]) == std::string::npos ||
        stem.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-") != std::string::npos)
      throw std::runtime_error("Invalid recording filename");
    // Reserve the session directory atomically; later sessions get a suffix.
    std::filesystem::remove(directory_);
    for (unsigned index = 1;; ++index) {
      const auto suffix = index == 1 ? std::string() : "-" + std::to_string(index);
      directory_ = std::filesystem::path("recordings") /
          (stem.substr(0, 120 - suffix.size()) + suffix);
      output_ = directory_.string() + ".mp4";
      if (std::filesystem::symlink_status(output_).type() != std::filesystem::file_type::not_found)
        continue;
      if (std::filesystem::create_directory(directory_)) break;
    }
    intermediate_ = directory_ / "incoming.mkv";
  }
}

Recording::~Recording() { Finish(); }

void Recording::Open(int width, int height) {
  Check(avformat_alloc_output_context2(&context_, nullptr, "matroska",
                                      intermediate_.c_str()), "Create muxer");
  if (video_) {
    AVStream* stream = avformat_new_stream(context_, nullptr);
    if (!stream) throw std::bad_alloc();
    video_index_ = stream->index;
    stream->time_base = AVRational{1, 1000000};
    auto* p = stream->codecpar;
    p->codec_type = AVMEDIA_TYPE_VIDEO;
    p->codec_id = h264_ ? AV_CODEC_ID_H264 : AV_CODEC_ID_VP8;
    p->width = width;
    p->height = height;
    if (h264_) {
      p->extradata_size = static_cast<int>(video_header_.size());
      p->extradata = static_cast<uint8_t*>(av_mallocz(video_header_.size() + AV_INPUT_BUFFER_PADDING_SIZE));
      if (!p->extradata) throw std::bad_alloc();
      std::memcpy(p->extradata, video_header_.data(), video_header_.size());
    }
  }
  if (audio_) {
    AVStream* stream = avformat_new_stream(context_, nullptr);
    if (!stream) throw std::bad_alloc();
    audio_index_ = stream->index;
    stream->time_base = AVRational{1, 1000000};
    auto* p = stream->codecpar;
    p->codec_type = AVMEDIA_TYPE_AUDIO;
    p->codec_id = AV_CODEC_ID_OPUS;
    p->sample_rate = sample_rate_;
    av_channel_layout_default(&p->ch_layout, channels_);
    // Cast transports raw Opus packets, without an Ogg OpusHead. There is no
    // Ogg encoder pre-skip to discard. Mapping family zero supports mono/stereo.
    p->extradata_size = 19;
    p->extradata = static_cast<uint8_t*>(av_mallocz(19 + AV_INPUT_BUFFER_PADDING_SIZE));
    if (!p->extradata) throw std::bad_alloc();
    std::memcpy(p->extradata, "OpusHead", 8);
    p->extradata[8] = 1;
    p->extradata[9] = static_cast<uint8_t>(channels_);
    for (int i = 0; i < 4; ++i) p->extradata[12 + i] = (sample_rate_ >> (8 * i)) & 255;
  }
  Check(avio_open(&context_->pb, intermediate_.c_str(), AVIO_FLAG_WRITE), "Open recording");
  Check(avformat_write_header(context_, nullptr), "Write recording header");
  header_written_ = true;
  std::cout << "Recording started\nOutput filename: " << output_ << std::endl;
}

void Recording::Write(bool video, const uint8_t* data, size_t size,
                      int64_t time_us, bool key) {
  if (finished_ || failed_) return;
  try {
    Packet packet{video, std::vector<uint8_t>(data, data + size), time_us, key};
    if (!context_) {
      if (video_) {
        if (!video) {
          // Bound buffering while waiting for a video keyframe (about 5s).
          if (pending_.size() < 250) pending_.push_back(std::move(packet));
          return;
        }
        if (!key) return;
        int width = 0, height = 0;
        if (h264_) {
          // Cast H.264 access units use Annex B, with SPS/PPS on keyframes.
          // FFmpeg's parser reads their dimensions; the muxer builds avcC.
          auto* parser = av_parser_init(AV_CODEC_ID_H264);
          auto* codec = avcodec_alloc_context3(nullptr);
          if (!parser || !codec) {
            if (parser) av_parser_close(parser);
            avcodec_free_context(&codec);
            throw std::bad_alloc();
          }
          parser->flags |= PARSER_FLAG_COMPLETE_FRAMES;
          std::vector<uint8_t> padded(data, data + size);
          padded.resize(size + AV_INPUT_BUFFER_PADDING_SIZE);
          uint8_t* parsed = nullptr;
          int parsed_size = 0;
          int result = av_parser_parse2(parser, codec, &parsed, &parsed_size,
              padded.data(), static_cast<int>(size), AV_NOPTS_VALUE, AV_NOPTS_VALUE, 0);
          width = parser->width;
          height = parser->height;
          av_parser_close(parser);
          avcodec_free_context(&codec);
          Check(result, "Parse H.264 keyframe");
          video_header_.assign(data, data + size);
        } else {
          if (size < 10 || data[3] != 0x9d || data[4] != 0x01 || data[5] != 0x2a)
            throw std::runtime_error("Invalid VP8 keyframe");
          width = (data[6] | (data[7] << 8)) & 0x3fff;
          height = (data[8] | (data[9] << 8)) & 0x3fff;
        }
        if (!width || !height) throw std::runtime_error("Invalid video dimensions");
        Open(width, height);
      } else {
        Open(0, 0);
      }
      pending_.push_back(std::move(packet));
      std::stable_sort(pending_.begin(), pending_.end(),
                       [](const Packet& a, const Packet& b) { return a.time_us < b.time_us; });
      origin_ = pending_.front().time_us;
      for (const auto& p : pending_) Mux(p);
      pending_.clear();
    } else {
      Mux(packet);
    }
  } catch (const std::exception& error) {
    failed_ = true;
    std::cerr << "Error recording: " << error.what() << std::endl;
  }
}

void Recording::Mux(const Packet& input) {
  if (input.time_us < origin_) return;
  AVPacket* packet = av_packet_alloc();
  if (!packet) throw std::bad_alloc();
  int result = av_new_packet(packet, static_cast<int>(input.data.size()));
  if (result < 0) { av_packet_free(&packet); Check(result, "Allocate packet"); }
  std::memcpy(packet->data, input.data.data(), input.data.size());
  packet->stream_index = input.video ? video_index_ : audio_index_;
  AVStream* stream = context_->streams[packet->stream_index];
  auto& last = input.video ? last_video_ : last_audio_;
  int64_t pts = av_rescale_q(input.time_us - origin_, AVRational{1, 1000000}, stream->time_base);
  // RTCP clock corrections must never make mux timestamps go backwards.
  pts = std::max(pts, last + 1);
  last = pts;
  packet->pts = packet->dts = pts;
  if (input.key || !input.video) packet->flags |= AV_PKT_FLAG_KEY;
  result = av_interleaved_write_frame(context_, packet);
  av_packet_free(&packet);
  Check(result, "Write packet");
}

void Recording::Close() {
  if (!context_) return;
  int result = header_written_ ? av_write_trailer(context_) : 0;
  int io_result = context_->pb ? avio_closep(&context_->pb) : 0;
  avformat_free_context(context_);
  context_ = nullptr;
  Check(result, "Finalize incoming stream");
  Check(io_result, "Close incoming stream");
}

bool Recording::Finish() {
  if (finished_) return !failed_;
  finished_ = true;
  const bool had_stream = header_written_;
  try { Close(); }
  catch (const std::exception& error) {
    failed_ = true;
    std::cerr << "Error: " << error.what() << std::endl;
  }
  if (!had_stream) {
    std::cout << "Recording stopped: no decodable frames received" << std::endl;
    std::error_code ignored;
    std::filesystem::remove(directory_, ignored);
    return !failed_;
  }
  // Keep incoming packets untouched in MKV. Copy H.264 video to MP4;
  // transcode VP8 and Opus for broad player compatibility after graceful close.
  // No shell is used; paths (including spaces) are individual arguments.
  std::cout << "Finalizing recording" << std::endl;
  std::vector<std::string> args = {
      "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-n",
      "-i", intermediate_.string(), "-map", "0:v?", "-map", "0:a?"};
  if (video_) {
    if (h264_) args.insert(args.end(), {"-c:v", "copy"});
    else args.insert(args.end(), {"-c:v", "libx264", "-preset", "veryfast",
        "-crf", "23", "-pix_fmt", "yuv420p", "-fps_mode", "vfr"});
  }
  args.insert(args.end(), {"-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", output_.string()});
  std::vector<char*> argv;
  for (auto& arg : args) argv.push_back(arg.data());
  argv.push_back(nullptr);
  pid_t child = -1;
  int result = posix_spawnp(&child, "ffmpeg", nullptr, nullptr, argv.data(), environ);
  int status = 0;
  pid_t waited = -1;
  if (result == 0) {
    do { waited = waitpid(child, &status, 0); } while (waited < 0 && errno == EINTR);
  }
  if (result || waited < 0 || !WIFEXITED(status) || WEXITSTATUS(status)) {
    failed_ = true;
    std::cerr << "Error finalizing MP4; incoming stream retained at " << intermediate_ << std::endl;
  } else {
    std::cout << "Recording stopped\nOutput filename: " << output_ << std::endl;
    if (!failed_) {
      std::error_code ignored;
      std::filesystem::remove(intermediate_, ignored);
      std::filesystem::remove(directory_, ignored);
    }
  }
  return !failed_;
}
