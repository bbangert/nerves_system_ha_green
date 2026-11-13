# Changelog

## 0.1.0-dev

Unreleased. Initial system for the Home Assistant Green (RK3566); not yet run
on a board.

- Vendor kernel 6.1 (`rk-6.1-rkr7.2`) with a device tree for the Green and
  drivers for the NPU, Mali GPU, MPP video codecs, RGA and the PCF8563 RTC.
  Rockchip vendor storage, the SFC controller and SPI-NOR support are
  disabled so no driver can write the stock loader.
- U-Boot v2026.04 with Home Assistant OS's Green patches, built as a FIT
  (`u-boot.itb`) only. The board's stock first-stage loader loads it from
  LBA 16384 of the SD card, else the eMMC. A compiled-in boot script picks
  the boot disk, handles the A/B state block, and shows its progress on the
  user LED and in breadcrumb files on the SD card's boot FAT.
- Disk layout and fwup tasks that never write eMMC LBA 64-16383 or the SPI
  NOR; `green-install-emmc` copies an SD-booted system onto the eMMC and
  verifies afterwards that the loader area is unchanged.
- Build guards (`scripts/`) for the loader area, the FIT against the stock
  SPL's contract, the kernel config, env/layout consistency, GUID lookups
  and loader binaries, each with a failing test in `scripts/test-guards.sh`.
