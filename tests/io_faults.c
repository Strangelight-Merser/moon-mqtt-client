/* Test-only child-process syscall interposition. Never linked into the library.
 * Fail exactly one matching call beneath an explicit temporary directory. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static atomic_int fired = 0;
static int arm_prefix_exists(const char *root, const char *prefix) {
  if (!prefix) return 1;
  DIR *directory = opendir(root);
  if (!directory) return 0;
  struct dirent *entry;
  int found = 0;
  size_t length = strlen(prefix);
  while ((entry = readdir(directory))) {
    if (!strncmp(entry->d_name, prefix, length)) { found = 1; break; }
  }
  closedir(directory);
  return found;
}
static int selected(int fd, const char *operation, const char *syscall_name) {
  const char *root = getenv("MQTT_IO_ROOT"), *op = getenv("MQTT_IO_OPERATION");
  const char *arm = getenv("MQTT_IO_ARM"), *marker = getenv("MQTT_IO_MARKER");
  const char *contains = getenv("MQTT_IO_PATH_CONTAINS");
  const char *arm_prefix = getenv("MQTT_IO_ARM_PREFIX");
  const char *arm_path = getenv("MQTT_IO_ARM_PATH");
  const char *directory_only = getenv("MQTT_IO_DIRECTORY_ONLY");
  if (!root || !op || !marker || strcmp(op, operation) ||
      (arm && access(arm, F_OK))) return 0;
  char path[PATH_MAX];
#ifdef __APPLE__
  if (fcntl(fd, F_GETPATH, path)) return 0;
#else
  char link[64];
  snprintf(link, sizeof(link), "/proc/self/fd/%d", fd);
  ssize_t n = readlink(link, path, sizeof(path) - 1);
  if (n < 0) return 0;
  path[n] = 0;
#endif
  size_t length = strlen(root);
  if (strncmp(path, root, length) || (path[length] && path[length] != '/')) return 0;
  if (contains && !strstr(path, contains)) return 0;
  if (directory_only && strcmp(path, root)) return 0;
  if (!arm_prefix_exists(root, arm_prefix)) return 0;
  if (arm_path && access(arm_path, F_OK)) return 0;
  if (atomic_exchange(&fired, 1)) return 0;
  int log = open(marker, O_WRONLY | O_CREAT | O_APPEND, 0600);
  if (log >= 0) {
    dprintf(log, "%s EIO %s\n", syscall_name, path);
    close(log);
  }
  errno = EIO;
  return 1;
}

#ifdef __APPLE__
#define WRAP(name) mqtt_fault_##name
#define INTERPOSE(name) \
  __attribute__((used)) static struct { const void *replacement; const void *original; } \
  interpose_##name __attribute__((section("__DATA,__interpose"))) = \
  { (const void *)WRAP(name), (const void *)name };
#else
#define WRAP(name) name
#define INTERPOSE(name)
#endif

int WRAP(fsync)(int fd) {
  if (selected(fd, "fsync", "fsync")) return -1;
#ifdef __APPLE__
  return fsync(fd); /* dyld does not interpose calls from this image itself. */
#else
  int (*real_call)(int) = dlsym(RTLD_NEXT, "fsync");
  return real_call(fd);
#endif
}
INTERPOSE(fsync)

ssize_t WRAP(pwrite)(int fd, const void *bytes, size_t size, off_t offset) {
  if (selected(fd, "pwrite", "pwrite")) return -1;
#ifdef __APPLE__
  return pwrite(fd, bytes, size, offset);
#else
  ssize_t (*real_call)(int, const void *, size_t, off_t) = dlsym(RTLD_NEXT, "pwrite");
  return real_call(fd, bytes, size, offset);
#endif
}
INTERPOSE(pwrite)

#ifndef __APPLE__
/* Python's Linux SQLite build may sync a rollback journal with fdatasync. */
int fdatasync(int fd) {
  if (selected(fd, "fsync", "fdatasync")) return -1;
  int (*real_call)(int) = dlsym(RTLD_NEXT, "fdatasync");
  return real_call(fd);
}

ssize_t pwrite64(int fd, const void *bytes, size_t size, off64_t offset) {
  if (selected(fd, "pwrite", "pwrite64")) return -1;
  ssize_t (*real_call)(int, const void *, size_t, off64_t) = dlsym(RTLD_NEXT, "pwrite64");
  return real_call(fd, bytes, size, offset);
}
#endif
