/* Test executable only. Public SQLite trace API; no library/SQLite patch. */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <time.h>
#include <unistd.h>
#include <fcntl.h>
typedef struct sqlite3 sqlite3;
typedef struct sqlite3_stmt sqlite3_stmt;
typedef struct sqlite3_api_routines sqlite3_api_routines;
extern int sqlite3_auto_extension(void (*)(void));
extern int sqlite3_trace_v2(sqlite3 *, unsigned, int (*)(unsigned,void*,void*,void*), void*);
extern const char *sqlite3_sql(sqlite3_stmt*);
extern char *sqlite3_expanded_sql(sqlite3_stmt*);
extern void sqlite3_free(void*);
extern int sqlite3_get_autocommit(sqlite3*);
extern int sqlite3_errcode(sqlite3*);
static int trace_fd = -1;
static const char *crash_stage;
struct context { sqlite3 *db; char id[128]; const char *stage; };
int64_t moon_bench_now_ns(void) {
  struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
  return (int64_t)t.tv_sec*1000000000LL+t.tv_nsec;
}
static void record(struct context *c, const char *stage) {
  if (trace_fd >= 0) dprintf(trace_fd,"{\"id\":\"%s\",\"stage\":\"%s\",\"ns\":%lld}\n",c->id,stage,(long long)moon_bench_now_ns());
  if (crash_stage && !strcmp(crash_stage, stage)) _exit(86);
}
static void identify(struct context *c, sqlite3_stmt *s, const char *stage) {
  char *sql=sqlite3_expanded_sql(s), *start=sql ? strstr(sql,"bench-") : NULL;
  c->id[0]=0; c->stage=NULL;
  if (start) {
    size_t n=strspn(start,"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_/");
    if (n>0 && n<sizeof(c->id)) { memcpy(c->id,start,n); c->id[n]=0; c->stage=stage; }
  }
  sqlite3_free(sql);
}
static int trace(unsigned event, void *ctx, void *p, void *unused) {
  (void)unused; struct context *c=ctx;
  if (event==8) { free(c); return 0; }
  sqlite3_stmt *s=p; const char *sql=sqlite3_sql(s);
  if (!sql) return 0;
  if (event==1 && !strncmp(sql,"DELETE FROM durable_outbox",26)) {
    identify(c,s,"delete_commit"); if(c->stage) record(c,"before_delete");
  }
  if (event==1 && !strcmp(sql,"COMMIT") && c->stage && !strcmp(c->stage,"admission_commit")) record(c,"before_admission_commit");
  if(event!=2) return 0;
  if (!strncmp(sql,"INSERT INTO durable_outbox",26)) identify(c,s,"admission_commit");
  else if (!strncmp(sql,"UPDATE durable_outbox SET ever_started = 1",41)) identify(c,s,"possible_write_commit");
  else if (!strncmp(sql,"DELETE FROM durable_outbox",26)) identify(c,s,"delete_commit");
  else if (!strcmp(sql,"ROLLBACK")) { c->stage=NULL; }
  else if (!strcmp(sql,"COMMIT") && c->stage) {
    if (sqlite3_get_autocommit(c->db) && (sqlite3_errcode(c->db)==0 || sqlite3_errcode(c->db)==101)) record(c,c->stage);
    c->stage=NULL;
  }
  return 0;
}
static int extension(sqlite3 *db,char **err,const sqlite3_api_routines *api) {
  (void)err;(void)api; struct context *c=calloc(1,sizeof(*c));
  if(!c) return 7; c->db=db;
  return sqlite3_trace_v2(db,1|2|8,trace,c);
}
int moon_bench_trace_enable(void) {
  const char *path=getenv("MQTT_BENCH_TRACE"); if(!path) return 1;
  trace_fd=open(path,O_WRONLY|O_CREAT|O_EXCL,0600); if(trace_fd<0) return 2;
  crash_stage=getenv("MQTT_BENCH_CRASH");
  return sqlite3_auto_extension((void(*)(void))extension);
}
void moon_bench_trace_close(void) { if(trace_fd>=0) {close(trace_fd);trace_fd=-1;} }
