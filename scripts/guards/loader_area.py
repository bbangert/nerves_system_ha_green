"""Static and dynamic proof that a .fw never writes the stock loader area.

Everything is derived from the resolved meta.conf inside the archive, so the
result covers what fwup will really do, not what the source files suggest.
"""

import csv
import os
import random
import re
import subprocess
import sys
import tempfile
import zipfile

SECTOR = 512
GPT_LAST = 33
LOADER_FIRST, LOADER_LAST = 64, 16383
WINDOW_FIRST, WINDOW_LAST = 16384, 32767
# The FIT window may only be written by tasks that lay down a whole disk.
WINDOW_TASKS = ("complete", "install")
MIN_PARTITION_START = WINDOW_LAST + 1

NO_WRITE = {"info", "error"}
RAW = {"raw_write", "raw_memset", "trim"}
FAT = {
    "fat_attrib", "fat_cp", "fat_mkdir", "fat_mkfs", "fat_mv", "fat_mv!",
    "fat_rm", "fat_rm!", "fat_setlabel", "fat_touch", "fat_write",
}
ENV = {"uboot_clearenv", "uboot_recover", "uboot_setenv", "uboot_unsetenv"}
BANNED = {"path_write", "pipe_write", "execute"}


class Violations(list):
    def add(self, where, msg):
        self.append("%s: %s" % (where, msg))


def split_funlist(body):
    """funlist={N,name,arg...,N,name,...}: N counts the name plus its args."""
    toks = next(csv.reader([body], escapechar="\\"))
    calls, i = [], 0
    while i < len(toks):
        n = int(toks[i])
        calls.append((toks[i + 1], toks[i + 2:i + 1 + n]))
        i += 1 + n
    return calls


def parse_meta(text):
    res, gpts, envs, tasks = {}, {}, {}, {}
    stack = []
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r'^(file-resource|gpt|task|uboot-environment|partition|on-resource)\s+"([^"]*)"\s*\{$', line)
        if m:
            stack.append((m.group(1), m.group(2), {}))
            continue
        m = re.match(r"^(on-init|on-finish|on-error)\s*\{$", line)
        if m:
            stack.append((m.group(1), "", {}))
            continue
        if line == "}":
            kind, name, props = stack.pop()
            if kind == "file-resource":
                res[name] = props
            elif kind == "uboot-environment":
                envs[name] = props
            elif kind == "partition":
                stack[-1][2].setdefault("partitions", []).append(props)
            elif kind == "gpt":
                gpts[name] = props
            elif kind == "task":
                tasks[name] = props
            elif kind in ("on-init", "on-finish", "on-error", "on-resource"):
                stack[-1][2].setdefault("hooks", []).append((kind, name, props))
            continue
        if stack and "=" in line:
            k, v = line.split("=", 1)
            stack[-1][2][k] = v
    return res, gpts, envs, tasks


def num(s):
    return int(s, 0)


