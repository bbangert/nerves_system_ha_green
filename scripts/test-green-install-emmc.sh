#!/bin/sh
#
# Tests green-install-emmc against regular files standing in for the block
# devices, sysfs and fwup. The tool is sourced (not executed) and its path
# variables are reassigned per case, so nothing here can touch a real disk.
#
# Usage: scripts/test-green-install-emmc.sh [--tool PATH | --break-test]
#   --tool PATH    test another copy of the tool
#   --break-test   remove each check from a scratch copy in turn and require
#                  the suite to notice

DIR=$(cd "$(dirname "$0")" && pwd)
TOOL=$DIR/../rootfs_overlay/usr/sbin/green-install-emmc
MODE=run
while [ $# -gt 0 ]; do
    case $1 in
        --tool) TOOL=$2; shift 2 ;;
        --break-test) MODE=breaktest; shift ;;
        *) echo "unknown argument $1" >&2; exit 2 ;;
    esac
done

ROOT=$(mktemp -d) || exit 2
trap 'rm -rf "$ROOT"' EXIT

if [ "$MODE" = breaktest ]; then
    # name|sed expression that disables one check
    # shellcheck disable=SC2016
    CASES='
check_sd_booted|s/^check_sd_booted() {/&\n    return 0/
check_emmc_present|s/^check_emmc_present() {/&\n    return 0/
check_emmc_nonremovable|s/^check_emmc_nonremovable() {/&\n    return 0/
check_emmc_is_mmc|s/^check_emmc_is_mmc() {/&\n    return 0/
check_emmc_not_boot_disk|s/^check_emmc_not_boot_disk() {/&\n    return 0/
check_loader_present|s/^check_loader_present() {/&\n    return 0/
check_fw_platform|s/^check_fw_platform() {/&\n    return 0/
check_emmc_unused|s/^check_emmc_unused() {/&\n    return 0/
check_sd_has_fit|s/^check_sd_has_fit() {/&\n    return 0/
fw_file_exists|s/\[ -f "\$FW" \] ||/true ||/
usage_check|s/\[ \$# -eq 1 \] ||/true ||/
short_read_guard|s/grep -q "^\$3+0 records in" "\$TMPD\/dd.err" \&\& //
loader_after_compare|s/\[ "\$loader_before" = "\$loader_after" \]/true/
fit_compare|s/\[ "\$fit_sd" = "\$fit_emmc" \]/true/
fwup_exit_status|s/\[ "\$fwup_rc" -ne 0 \]/false/
'
    rc=0
    printf '%s\n' "$CASES" | while IFS='|' read -r name expr; do
        [ -n "$name" ] || continue
        copy=$ROOT/broken-$name
        sed "$expr" "$TOOL" > "$copy"
        if cmp -s "$TOOL" "$copy"; then
            echo "BREAK $name: sed did not change the tool"
            exit 3
        fi
        if out=$(sh "$0" --tool "$copy" 2>&1); then
            echo "BREAK $name: NOT DETECTED (suite still passes)"
            exit 3
        fi
        echo "BREAK $name: detected by $(echo "$out" | sed -n 's/^FAILED: //p' | tr '\n' ' ')"
    done || rc=$?
    exit $rc
fi

MB=1048576
FAILS=
NCASE=0

{ printf '\320\015\376\355'; dd if=/dev/urandom bs=1024 count=64 2>/dev/null; } > "$ROOT/fit.bin"
dd if=/dev/zero of="$ROOT/window.bin" bs=$MB count=8 2>/dev/null
dd if="$ROOT/fit.bin" of="$ROOT/window.bin" conv=notrunc 2>/dev/null
cp "$ROOT/window.bin" "$ROOT/window.bad"
printf 'X' | dd of="$ROOT/window.bad" bs=1 seek=4194304 conv=notrunc 2>/dev/null

dd if=/dev/urandom of="$ROOT/sd.master" bs=$MB count=20 2>/dev/null
dd if="$ROOT/window.bin" of="$ROOT/sd.master" bs=$MB seek=8 conv=notrunc 2>/dev/null
dd if=/dev/urandom of="$ROOT/emmc.master" bs=$MB count=20 2>/dev/null
printf 'RKNS' | dd of="$ROOT/emmc.master" bs=1 seek=32768 conv=notrunc 2>/dev/null
# The byte the loader_changed stub overwrites must differ from what it writes.
printf '\000' | dd of="$ROOT/emmc.master" bs=1 seek=51200 conv=notrunc 2>/dev/null

# Stands in for fwup; behaviour comes from files in its directory.
cat > "$ROOT/fwup.stub" <<'EOF'
#!/bin/sh
D=$(dirname "$0")
echo "$*" >> "$D/fwup.calls"
case $1 in
    -m) printf 'meta-platform=%s\n' "$(cat "$D/meta")"; exit 0 ;;
    -a) ;;
    *) exit 98 ;;
esac
[ "$*" = "-a -d $D/emmc.img -t install -i $D/test.fw" ] || exit 99
dev=$3
dd if=/dev/zero of="$dev" bs=512 count=34 conv=notrunc 2>/dev/null
case $(cat "$D/mode") in
    ok) dd if="$D/window.bin" of="$dev" bs=512 seek=16384 conv=notrunc 2>/dev/null ;;
    fit_wrong) dd if="$D/window.bad" of="$dev" bs=512 seek=16384 conv=notrunc 2>/dev/null ;;
    no_fit) ;;
    loader_changed)
        dd if="$D/window.bin" of="$dev" bs=512 seek=16384 conv=notrunc 2>/dev/null
        printf 'X' | dd of="$dev" bs=1 seek=51200 conv=notrunc 2>/dev/null ;;
    fwup_fail)
        dd if="$D/window.bin" of="$dev" bs=512 seek=16384 conv=notrunc 2>/dev/null
        exit 1 ;;
esac
exit 0
EOF

# Builds a case directory in which every precondition holds.
new_case() {
    NCASE=$((NCASE + 1))
    C=$ROOT/case$NCASE
    mkdir -p "$C/sys/device"
    cp "$ROOT/sd.master" "$C/sd.img"
    cp "$ROOT/emmc.master" "$C/emmc.img"
    cp "$ROOT/emmc.master" "$C/emmc.orig"
    cp "$ROOT/window.bin" "$ROOT/window.bad" "$C/"
    cp "$ROOT/fwup.stub" "$C/fwup"
    chmod +x "$C/fwup"
    ln -s "$C/sd.img" "$C/rootdisk0"
    SD_PATH=$C/sd.img
    echo 0 > "$C/sys/removable"
    echo MMC > "$C/sys/device/type"
    echo '"ha_green"' > "$C/meta"
    echo ok > "$C/mode"
    : > "$C/test.fw"
    : > "$C/fwup.calls"
    printf '%s/sd.imgp5 /root ext4 rw 0 0\ntmpfs /tmp tmpfs rw 0 0\n' "$C" > "$C/mounts"
    printf 'Filename Type Size Used Priority\n' > "$C/swaps"
}

# shellcheck disable=SC2034,SC2329
run_tool() {
    (
        # shellcheck disable=SC1090
        . "$TOOL"
        ROOTDISK=$C/rootdisk0
        SD_DISK=$SD_PATH
        EMMC_DISK=$C/emmc.img
        EMMC_SYSFS=$C/sys
        MOUNTS=$C/mounts
        SWAPS=$C/swaps
        TMPBASE=$C
        FWUP=$C/fwup
        # Test files are not block devices.
        is_blockdev() { [ -f "$1" ]; }
        main "$@"
    ) > "$C/out" 2> "$C/err"
    RC=$?
}

fail() {
    echo "  not ok: $1"
    FAILED_NOW=1
}

begin() {
    CASE_NAME=$1
    FAILED_NOW=0
    new_case
}

finish() {
    if [ "$FAILED_NOW" -eq 1 ]; then
        FAILS="$FAILS $CASE_NAME"
        echo "FAIL $CASE_NAME"
        sed 's/^/    out: /' "$C/out"
        sed 's/^/    err: /' "$C/err"
    else
        echo "ok   $CASE_NAME"
    fi
}

assert_emmc_untouched() {
    if [ -e "$C/emmc.img" ]; then
        cmp -s "$C/emmc.img" "$C/emmc.orig" || fail "eMMC file changed"
    fi
}

assert_fwup_not_applied() {
    if grep -q '^-a' "$C/fwup.calls"; then fail "fwup was invoked with -a"; fi
}

# expect_refusal <name> <stderr text> [tool args...]; default arg is the fw
expect_refusal() {
    name=$1; want=$2; shift 2
    [ $# -gt 0 ] || set -- "$C/test.fw"
    run_tool "$@"
    [ "$RC" -eq 1 ] || fail "exit status $RC, want 1"
    [ "$(wc -l < "$C/err")" -eq 1 ] || fail "stderr is not exactly one line"
    grep -q "REFUSED: .*$want" "$C/err" || fail "stderr lacks '$want'"
    grep -q 'nothing was written' "$C/err" || fail "stderr does not say nothing was written"
    ! grep -q PASS "$C/out" || fail "printed PASS"
    assert_emmc_untouched
    assert_fwup_not_applied
}

# expect_fail <stderr text>: install ran, verification failed
expect_fail() {
    run_tool "$C/test.fw"
    [ "$RC" -eq 1 ] || fail "exit status $RC, want 1"
    [ "$(tail -n 1 "$C/out" | cut -c1-5)" = "FAIL:" ] || fail "last stdout line is not FAIL"
    grep -q "$1" "$C/err" || fail "stderr lacks '$1'"
    ! grep -q '^PASS' "$C/out" || fail "printed PASS"
    [ "$(grep -c '^-a' "$C/fwup.calls")" -eq 1 ] || fail "fwup not applied exactly once"
}

begin happy_path
run_tool "$C/test.fw"
[ "$RC" -eq 0 ] || fail "exit status $RC"
[ "$(tail -n 1 "$C/out")" = PASS ] || fail "last line is not PASS"
[ "$(grep -c '[0-9a-f]\{64\}' "$C/out")" -eq 4 ] || fail "expected four sha256 lines"
[ ! -s "$C/err" ] || fail "unexpected stderr"
[ "$(grep -c '^-a' "$C/fwup.calls")" -eq 1 ] || fail "fwup not applied exactly once"
grep -qx -- "-a -d $C/emmc.img -t install -i $C/test.fw" "$C/fwup.calls" || fail "fwup command line"
cmp -s "$C/emmc.img" "$C/emmc.orig" && fail "stub install did not run"
cmp -s -i $((64 * 512)) -n $((16320 * 512)) "$C/emmc.img" "$C/emmc.orig" || fail "loader area differs"
cmp -s -i $((16384 * 512)) -n $((16384 * 512)) "$C/emmc.img" "$C/sd.img" || fail "FIT window differs from SD"
finish

begin happy_path_unquoted_meta
echo ha_green > "$C/meta"
run_tool "$C/test.fw"
[ "$RC" -eq 0 ] && [ "$(tail -n 1 "$C/out")" = PASS ] || fail "no PASS with unquoted meta"
finish

begin no_arguments
run_tool
[ "$RC" -eq 2 ] || fail "exit status $RC, want 2"
grep -q usage "$C/err" || fail "no usage line"
assert_emmc_untouched; assert_fwup_not_applied
finish

begin fw_file_missing
rm -f "$C/test.fw"
expect_refusal fw_file_missing "not found"
finish

begin not_sd_booted
rm "$C/rootdisk0"; ln -s "$C/emmc.img" "$C/rootdisk0"
expect_refusal not_sd_booted "not running from the SD card"
finish

begin rootdisk_missing
rm "$C/rootdisk0"
expect_refusal rootdisk_missing "not running from the SD card"
finish

begin rootdisk_is_other_file
rm "$C/rootdisk0"; cp "$C/sd.img" "$C/rootdisk0"
expect_refusal rootdisk_is_other_file "not running from the SD card"
finish

begin emmc_missing
rm "$C/emmc.img" "$C/emmc.orig"
expect_refusal emmc_missing "not a block device"
finish

begin emmc_removable
echo 1 > "$C/sys/removable"
expect_refusal emmc_removable "non-removable"
finish

begin emmc_removable_unreadable
rm "$C/sys/removable"
expect_refusal emmc_removable_unreadable "non-removable"
finish

begin emmc_not_mmc
echo SD > "$C/sys/device/type"
expect_refusal emmc_not_mmc "not an MMC"
finish

begin emmc_is_boot_disk
# Only reachable when SD_DISK and EMMC_DISK alias the same device.
rm "$C/rootdisk0"; ln -s "$C/emmc.img" "$C/rootdisk0"
SD_PATH=$C/emmc.img
run_tool "$C/test.fw"
[ "$RC" -eq 1 ] || fail "exit status $RC, want 1"
grep -q 'REFUSED: .*is the boot disk' "$C/err" || fail "stderr lacks 'is the boot disk'"
assert_emmc_untouched; assert_fwup_not_applied
finish

begin loader_absent
dd if=/dev/zero of="$C/emmc.img" bs=1 seek=32768 count=4 conv=notrunc 2>/dev/null
cp "$C/emmc.img" "$C/emmc.orig"
expect_refusal loader_absent "no RKNS stock loader"
finish

begin loader_wrong_magic
printf 'RKNX' | dd of="$C/emmc.img" bs=1 seek=32768 conv=notrunc 2>/dev/null
cp "$C/emmc.img" "$C/emmc.orig"
expect_refusal loader_wrong_magic "no RKNS stock loader"
finish

begin wrong_platform
echo '"rock_4d"' > "$C/meta"
expect_refusal wrong_platform "not a ha_green firmware"
finish

begin platform_missing_in_meta
: > "$C/meta"
expect_refusal platform_missing_in_meta "not a ha_green firmware"
finish

begin emmc_partition_mounted
echo "$C/emmc.imgp1 /mnt vfat rw 0 0" >> "$C/mounts"
expect_refusal emmc_partition_mounted "mounted or used as swap"
finish

begin emmc_used_as_swap
echo "$C/emmc.imgp5 partition 1024 0 -2" >> "$C/swaps"
expect_refusal emmc_used_as_swap "mounted or used as swap"
finish

begin sd_has_no_fit
dd if=/dev/zero of="$C/sd.img" bs=512 seek=16384 count=8 conv=notrunc 2>/dev/null
expect_refusal sd_has_no_fit "no FIT"
finish

begin emmc_loader_unreadable
# Long enough for the RKNS check, too short for the whole loader area.
dd if="$C/emmc.orig" of="$C/emmc.img" bs=1 count=33000 2>/dev/null
cp "$C/emmc.img" "$C/emmc.orig"
expect_refusal emmc_loader_unreadable "cannot read the loader area"
finish

begin loader_area_changed_by_fwup
echo loader_changed > "$C/mode"
expect_fail "loader area changed"
finish

begin fit_not_written
echo no_fit > "$C/mode"
expect_fail "FIT differs"
finish

begin fit_differs
echo fit_wrong > "$C/mode"
expect_fail "FIT differs"
finish

begin fwup_exits_nonzero
echo fwup_fail > "$C/mode"
expect_fail "fwup exited"
finish

if [ -n "$FAILS" ]; then
    echo "FAILED: $FAILS" | sed 's/  */ /g'
    exit 1
fi
echo "all $NCASE cases passed"
