// Use macOS's existing mDNS responder instead of a second raw multicast stack.
#include <arpa/inet.h>
#include <dns_sd.h>
#include <net/if.h>
#include <poll.h>
#include <signal.h>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <string>

namespace {
volatile sig_atomic_t stopping = 0;
int result = 0;
void Stop(int) { stopping = 1; }
void Registered(DNSServiceRef, DNSServiceFlags, DNSServiceErrorType error,
                const char*, const char*, const char*, void* ready_path) {
  if (error != kDNSServiceErr_NoError) {
    std::fprintf(stderr, "Error: Bonjour registration failed (%d)\n", error);
    result = 1;
    stopping = 1;
    return;
  }
  FILE* ready = std::fopen(static_cast<const char*>(ready_path), "w");
  if (!ready) {
    std::perror("Error writing Bonjour readiness file");
    result = 1;
    stopping = 1;
    return;
  }
  std::fclose(ready);
  std::puts("Bonjour advertisement registered: Local Recorder");
  std::fflush(stdout);
}
}

int main(int argc, char** argv) {
  if (argc != 4) return 2;  // interface, stable public ID, readiness filename
  unsigned index = if_nametoindex(argv[1]);
  if (!index) { std::perror("Error selecting Bonjour interface"); return 1; }
  const bool loopback = std::strcmp(argv[1], "lo0") == 0;
  if (loopback) index = kDNSServiceInterfaceIndexLocalOnly;
  TXTRecordRef txt;
  TXTRecordCreate(&txt, 0, nullptr);
  // The same Cast receiver fields as Open Screen's ReceiverInfo serializer.
  const char* fields[][2] = {
      {"id", argv[2]}, {"ve", "2"}, {"ca", "5"}, {"st", "0"},
      {"fn", "Local Recorder"}, {"md", "cast_standalone_receiver"}};
  for (const auto& field : fields) {
    auto error = TXTRecordSetValue(&txt, field[0], std::strlen(field[1]), field[1]);
    if (error) { TXTRecordDeallocate(&txt); return 1; }
  }
  DNSServiceRef service = nullptr;
  const std::string name = std::string("LocalRecorder-") + argv[2];
  auto error = DNSServiceRegister(
      &service, 0, index, name.c_str(), "_googlecast._tcp", "local.",
      loopback ? "localhost." : nullptr,
      htons(8010), TXTRecordGetLength(&txt), TXTRecordGetBytesPtr(&txt),
      Registered, argv[3]);
  TXTRecordDeallocate(&txt);
  if (error) {
    std::fprintf(stderr, "Error: cannot register Bonjour service (%d)\n", error);
    return 1;
  }
  signal(SIGINT, Stop);
  signal(SIGTERM, Stop);
  signal(SIGHUP, Stop);
  pollfd fd{DNSServiceRefSockFD(service), POLLIN, 0};
  while (!stopping) {
    int ready = poll(&fd, 1, 100);
    if (ready < 0 && errno == EINTR) continue;
    if (ready < 0 || (ready && !(fd.revents & POLLIN))) {
      std::fprintf(stderr, "Error: Bonjour connection closed\n");
      result = 1;
      break;
    }
    if (ready && (error = DNSServiceProcessResult(service)) != 0) {
      std::fprintf(stderr, "Error processing Bonjour events (%d)\n", error);
      result = 1;
      break;
    }
  }
  DNSServiceRefDeallocate(service);  // Withdraw the advertisement on shutdown.
  return result;
}
