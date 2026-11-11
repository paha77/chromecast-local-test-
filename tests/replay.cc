// Feed real encoded VP8/Opus fixture packets through the same recording class.
#include "recording.h"
#include <iostream>
extern "C" {
#include <libavformat/avformat.h>
#include <libavcodec/bsf.h>
}

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  AVFormatContext* input = nullptr;
  if (avformat_open_input(&input, argv[1], nullptr, nullptr) < 0 ||
      avformat_find_stream_info(input, nullptr) < 0) return 3;
  bool video = false, audio = false, h264 = false;
  AVBSFContext* filter = nullptr;
  for (unsigned i = 0; i < input->nb_streams; ++i) {
    video |= input->streams[i]->codecpar->codec_id == AV_CODEC_ID_VP8;
    if (input->streams[i]->codecpar->codec_id == AV_CODEC_ID_H264) {
      video = h264 = true;
      if (av_bsf_alloc(av_bsf_get_by_name("h264_mp4toannexb"), &filter) < 0 ||
          avcodec_parameters_copy(filter->par_in, input->streams[i]->codecpar) < 0 ||
          av_bsf_init(filter) < 0) return 5;
    }
    audio |= input->streams[i]->codecpar->codec_id == AV_CODEC_ID_OPUS;
  }
  Recording recording(video, audio, 2, 48000, h264);
  AVPacket* packet = av_packet_alloc();
  while (av_read_frame(input, packet) >= 0) {
    AVStream* stream = input->streams[packet->stream_index];
    const bool is_video = stream->codecpar->codec_type == AVMEDIA_TYPE_VIDEO;
    int64_t time = av_rescale_q(packet->pts, stream->time_base, AVRational{1, 1000000});
    // Exercise a real A/V offset, and a nonzero shared clock origin.
    time += 123456000000LL + (is_video ? 250000 : 0);
    if (is_video && filter) {
      if (av_bsf_send_packet(filter, packet) < 0 || av_bsf_receive_packet(filter, packet) < 0) return 6;
    }
    recording.Write(is_video, packet->data, packet->size, time,
                     (packet->flags & AV_PKT_FLAG_KEY) != 0);
    av_packet_unref(packet);
  }
  av_packet_free(&packet);
  av_bsf_free(&filter);
  avformat_close_input(&input);
  return recording.Finish() ? 0 : 4;
}
