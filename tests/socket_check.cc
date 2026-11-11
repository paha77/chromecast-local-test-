// One-shot diagnostic: separate macOS TCP access from Open Screen and TLS.
#include <arpa/inet.h>
#include <fcntl.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>
#include <cstdio>
#include <cstring>

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  int listener = socket(AF_INET, SOCK_STREAM, 0);
  int reuse = 1;
  setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
  sockaddr_in address{};
  address.sin_len = sizeof(address);
  address.sin_family = AF_INET;
  address.sin_port = htons(18010);
  if (inet_pton(AF_INET, argv[1], &address.sin_addr) != 1) return 2;
  if (bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) ||
      listen(listener, 1)) { std::perror("listen"); return 1; }
  std::puts("Socket check listening on TCP 18010");
  std::fflush(stdout);
  int client = accept(listener, nullptr, nullptr);
  if (client < 0) { std::perror("accept"); return 1; }
  int flags = fcntl(client, F_GETFL, 0);
  if (fcntl(client, F_SETFL, flags | O_NONBLOCK)) { std::perror("fcntl"); return 1; }
  pollfd ready{client, POLLIN, 0};
  poll(&ready, 1, 5000);
  char buffer[1024];
  auto count = recv(client, buffer, sizeof(buffer), 0);
  if (count < 0) std::perror("recv");
  else std::printf("Received %zd bytes on accepted socket\n", count);
  const char reply[] = "HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK";
  if (count > 0) send(client, reply, std::strlen(reply), 0);
  close(client);
  close(listener);
  return count > 0 ? 0 : 1;
}
