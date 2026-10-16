/* Test-only libc boundary injection. Fail one staged install, allow rollback.
 * The marker proves the actual rename boundary was reached; no Lua is mocked.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/syscall.h>

static int fail_once(const char *newpath) {
    const char *target = getenv("UCP_FAIL_RENAME_TO");
    const char *marker = getenv("UCP_FAIL_RENAME_MARKER");
    if (!target || !marker || !newpath || strcmp(target, newpath)) return 0;
    int fd = open(marker, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (fd < 0) return 0;
    close(fd);
    errno = EIO;
    return 1;
}

int rename(const char *oldpath, const char *newpath) {
    if (fail_once(newpath)) return -1;
    int (*next)(const char *, const char *) = dlsym(RTLD_NEXT, "rename");
    return next(oldpath, newpath);
}

int renameat(int oldfd, const char *oldpath, int newfd, const char *newpath) {
    if (fail_once(newpath)) return -1;
    int (*next)(int, const char *, int, const char *) = dlsym(RTLD_NEXT, "renameat");
    return next(oldfd, oldpath, newfd, newpath);
}

int renameat2(int oldfd, const char *oldpath, int newfd, const char *newpath, unsigned flags) {
    if (fail_once(newpath)) return -1;
    return syscall(SYS_renameat2, oldfd, oldpath, newfd, newpath, flags);
}
