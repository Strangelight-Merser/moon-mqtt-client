/* Force the server to accept before connect returns to the observer. */
#include "socket_observer.c"
static int listener_fd, accepted_fd;
static uint64_t accepted_ns, returned_ns;
static pthread_t accept_thread;
static int (*system_connect)(int,const struct sockaddr*,socklen_t);
static void *accept_peer(void *unused) {
  (void)unused;
  accepted_fd=accept(listener_fd,NULL,NULL);
  if(accepted_fd<0) _exit(1);
  accepted_ns=clock_ns();
  /* No interposed close while observed_connect holds the observer mutex. */
  return NULL;
}
static int connect_then_accept(int fd,const struct sockaddr *addr,socklen_t length) {
  int rc=system_connect(fd,addr,length);int saved=errno;
  if(rc || pthread_join(accept_thread,NULL)) _exit(2);
  returned_ns=clock_ns();errno=saved;return rc;
}
int main(void) {
  listener_fd=socket(AF_INET,SOCK_STREAM,0);
  struct sockaddr_in address={0};address.sin_family=AF_INET;address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
  socklen_t length=sizeof address;
  if(bind(listener_fd,(void*)&address,length) || listen(listener_fd,1) || getsockname(listener_fd,(void*)&address,&length)) return 3;
  int client=socket(AF_INET,SOCK_STREAM,0);
  if(pthread_create(&accept_thread,NULL,accept_peer,NULL)) return 4;
  system_connect=original_connect;original_connect=connect_then_accept;enabled=1;
  int rc=observed_connect(client,(void*)&address,length);
  observed_close(client);enabled=0;
  original_close(accepted_fd);original_close(listener_fd);
  if(rc || total!=2 || rows[0].kind!=0 || rows[1].kind!=2) return 5;
  printf("{\"connect_ns\":%"PRIu64",\"accept_ns\":%"PRIu64",\"return_ns\":%"PRIu64",\"close_ns\":%"PRIu64"}\n",rows[0].ns,accepted_ns,returned_ns,rows[1].ns);
  return !(rows[0].ns<accepted_ns && accepted_ns<returned_ns && returned_ns<rows[1].ns);
}
