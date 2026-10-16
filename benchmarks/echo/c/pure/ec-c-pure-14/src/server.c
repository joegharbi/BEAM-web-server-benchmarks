/* TCP echo server in C: one accept loop, an OS thread per connection, every byte received is sent back
 * until the client closes. The same behaviour as the Erlang and Java echo servers (benchmarks/echo/). */
#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#define BUF_SIZE 65536

/* Server contract (framework README): stop on SIGTERM. In a container the server is process 1, and the
 * kernel ignores SIGTERM for process 1 unless the program handles it, so it is handled here. */
static void stop(int sig)
{
    (void)sig;
    _exit(0);
}

static void *serve(void *arg)
{
    int conn = (int)(long)arg;
    char buf[BUF_SIZE];
    ssize_t n;
    while ((n = recv(conn, buf, sizeof buf, 0)) > 0) {
        ssize_t sent = 0;
        while (sent < n) {                       /* send can write less than asked */
            ssize_t w = send(conn, buf + sent, (size_t)(n - sent), MSG_NOSIGNAL);
            if (w < 0) {
                if (errno == EINTR) continue;
                goto done;
            }
            sent += w;
        }
    }
done:
    close(conn);
    return NULL;
}

int main(void)
{
    /* Server contract (framework README): listen on PORT, 8001 when it is unset */
    const char *p = getenv("PORT");
    int port = p ? atoi(p) : 8001;
    int listener = socket(AF_INET, SOCK_STREAM, 0);
    int one = 1;
    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    struct sockaddr_in addr = {.sin_family = AF_INET, .sin_port = htons((uint16_t)port), .sin_addr.s_addr = htonl(INADDR_ANY)};
    if (bind(listener, (struct sockaddr *)&addr, sizeof addr) < 0 || listen(listener, 1024) < 0) {
        perror("listen");
        return 1;
    }
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, stop);
    signal(SIGINT, stop);
    for (;;) {
        int conn = accept(listener, NULL, NULL);
        if (conn < 0) {
            if (errno != EINTR) {
                perror("accept");
                sleep(1);
            }
            continue;
        }
        pthread_t t;
        if (pthread_create(&t, NULL, serve, (void *)(long)conn) != 0) {
            close(conn);
            continue;
        }
        pthread_detach(t);
    }
}
