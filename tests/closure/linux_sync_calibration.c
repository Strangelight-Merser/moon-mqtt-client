#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <signal.h>
#include <sys/resource.h>
#include <unistd.h>
int main(void) {
  int fd=open("first.db",O_CREAT|O_RDWR|O_TRUNC,0600);assert(fd>=0);
  assert(write(fd,"test",4)==4);errno=E2BIG;assert(fsync(fd)==0 && errno==E2BIG);
  errno=E2BIG;assert(fdatasync(fd)==0 && errno==E2BIG);int old=fd;close(fd);
  fd=open("second.db",O_CREAT|O_RDWR|O_TRUNC,0600);assert(fd==old);
  assert(write(fd,"test",4)==4);assert(fdatasync(fd)==0);close(fd);
  fd=open(".",O_RDONLY|O_DIRECTORY);assert(fd>=0);assert(fsync(fd)==0);close(fd);
  errno=0;assert(fsync(-1)==-1 && errno==EBADF);
  errno=0;assert(fdatasync(-1)==-1 && errno==EBADF);
  if(getenv("MQTT_SYNC_FAIL_FLUSH")) {
    struct rlimit limit={0,0};signal(SIGXFSZ,SIG_IGN);assert(setrlimit(RLIMIT_FSIZE,&limit)==0);
  }
  return 0;
}