def check_static(fw, zf, v):
    meta = zf.read("meta.conf").decode()
    res, gpts, envs, tasks = parse_meta(meta)
    where = os.path.basename(fw)

    for name, g in gpts.items():
        for p in g.get("partitions", []):
            first = num(p["block-offset"])
            if first < MIN_PARTITION_START:
                v.add(where, "gpt %s partition '%s' starts at LBA %d, below LBA %d (loader area, FIT window)"
                      % (name, p.get("name", "?"), first, MIN_PARTITION_START))

    def span(first, last, task, hook, call, resource=None):
        tag = "%s task %s %s %s" % (where, task, hook, call)
        if first <= LOADER_LAST and last >= LOADER_FIRST:
            v.add(tag, "writes LBA %d-%d, inside the stock loader area %d-%d"
                  % (first, last, LOADER_FIRST, LOADER_LAST))
        elif first <= LOADER_LAST and last >= GPT_LAST + 1:
            v.add(tag, "writes LBA %d-%d, between the GPT and the loader area" % (first, last))
        elif first <= GPT_LAST:
            v.add(tag, "writes LBA %d-%d, the protective MBR/GPT, outside gpt_write/mbr_write" % (first, last))
        elif first <= WINDOW_LAST and last >= WINDOW_FIRST:
            if not task.startswith(WINDOW_TASKS):
                v.add(tag, "writes LBA %d-%d, the FIT window, outside complete/install" % (first, last))
            elif resource not in (None, "u-boot.itb"):
                v.add(tag, "writes LBA %d-%d, the FIT window, from resource %s" % (first, last, resource))

    for tname, t in tasks.items():
        for kind, rname, hp in t.get("hooks", []):
            if "funlist" not in hp:
                continue
            body = hp["funlist"].strip()[1:-1]
            for action, args in split_funlist(body):
                call = "%s(%s)" % (action, ",".join(args))
                resource = rname if kind == "on-resource" else None
                if action in NO_WRITE:
                    continue
                if action in BANNED:
                    v.add("%s task %s %s %s" % (where, tname, kind, call),
                          "action can write anywhere and cannot be bounded")
                elif action == "mbr_write":
                    continue
                elif action == "gpt_write":
                    if args[0] not in gpts:
                        v.add("%s task %s %s" % (where, tname, call), "unknown gpt block")
                elif action in RAW:
                    off = num(args[0])
                    if action == "raw_write":
                        if resource is None or resource not in res:
                            v.add("%s task %s %s" % (where, tname, call), "raw_write outside a known on-resource")
                            continue
                        length = num(res[resource]["length"])
                        blocks = (length + SECTOR - 1) // SECTOR
                    else:
                        blocks = num(args[1])
                    if blocks:
                        span(off, off + blocks - 1, tname, kind, call, resource if action == "raw_write" or resource == "u-boot.itb" else None)
                elif action in FAT:
                    off = num(args[0])
                    if off < MIN_PARTITION_START:
                        v.add("%s task %s %s %s" % (where, tname, kind, call),
                              "FAT offset LBA %d is below LBA %d" % (off, MIN_PARTITION_START))
                elif action in ENV:
                    e = envs.get(args[0])
                    if e is None:
                        v.add("%s task %s %s" % (where, tname, call), "unknown uboot-environment block")
                        continue
                    off, cnt = num(e["block-offset"]), num(e["block-count"])
                    span(off, off + cnt - 1, tname, kind, call)
                else:
                    v.add("%s task %s %s %s" % (where, tname, kind, call),
                          "unknown action; add it to the guard after working out what it writes")

    for name in res:
        try:
            head = zf.read("data/" + name)[:4]
        except KeyError:
            continue
        if head == b"RKNS":
            v.add(where, "resource %s is a Rockchip idblock (RKNS); no loader may ship in a .fw" % name)
    return tasks, meta


def check_dynamic(fw, zf, tasks, v, fwup):
    where = os.path.basename(fw)
    if "complete" not in tasks:
        print("check-loader-area: %s has no complete task, static check only" % where)
        return
    first_byte = (GPT_LAST + 1) * SECTOR
    window_byte = WINDOW_FIRST * SECTOR
    before = len(v)
    with tempfile.TemporaryDirectory(prefix="loader-area-") as d:
        img = os.path.join(d, "disk.img")
        pattern = random.Random(7).randbytes(window_byte + (1 << 20))
        with open(img, "wb") as f:
            f.write(pattern)
            f.truncate(3 << 30)
        r = subprocess.run([fwup, "-a", "-q", "-d", img, "-t", "complete", "-i", fw],
                           capture_output=True, text=True)
        if r.returncode:
            v.add(where, "fwup could not apply task complete to a scratch image: %s" % r.stderr.strip())
            return
        with open(img, "rb") as f:
            f.seek(first_byte)
            after = f.read(window_byte - first_byte)
            fit = f.read(4096)
        if after != pattern[first_byte:window_byte]:
            idx = next(i for i in range(len(after)) if after[i] != pattern[first_byte + i])
            lba = (first_byte + idx) // SECTOR
            v.add(where, "task complete changed byte %d (LBA %d) below 8 MiB" % (first_byte + idx, lba))
        if fit[:4] != b"\xd0\x0d\xfe\xed":
            v.add(where, "no FIT magic at 8 MiB after task complete")
        elif "data/u-boot.itb" in zf.namelist():
            itb = zf.read("data/u-boot.itb")
            with open(img, "rb") as f:
                f.seek(window_byte)
                if f.read(len(itb)) != itb:
                    v.add(where, "bytes at 8 MiB are not the u-boot.itb of this .fw")
    if len(v) == before:
        print("check-loader-area: %s dynamic: bytes 17 KiB..8 MiB untouched, FIT at 8 MiB" % where)


def main(argv):
    static_only = False
    if argv and argv[0] == "--static-only":
        static_only, argv = True, argv[1:]
    if not argv:
        print("usage: check-loader-area.sh [--static-only] <fw>...", file=sys.stderr)
        return 2
    fwup = os.environ.get("FWUP", "fwup")
    v = Violations()
    for fw in argv:
        try:
            zf = zipfile.ZipFile(fw)
            tasks, _ = check_static(fw, zf, v)
        except (OSError, KeyError, zipfile.BadZipFile) as e:
            v.add(fw, "cannot read as a .fw: %s" % e)
            continue
        if not static_only:
            check_dynamic(fw, zf, tasks, v, fwup)
    if v:
        for line in v:
            print("check-loader-area: FAIL %s" % line, file=sys.stderr)
        return 1
    print("check-loader-area: OK (%d file%s)" % (len(argv), "" if len(argv) == 1 else "s"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
