/* External connect-lifetime evidence. No MQTT client code or wire changes. */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
#ifdef __APPLE__
#include <mach-o/dyld.h>
#endif
#define FDS 65536
#define LIMIT 65536
struct endpoint { char ip[INET6_ADDRSTRLEN]; int port; };
struct event { uint64_t ns, life, dial; int fd, kind, rc, error, soerror; struct endpoint local, remote; };
static struct event rows[LIMIT];
static uint64_t lives[FDS], dials[FDS], sequence, dial_sequence, active_dial;
static struct addrinfo *active_resolution;
static struct endpoint remotes[FDS];
static size_t total, capacity=LIMIT;
static int enabled;
static const char *output;
static pthread_mutex_t lock=PTHREAD_MUTEX_INITIALIZER;
static int (*original_connect)(int,const struct sockaddr*,socklen_t);
static int (*original_getsockopt)(int,int,int,void*,socklen_t*);
static int (*original_close)(int);
static int (*original_getaddrinfo)(const char*,const char*,const struct addrinfo*,struct addrinfo**);
static void (*original_freeaddrinfo)(struct addrinfo*);
static uint64_t clock_ns(void) { struct timespec t; if(clock_gettime(CLOCK_MONOTONIC,&t)) _exit(120); return (uint64_t)t.tv_sec*1000000000+t.tv_nsec; }
static struct endpoint endpoint(const struct sockaddr *a) {
  struct endpoint e={"",0};
  if(a->sa_family==AF_INET) { const struct sockaddr_in *p=(const void*)a; inet_ntop(AF_INET,&p->sin_addr,e.ip,sizeof e.ip);e.port=ntohs(p->sin_port); }
  if(a->sa_family==AF_INET6) { const struct sockaddr_in6 *p=(const void*)a; inet_ntop(AF_INET6,&p->sin6_addr,e.ip,sizeof e.ip);e.port=ntohs(p->sin6_port); }
  return e;
}
static void record(int fd,int kind,int rc,int error,int soerror) {
  if(fd<0 || fd>=FDS || !lives[fd]) return;
  struct sockaddr_storage addr; socklen_t n=sizeof addr;
  struct event r={0};r.ns=clock_ns();r.life=lives[fd];r.dial=dials[fd];r.fd=fd;r.kind=kind;r.rc=rc;r.error=error;r.soerror=soerror;r.remote=remotes[fd];
  if(!getsockname(fd,(void*)&addr,&n)) r.local=endpoint((void*)&addr);
  if(total<capacity) rows[total]=r;
  total++;
}
__attribute__((constructor)) static void initialize(void) {
#ifdef __APPLE__
  original_connect=connect;original_getsockopt=getsockopt;original_close=close;
#else
  original_connect=dlsym(RTLD_NEXT,"connect"); original_getsockopt=dlsym(RTLD_NEXT,"getsockopt");original_close=dlsym(RTLD_NEXT,"close");
#endif
#ifdef __APPLE__
  original_getaddrinfo=getaddrinfo;original_freeaddrinfo=freeaddrinfo;
#else
  original_getaddrinfo=dlsym(RTLD_NEXT,"getaddrinfo");original_freeaddrinfo=dlsym(RTLD_NEXT,"freeaddrinfo");
#endif
  output=getenv("MQTT_DIAL_OUTPUT");const char *expected=getenv("MQTT_DIAL_EXECUTABLE");
  if(!output || !expected) return;
  char path[4096];
#ifdef __APPLE__
  uint32_t n=sizeof path; if(_NSGetExecutablePath(path,&n)) _exit(120);
#else
  ssize_t n=readlink("/proc/self/exe",path,sizeof(path)-1); if(n<0) _exit(120);path[n]=0;
#endif
  const char *name=strrchr(path,'/');name=name?name+1:path;
  if(strcmp(name,expected)) return;
  const char *cap=getenv("MQTT_DIAL_CAPACITY");if(cap) capacity=strtoul(cap,NULL,10);
  if(!capacity || capacity>LIMIT || !original_connect || !original_close || !original_getsockopt) _exit(120);
  enabled=1;
}
static int observed_connect(int fd,const struct sockaddr *addr,socklen_t length) {
#ifdef __APPLE__
  if(!original_connect) original_connect=connect;
#else
  if(!original_connect) original_connect=dlsym(RTLD_NEXT,"connect");
#endif
  if(!enabled) return original_connect(fd,addr,length);
  int saved=errno;
  pthread_mutex_lock(&lock);
  if(fd<0 || fd>=FDS || (addr->sa_family!=AF_INET && addr->sa_family!=AF_INET6)) {pthread_mutex_unlock(&lock);errno=saved;return original_connect(fd,addr,length);}
  if(lives[fd]) _exit(125);
  lives[fd]=++sequence;dials[fd]=active_dial;remotes[fd]=endpoint(addr);
  errno=saved;int rc=original_connect(fd,addr,length);int error=errno;
  record(fd,0,rc,rc<0?error:0,0);pthread_mutex_unlock(&lock);errno=error;return rc;
}
static int observed_getsockopt(int fd,int level,int option,void *value,socklen_t *length) {
#ifdef __APPLE__
  if(!original_getsockopt) original_getsockopt=getsockopt;
#else
  if(!original_getsockopt) original_getsockopt=dlsym(RTLD_NEXT,"getsockopt");
#endif
  int rc=original_getsockopt(fd,level,option,value,length);int error=errno;
  if(enabled && level==SOL_SOCKET && option==SO_ERROR) {pthread_mutex_lock(&lock);record(fd,1,rc,rc<0?error:0,rc==0?*(int*)value:-1);pthread_mutex_unlock(&lock);}
  errno=error;return rc;
}
static int observed_close(int fd) {
#ifdef __APPLE__
  if(!original_close) original_close=close;
#else
  if(!original_close) original_close=dlsym(RTLD_NEXT,"close");
#endif
  int saved=errno;
  if(!enabled) return original_close(fd);
  pthread_mutex_lock(&lock);
  size_t index=total;record(fd,2,0,0,0);
  errno=saved;int rc=original_close(fd);int error=errno;
  if(total>index && index<capacity) {rows[index].rc=rc;rows[index].error=rc<0?error:0;}
  if(fd>=0 && fd<FDS) lives[fd]=0;
  pthread_mutex_unlock(&lock);errno=error;return rc;
}
static void resolution_record(int kind,int rc) {
  struct event r={0};r.ns=clock_ns();r.dial=active_dial;r.fd=-1;r.kind=kind;r.rc=rc;
  if(total<capacity) rows[total]=r;
  total++;
}
static int observed_getaddrinfo(const char *host,const char *service,const struct addrinfo *hints,struct addrinfo **result) {
#ifdef __APPLE__
  if(!original_getaddrinfo) original_getaddrinfo=getaddrinfo;
#else
  if(!original_getaddrinfo) original_getaddrinfo=dlsym(RTLD_NEXT,"getaddrinfo");
#endif
  int rc=original_getaddrinfo(host,service,hints,result);int saved=errno;
  if(enabled) {
    pthread_mutex_lock(&lock);
    if(active_dial) _exit(126); /* This observer only qualifies one serial client. */
    active_dial=++dial_sequence;active_resolution=rc==0?*result:NULL;
    resolution_record(3,rc);
    if(rc) {resolution_record(4,rc);active_dial=0;}
    pthread_mutex_unlock(&lock);
  }
  errno=saved;return rc;
}
static void observed_freeaddrinfo(struct addrinfo *result) {
#ifdef __APPLE__
  if(!original_freeaddrinfo) original_freeaddrinfo=freeaddrinfo;
#else
  if(!original_freeaddrinfo) original_freeaddrinfo=dlsym(RTLD_NEXT,"freeaddrinfo");
#endif
  int saved=errno;
  if(enabled) {
    pthread_mutex_lock(&lock);
    if(result==active_resolution && active_dial) {resolution_record(4,0);active_dial=0;active_resolution=NULL;}
    pthread_mutex_unlock(&lock);
  }
  errno=saved;original_freeaddrinfo(result);
}
#ifdef __APPLE__
#define INTERPOSE(replacement,original) __attribute__((used)) static struct {const void *replacement;const void *original;} interpose_##original __attribute__((section("__DATA,__interpose")))={(const void*)(uintptr_t)&replacement,(const void*)(uintptr_t)&original};
INTERPOSE(observed_connect,connect)
INTERPOSE(observed_getsockopt,getsockopt)
INTERPOSE(observed_close,close)
INTERPOSE(observed_getaddrinfo,getaddrinfo)
INTERPOSE(observed_freeaddrinfo,freeaddrinfo)
#else
int connect(int f,const struct sockaddr*a,socklen_t n){return observed_connect(f,a,n);}
int getsockopt(int f,int l,int o,void*v,socklen_t*n){return observed_getsockopt(f,l,o,v,n);}
int close(int f){return observed_close(f);}
int getaddrinfo(const char*h,const char*s,const struct addrinfo*a,struct addrinfo**r){return observed_getaddrinfo(h,s,a,r);}
void freeaddrinfo(struct addrinfo*a){observed_freeaddrinfo(a);}
#endif
__attribute__((destructor)) static void finish(void) {
  if(!enabled) return;
  enabled=0;
  int fd=open(output,O_WRONLY|O_CREAT|O_EXCL,0600);if(fd<0) _exit(122);
  FILE *f=fdopen(fd,"w");if(!f) _exit(122);
  for(size_t i=0;i<total && i<capacity;i++) {struct event*r=&rows[i];fprintf(f,"{\"index\":%zu,\"pid\":%d,\"ns\":%"PRIu64",\"life\":%"PRIu64",\"dial\":%"PRIu64",\"fd\":%d,\"kind\":%d,\"rc\":%d,\"errno\":%d,\"so_error\":%d,\"local\":[\"%s\",%d],\"remote\":[\"%s\",%d]}\n",i,getpid(),r->ns,r->life,r->dial,r->fd,r->kind,r->rc,r->error,r->soerror,r->local.ip,r->local.port,r->remote.ip,r->remote.port);}
  fprintf(f,"{\"event\":\"buffer\",\"pid\":%d,\"total\":%zu,\"lost\":%zu}\n",getpid(),total,total>capacity?total-capacity:0);
  if(fflush(f)||ferror(f)||fclose(f)) _exit(122);
  if(total>capacity) _exit(123);
  if(!total) _exit(124);
}
