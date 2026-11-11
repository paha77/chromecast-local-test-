#include "cast/standalone_receiver/streaming_playback_controller.h"

#include <chrono>
#include <iostream>
#include <stdexcept>
#include <vector>
#include "cast/streaming/public/receiver.h"

namespace openscreen::cast {
class FrameRecorder final : public Receiver::Consumer {
 public:
  FrameRecorder(Receiver& receiver, Recording& recording, bool video)
      : receiver_(receiver), recording_(recording), video_(video) {
    receiver_.SetPlayerProcessingTime(Clock::duration::zero());
    receiver_.SetConsumer(this);
    if (video_) receiver_.RequestKeyFrame();
  }
  ~FrameRecorder() override { receiver_.SetConsumer(nullptr); }
  void OnFramesReady(size_t size) override {
    buffer_.resize(size);
    const auto frame = receiver_.ConsumeNextFrame(buffer_);
    // Open Screen maps audio and video RTCP timestamps onto this shared
    // reference clock. Independent RTP origins would lose their A/V offset.
    const auto us = std::chrono::duration_cast<std::chrono::microseconds>(
        frame.reference_time.time_since_epoch()).count();
    recording_.Write(video_, frame.data.data(), frame.data.size(), us,
                     frame.dependency == EncodedFrame::Dependency::kKeyFrame);
    receiver_.ReportPlayoutEvent(frame.frame_id, frame.rtp_timestamp, Clock::now());
  }
 private:
  Receiver& receiver_;
  Recording& recording_;
  bool video_;
  std::vector<uint8_t> buffer_;
};

StreamingPlaybackController::Client::~Client() = default;
StreamingPlaybackController::StreamingPlaybackController(Client* client, bool)
    : client_(client) {}
StreamingPlaybackController::~StreamingPlaybackController() { Stop(); }
void StreamingPlaybackController::Stop() {
  audio_.reset();
  video_.reset();
  recording_.reset();
}
void StreamingPlaybackController::OnNegotiated(
    const ReceiverSession*, ReceiverSession::ConfiguredReceivers receivers) {
  Stop();
  try {
    const bool h264 = receivers.video_config.codec == VideoCodec::kH264;
    if (receivers.video_receiver && !h264 && receivers.video_config.codec != VideoCodec::kVp8)
      throw std::runtime_error("Recorder requires VP8 or H.264 video");
    if (receivers.audio_receiver && receivers.audio_config.codec != AudioCodec::kOpus)
      throw std::runtime_error("Recorder requires Opus audio");
    const auto* audio = receivers.audio_receiver.get();
    int channels = audio ? audio->config().channels : 2;
    int rate = audio ? audio->config().rtp_timebase : 48000;
    if (audio && (channels < 1 || channels > 2 || rate != 48000))
      throw std::runtime_error("Recorder requires 48kHz mono/stereo Opus");
    std::cout << "Chrome connected: negotiated Cast streaming session (video="
              << (receivers.video_receiver ? (h264 ? "H.264" : "VP8") : "none") << ", audio="
              << (audio ? "Opus" : "none") << ")" << std::endl;
    recording_ = std::make_unique<Recording>(receivers.video_receiver != nullptr,
                                            audio != nullptr, channels, rate, h264);
    if (audio) audio_ = std::make_unique<FrameRecorder>(*receivers.audio_receiver, *recording_, false);
    if (receivers.video_receiver)
      video_ = std::make_unique<FrameRecorder>(*receivers.video_receiver, *recording_, true);
  } catch (const std::exception& error) {
    std::cerr << "Error starting recording: " << error.what() << std::endl;
    Stop();
  }
}
void StreamingPlaybackController::OnReceiversDestroying(
    const ReceiverSession*, ReceiversDestroyingReason) { Stop(); }
void StreamingPlaybackController::OnRemotingNegotiated(
    const ReceiverSession*, ReceiverSession::RemotingNegotiation) {
  std::cerr << "Error: media remoting unsupported; select Cast tab" << std::endl;
}
void StreamingPlaybackController::OnError(const ReceiverSession*, const Error& error) {
  client_->OnPlaybackError(this, error);
}
}
