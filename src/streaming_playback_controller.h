// Replacement for Open Screen's demo player; protocol code stays upstream.
#ifndef CAST_STANDALONE_RECEIVER_STREAMING_PLAYBACK_CONTROLLER_H_
#define CAST_STANDALONE_RECEIVER_STREAMING_PLAYBACK_CONTROLLER_H_

#include <memory>
#include "cast/streaming/public/receiver_session.h"
#include "recording.h"

namespace openscreen::cast {
class FrameRecorder;
class StreamingPlaybackController final : public ReceiverSession::Client {
 public:
  class Client {
   public:
    virtual void OnPlaybackError(StreamingPlaybackController*, const Error&) = 0;
   protected:
    virtual ~Client();
  };
  StreamingPlaybackController(Client* client, bool enable_input_events);
  ~StreamingPlaybackController() override;
  void OnNegotiated(const ReceiverSession*, ReceiverSession::ConfiguredReceivers) override;
  void OnRemotingNegotiated(const ReceiverSession*, ReceiverSession::RemotingNegotiation) override;
  void OnReceiversDestroying(const ReceiverSession*, ReceiversDestroyingReason) override;
  void OnError(const ReceiverSession*, const Error&) override;
 private:
  void Stop();
  Client* client_;
  std::unique_ptr<Recording> recording_;
  std::unique_ptr<FrameRecorder> audio_, video_;
};
}
#endif
