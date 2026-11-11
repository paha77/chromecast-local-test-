#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

struct AVFormatContext;

// Independent of Cast so the exact mux/finalize path can be checked offline.
class Recording {
 public:
  Recording(bool video, bool audio, int channels, int sample_rate, bool h264 = false);
  ~Recording();
  Recording(const Recording&) = delete;
  Recording& operator=(const Recording&) = delete;
  void Write(bool video, const uint8_t* data, size_t size,
             int64_t time_us, bool key);
  bool Finish();

 private:
  struct Packet {
    bool video;
    std::vector<uint8_t> data;
    int64_t time_us;
    bool key;
  };
  void Open(int width, int height);
  void Mux(const Packet& packet);
  void Close();
  bool video_, audio_, finished_ = false, failed_ = false, header_written_ = false;
  int channels_, sample_rate_, video_index_ = -1, audio_index_ = -1;
  int64_t origin_ = 0, last_video_ = -1, last_audio_ = -1;
  AVFormatContext* context_ = nullptr;
  std::filesystem::path directory_, intermediate_, output_;
  std::vector<Packet> pending_;
  bool h264_;
  std::vector<uint8_t> video_header_;
};
