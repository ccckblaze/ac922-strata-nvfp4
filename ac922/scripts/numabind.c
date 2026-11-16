#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/mman.h>
#include <unistd.h>

/* T10: stage-island NUMA placement for the NVFP4 lane (AC922 4xV100, GPU0-2=node0 island, GPU3=node8).
 * mmap hook (zero rebuild, policy lands before any first touch):
 *   - expert arena: MAP_SHARED anonymous (or /dev/zero) in [30,80] GiB:
 *       [0, cut) MPOL_BIND node0 (default cut=69%; NB_CUT=0..1 overrides; 75% = exact stage 0-2 traffic)
 *       [cut, end) MPOL_BIND node8
 *   - ple-fp8.gguf mappings: MPOL_PREFERRED node0 (soft: file page cache never evicted under us)
 * BIND (not PREFERRED): MPOL_PREFERRED is a hint the allocator drops on the first fast-path miss (measured).
 * Gated off by NUMABIND_DISABLE=1. Log: /data/work/numabind.log */

#ifndef __NR_mbind
#define __NR_mbind 259 /* powerpc64 */
#endif
#define POL_PREFERRED 1
#define POL_BIND 2
#define GB (1024UL << 20)

typedef void *(*mmap_fn)(void *, size_t, int, int, int, off_t);
static mmap_fn next_mmap;
static volatile int in_hook;
static int logfd = -1;

static void blog(const char *cls, void *addr, size_t len, int pol, int node, int err)
{
    if (logfd < 0)
        logfd = open("/data/work/numabind.log", O_WRONLY | O_APPEND | O_CREAT, 0644);
    if (logfd < 0)
        return;
    char b[256];
    int n = snprintf(b, sizeof b, "%d %s addr=%p len=%lluMiB pol=%d node=%d err=%d\n",
                     (int)getpid(), cls, addr, (unsigned long long)(len >> 20), pol, node, err);
    if (n > 0)
        (void)!write(logfd, b, (size_t)n);
}

static void mpol(void *addr, size_t len, int pol, int node, const char *cls)
{
    if (len < (1UL << 20))
        return;
    unsigned long mask[1] = {1UL << (unsigned)node};
    long r = syscall(__NR_mbind, addr, len, pol, mask, 64, 0);
    blog(cls, addr, len, pol, node, r == 0 ? 0 : errno);
}

static void classify(void *addr, size_t len, int flags, int fd)
{
    if (len < 30 * GB || len > 80 * GB)
        return;
    if (fd >= 0 && !(flags & 0x20 /*MAP_ANONYMOUS*/)) {
        char link[64], path[512] = "";
        snprintf(link, sizeof link, "/proc/self/fd/%d", fd);
        ssize_t n = readlink(link, path, sizeof path - 1);
        if (n > 0)
            path[n] = '\0';
        if (strstr(path, "ple-fp8.gguf")) {
            mpol(addr, len, POL_PREFERRED, 0, "ple");
            return;
        }
        if (strcmp(path, "/dev/zero") != 0)
            return;
        /* else: glibc legacy MAP_SHARED|MAP_ANONYMOUS via /dev/zero -> the arena */
    } else if (fd >= 0) {
        return; /* fd>=0 with MAP_ANONYMOUS flag: not our targets via this path */
    }
    uint64_t cut = (uint64_t)len * 69 / 100;
    const char *cs = getenv("NB_CUT");
    if (cs != NULL) {
        const double cf = strtod(cs, NULL);
        if (cf >= 0.0 && cf <= 1.0)
            cut = (uint64_t)len * (uint64_t)(cf * 1000000.0) / 1000000ull;
    }
    cut &= ~(uint64_t)0xFFFF; /* 64 KiB page align */
    mpol(addr, (size_t)cut, POL_BIND, 0, "arena0");
    mpol((char *)addr + cut, (size_t)len - cut, POL_BIND, 8, "arena8");
}

void *mmap(void *p, size_t len, int prot, int flags, int fd, off_t off)
{
    if (!next_mmap) {
        if (in_hook)
            return MAP_FAILED; /* recursion guard during resolve */
        in_hook = 1;
        next_mmap = (mmap_fn)dlsym(RTLD_NEXT, "mmap");
        if (!next_mmap)
            next_mmap = (mmap_fn)dlsym(RTLD_NEXT, "mmap64");
        in_hook = 0;
    }
    void *r = next_mmap(p, len, prot, flags, fd, off);
    if (r != MAP_FAILED && !in_hook && getenv("NUMABIND_DISABLE") == NULL)
        classify(r, len, flags, fd);
    return r;
}

void *mmap64(void *p, size_t len, int prot, int flags, int fd, off_t off)
{
    return mmap(p, len, prot, flags, fd, off);
}
