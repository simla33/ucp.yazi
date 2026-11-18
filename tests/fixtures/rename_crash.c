/* Test-only abrupt process interruption at the staged-install libc boundary.
 * Record the actual executable and rename paths, then kill that process before
 * rename executes. This does not simulate power loss or storage durability.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/syscall.h>

static void crash_install(const char *oldpath, const char *newpath) {
    const char *target = getenv("UCP_CRASH_RENAME_TO");
    const char *marker = getenv("UCP_CRASH_RENAME_MARKER");
    const char *leaf = oldpath ? strrchr(oldpath, '/') : NULL;
    if (!target || !marker || !newpath || strcmp(target, newpath)
        || !leaf || strcmp(leaf, "/payload")) return;

    char executable[PATH_MAX];
    ssize_t length = readlink("/proc/self/exe", executable, sizeof(executable) - 1);
    if (length < 0 || length == sizeof(executable) - 1) _exit(125);
    executable[length] = '\0';
    int fd = open(marker, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (fd < 0) _exit(125);
    int written = dprintf(fd, "%s%c%s%c%s", executable, 0, oldpath, 0, newpath);
    if (close(fd) || written < 0) _exit(125);
    kill(getpid(), SIGKILL);
    _exit(125);
}

int rename(const char *oldpath, const char *newpath) {
    crash_install(oldpath, newpath);
    int (*next)(const char *, const char *) = dlsym(RTLD_NEXT, "rename");
    return next(oldpath, newpath);
}

int renameat(int oldfd, const char *oldpath, int newfd, const char *newpath) {
    crash_install(oldpath, newpath);
    int (*next)(int, const char *, int, const char *) = dlsym(RTLD_NEXT, "renameat");
    return next(oldfd, oldpath, newfd, newpath);
}

int renameat2(int oldfd, const char *oldpath, int newfd, const char *newpath, unsigned flags) {
    crash_install(oldpath, newpath);
    return syscall(SYS_renameat2, oldfd, oldpath, newfd, newpath, flags);
}
