/* Linux diagnostic-only LD_PRELOAD observer. No production or SQLite changes. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

struct row {
  int fd, tid, kind, rc, error, identity_error, scheduler_ok;
  uint64_t begin, end, cpu_begin, cpu_end, run_begin, run_end, wait_begin, wait_end;
  uint64_t dev, ino, mode;
  char path[512];
};
static struct row *rows;
static size_t capacity = 131072;
static _Atomic size_t total;
static int (*real_fsync)(int), (*real_fdatasync)(int);
static const char *output;
static uint64_t delay_ns;
static uint64_t now(clockid_t clock) {
  struct timespec t;
  if (clock_gettime(clock,&t)) _exit(121);
  return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;
}
static int scheduler(int tid, uint64_t *run, uint64_t *wait) {
  char path[80], text[256];
  snprintf(path,sizeof path,"/proc/self/task/%d/schedstat",tid);
  int fd=open(path,O_RDONLY|O_CLOEXEC);
  if(fd<0) return 0;
  ssize_t n=read(fd,text,sizeof(text)-1);close(fd);
  if(n<=0) return 0;
  text[n]=0;
  return sscanf(text,"%"SCNu64" %"SCNu64,run,wait)==2;
}
__attribute__((constructor)) static void init(void) {
  real_fsync=dlsym(RTLD_NEXT,"fsync");real_fdatasync=dlsym(RTLD_NEXT,"fdatasync");
  output=getenv("MQTT_SYNC_OUTPUT");
  const char *cap=getenv("MQTT_SYNC_CAPACITY"), *delay=getenv("MQTT_SYNC_DELAY_NS");
  if(cap) capacity=strtoull(cap,NULL,10);
  if(delay) delay_ns=strtoull(delay,NULL,10);
  if(!output || !real_fsync || !real_fdatasync || capacity<1 || capacity>1048576) _exit(120);
  rows=calloc(capacity,sizeof *rows);
  if(!rows) _exit(120);
  /* Fault in storage before calls under observation. */
  for(size_t i=0;i<capacity*sizeof *rows;i+=4096) ((volatile char *)rows)[i]=0;
}
static int observe(int fd, int kind) {
  int saved=errno;
  size_t i=atomic_fetch_add(&total,1);
  struct row local={0}, *r=i<capacity ? &rows[i] : &local;
  r->fd=fd;r->tid=(int)syscall(SYS_gettid);r->kind=kind;
  char path[80];struct stat st;
  snprintf(path,sizeof path,"/proc/self/fd/%d",fd);
  ssize_t n=readlink(path,r->path,sizeof(r->path)-1);
  if(n<0 || n==(ssize_t)sizeof(r->path)-1) r->identity_error=1;
  else r->path[n]=0;
  if(fstat(fd,&st)) r->identity_error=1;
  else {r->dev=st.st_dev;r->ino=st.st_ino;r->mode=st.st_mode;}
  r->scheduler_ok=scheduler(r->tid,&r->run_begin,&r->wait_begin);
  r->cpu_begin=now(CLOCK_THREAD_CPUTIME_ID);r->begin=now(CLOCK_MONOTONIC);
  if(delay_ns) {struct timespec d={delay_ns/1000000000,delay_ns%1000000000};while(nanosleep(&d,&d)&&errno==EINTR){}}
  errno=saved;
  r->rc=(kind==0 ? real_fsync : real_fdatasync)(fd);
  int error=errno;
  r->end=now(CLOCK_MONOTONIC);r->cpu_end=now(CLOCK_THREAD_CPUTIME_ID);
  struct stat after;
  if(fstat(fd,&after) || (uint64_t)after.st_dev!=r->dev || (uint64_t)after.st_ino!=r->ino) r->identity_error=1;
  r->error=error;r->scheduler_ok &= scheduler(r->tid,&r->run_end,&r->wait_end);
  errno=error;return r->rc;
}
int fsync(int fd) {return observe(fd,0);}
int fdatasync(int fd) {return observe(fd,1);}
static void escaped(FILE *f,const char *s) {
  fputc('"',f);
  for(;*s;s++) {
    unsigned char c=*s;
    if(c=='"'||c=='\\') {fputc('\\',f);fputc(c,f);}
    else if(c<32 || c>=127) fprintf(f,"\\u%04x",c);
    else fputc(c,f);
  }
  fputc('"',f);
}
__attribute__((destructor)) static void finish(void) {
  size_t n=atomic_load(&total), kept=n<capacity?n:capacity;
  int fd=open(output,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
  if(fd<0) _exit(122);
  FILE *f=fdopen(fd,"w");if(!f) _exit(122);
  for(size_t i=0;i<kept;i++) {
    struct row *r=&rows[i];
    fprintf(f,"{\"event\":\"sync\",\"index\":%zu,\"call\":\"%s\",\"tid\":%d,\"fd\":%d,\"rc\":%d,\"errno\":%d,\"identity_error\":%d,\"scheduler_ok\":%d,\"begin_ns\":%"PRIu64",\"end_ns\":%"PRIu64",\"cpu_begin_ns\":%"PRIu64",\"cpu_end_ns\":%"PRIu64",\"run_begin_ns\":%"PRIu64",\"run_end_ns\":%"PRIu64",\"wait_begin_ns\":%"PRIu64",\"wait_end_ns\":%"PRIu64",\"dev\":%"PRIu64",\"inode\":%"PRIu64",\"mode\":%"PRIu64",\"path\":",i,r->kind?"fdatasync":"fsync",r->tid,r->fd,r->rc,r->error,r->identity_error,r->scheduler_ok,r->begin,r->end,r->cpu_begin,r->cpu_end,r->run_begin,r->run_end,r->wait_begin,r->wait_end,r->dev,r->ino,r->mode);
    escaped(f,r->path);fprintf(f,"}\n");
  }
  fprintf(f,"{\"event\":\"buffer\",\"capacity\":%zu,\"total\":%zu,\"lost\":%zu,\"injected_delay_ns\":%"PRIu64"}\n",capacity,n,n-kept,delay_ns);
  if(fflush(f) || ferror(f) || fclose(f)) _exit(122);
  if(n>capacity) _exit(123);
  if(n==0) _exit(124);
}
