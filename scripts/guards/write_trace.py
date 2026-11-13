"""Runs a command under strace and reports every write syscall that reaches a
target file, as byte ranges, whatever the bytes are.

Content comparisons cannot see a rewrite with identical bytes (fwup's 128 KiB
read-modify-write of its cache segments), so this looks at the syscalls.
Fail-closed: a write whose offset cannot be worked out, a truncate, an
O_TRUNC/O_APPEND open or a shared writable mmap of the target is reported as
covering the whole file.

Usage: write_trace.py [--record FILE] <target> <first-byte> <last-byte> -- <command...>
Without --record: exit 0 if the command succeeded and no write overlapped
[first, last], printing one line per write seen. With --record: the
command's output passes through, its exit status is returned, and FILE gets
"write <start> <end> <syscall>" per write and "overlap ..." per offender.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

WHOLE = (0, 1 << 62)

LINE = re.compile(r"^(\d+)\s+(.*)$")
CALL = re.compile(r"^([a-z0-9_]+)\((.*)$")
RESUMED = re.compile(r"^<\.\.\. ([a-z0-9_]+) resumed>(.*)$")
RET = re.compile(r"\)\s+=\s+(-?\d+|0x[0-9a-f]+)")
FD = re.compile(r"^(\d+)<(.*?)>")


def have_strace():
    return shutil.which("strace") is not None


class Desc:
    def __init__(self, path, pos=0):
        self.path = path
        self.pos = pos


class Tracker:
    def __init__(self, target):
        self.target = target
        self.tables = {}   # pid -> {fd: Desc}; threads share one table
        self.pending = {}  # pid -> (call, args)
        self.writes = []   # (start, end_exclusive, what)

    def table(self, pid):
        return self.tables.setdefault(pid, {})

    def hit(self, start, end, what):
        self.writes.append((start, end, what))

    def feed(self, raw):
        m = LINE.match(raw)
        if not m:
            return
        pid, rest = int(m.group(1)), m.group(2)
        r = RESUMED.match(rest)
        if r:
            if pid not in self.pending:
                return
            call, args = self.pending.pop(pid)
            self.handle(pid, call, args + r.group(2))
            return
        c = CALL.match(rest)
        if not c:
            return
        call, args = c.group(1), c.group(2)
        if args.endswith("<unfinished ...>"):
            body = args[: -len("<unfinished ...>")]
            # A write's offset is fixed when it is issued; account for it now
            # so a later lseek on the same description cannot reorder it.
            if call in ("write", "writev"):
                self.handle(pid, call, body + ") = ?")
            else:
                self.pending[pid] = (call, body)
            return
        self.handle(pid, call, args)

    def ret(self, args):
        m = RET.search(args)
        if not m:
            return None
        return int(m.group(1), 0)

    def fdarg(self, args):
        m = FD.match(args)
        if not m:
            return None, None
        return int(m.group(1)), m.group(2)

    def handle(self, pid, call, args):
        tab = self.table(pid)
        rv = self.ret(args)

        if call in ("clone", "clone3", "fork", "vfork"):
            if rv is not None and rv > 0:
                if "CLONE_FILES" in args:
                    self.tables[rv] = tab
                else:
                    self.tables[rv] = dict(tab)
            return

        if call in ("open", "openat", "creat", "openat2"):
            if rv is None or rv < 0:
                return
            m = re.search(r"\)\s+=\s+\d+<(.*?)>", args)
            path = m.group(1) if m else None
            tab[rv] = Desc(path)
            if path == self.target and ("O_TRUNC" in args or "O_APPEND" in args or call == "creat"):
                self.hit(*WHOLE, "%s with O_TRUNC/O_APPEND" % call)
            return

        if call in ("dup", "dup2", "dup3") or (call == "fcntl" and "F_DUPFD" in args):
            fd, _ = self.fdarg(args)
            if fd is not None and rv is not None and rv >= 0:
                tab[rv] = tab.get(fd)
            return

        if call == "close":
            fd, _ = self.fdarg(args)
            tab.pop(fd, None)
            return

        fd, path = self.fdarg(args)
        if fd is None:
            if call in ("truncate",) and self.target in args:
                self.hit(*WHOLE, "truncate")
            return
        desc = tab.get(fd)
        if desc is None or desc.path != path:
            desc = Desc(path, None)
            tab[fd] = desc

        if call == "lseek":
            if rv is not None and rv >= 0:
                desc.pos = rv
            return

        if call == "mmap" and path == self.target:
            if "PROT_WRITE" in args and "MAP_SHARED" in args:
                self.hit(*WHOLE, "shared writable mmap")
            return

        if path != self.target:
            return

        rest = args[FD.match(args).end():]
        if call in ("write", "pwrite64"):
            m = re.match(r',\s*""(?:\.\.\.)?,\s*(\d+)(?:,\s*(\d+))?', rest)
            if not m or (call == "pwrite64") != (m.group(2) is not None):
                self.hit(*WHOLE, "%s not parsed" % call)
                desc.pos = None
                return
            n = int(m.group(1))
            if call == "pwrite64":
                off = int(m.group(2))
                self.hit(off, off + n, call)
            elif desc.pos is None:
                self.hit(*WHOLE, "write at an unknown offset")
            else:
                self.hit(desc.pos, desc.pos + n, call)
                desc.pos += n
            return

        if call in ("writev", "pwritev", "pwritev2"):
            n = sum(int(x) for x in re.findall(r"iov_len=(\d+)", rest))
            tail = re.search(r"\],\s*\d+(?:,\s*(\d+))?", rest)
            if not tail or (call != "writev") != (tail.group(1) is not None):
                self.hit(*WHOLE, "%s not parsed" % call)
                desc.pos = None
            elif call != "writev":
                off = int(tail.group(1))
                self.hit(off, off + n, call)
            elif desc.pos is None:
                self.hit(*WHOLE, "writev at an unknown offset")
            else:
                self.hit(desc.pos, desc.pos + n, call)
                desc.pos += n
            return

        if call == "ioctl":
            if re.search(r"BLK(SEC)?DISCARD|BLKZEROOUT|MMC_IOC", rest):
                self.hit(*WHOLE, "ioctl " + rest.split(",")[1].strip())
            return

        if call in ("ftruncate", "fallocate", "copy_file_range", "sendfile",
                    "sendfile64", "splice"):
            # Rare enough on a disk being installed that parsing direction
            # and offsets is not worth it.
            self.hit(*WHOLE, call)


def run(target, cmd, env=None, capture=True):
    """Returns (CompletedProcess, [(start, end_exclusive, syscall), ...])."""
    target = os.path.realpath(target)
    with tempfile.TemporaryDirectory(prefix="write-trace-") as d:
        log = os.path.join(d, "trace")
        argv = ["strace", "-f", "-qq", "-y", "-s", "0", "--seccomp-bpf", "-o", log,
                "-e", "trace=open,openat,openat2,creat,close,dup,dup2,dup3,fcntl,lseek,"
                "write,pwrite64,writev,pwritev,pwritev2,ftruncate,truncate,fallocate,"
                "copy_file_range,sendfile,splice,ioctl,mmap,clone,clone3,fork,vfork"]
        r = subprocess.run(argv + ["--"] + cmd, env=env, capture_output=capture, text=True)
        t = Tracker(target)
        with open(log, errors="replace") as f:
            for line in f:
                t.feed(line.rstrip("\n"))
        return r, t.writes


def overlapping(writes, first, last):
    return [w for w in writes if w[0] <= last and w[1] - 1 >= first]


def main(argv):
    record = None
    if argv[:1] == ["--record"] and len(argv) > 1:
        record, argv = argv[1], argv[2:]
    if len(argv) < 5 or argv[3] != "--":
        print("usage: write_trace.py [--record FILE] <target> <first-byte> <last-byte> -- <command...>",
              file=sys.stderr)
        return 2
    if not have_strace():
        print("write_trace: strace not found", file=sys.stderr)
        return 2
    target, first, last, cmd = argv[0], int(argv[1], 0), int(argv[2], 0), argv[4:]
    r, writes = run(target, cmd, capture=record is None)
    bad = overlapping(writes, first, last)
    if record is not None:
        with open(record, "w") as f:
            for s, e, what in writes:
                f.write("write %d %d %s\n" % (s, e - 1, what))
            for s, e, what in bad:
                f.write("overlap %d %d %s\n" % (s, e - 1, what))
        return r.returncode
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    for s, e, what in writes:
        print("write %d..%d %s" % (s, e - 1, what))
    for s, e, what in bad:
        print("write_trace: FAIL %s to %s bytes %d..%d overlaps %d..%d"
              % (what, target, s, e - 1, first, last), file=sys.stderr)
    if r.returncode:
        return r.returncode
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
