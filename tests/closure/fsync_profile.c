/* External diagnostic for our own macOS executable; no candidate rebuild. */
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/resource.h>
#define CAPACITY 200000
struct row { int fd, result, directory; int64_t begin, end; };
static struct row rows[CAPACITY];
static _Atomic unsigned used;
static int64_t now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return (int64_t)t.tv_sec*1000000000LL+t.tv_nsec; }
static int observe_fsync(int fd) {
    /* dyld excludes references originating in the interposing image. */
    int64_t begin=now(); int result=fsync(fd); int saved_errno=errno; int64_t end=now();
    struct stat info; int directory=fstat(fd,&info)==0 && S_ISDIR(info.st_mode);
    unsigned index=atomic_fetch_add(&used,1);
    if(index<CAPACITY)rows[index]=(struct row){fd,result,directory,begin,end};
    errno=saved_errno; return result;
}
__attribute__((used)) static struct { const void *replacement; const void *original; } interpose[]
__attribute__((section("__DATA,__interpose")))={{(void*)observe_fsync,(void*)fsync}};
__attribute__((destructor)) static void export_rows(void) {
    const char *path=getenv("MQTT_CLOSURE_FSYNC"); if(!path)return;
    FILE *f=fopen(path,"w"); if(!f)return;
    unsigned count=atomic_load(&used);
    struct rusage usage;getrusage(RUSAGE_SELF,&usage);
    fprintf(f,"{\"event\":\"profile\",\"capacity\":%u,\"records\":%u,\"lost\":%u,\"voluntary_switches\":%ld,\"involuntary_switches\":%ld}\n",CAPACITY,count,count>CAPACITY?count-CAPACITY:0,usage.ru_nvcsw,usage.ru_nivcsw);
    for(unsigned i=0;i<count && i<CAPACITY;i++) { struct row *r=&rows[i];fprintf(f,"{\"event\":\"fsync\",\"fd\":%d,\"directory\":%d,\"begin_ns\":%lld,\"end_ns\":%lld,\"result\":%d}\n",r->fd,r->directory,(long long)r->begin,(long long)r->end,r->result); }
    fclose(f);
}
