# Changelog

## 0.1.0-dev

Unreleased. Initial system for the Home Assistant Green (RK3566); not yet
booted to userspace on a board.

- Vendor kernel 6.1 (`rk-6.1-rkr7.2`) with a device tree for the Green and
  drivers for the NPU, Mali GPU, MPP video codecs, RGA and the PCF8563 RTC.
  Rockchip vendor storage, the SFC controller and SPI-NOR support are
  disabled, as are `/dev/mem` and the MMC test driver, so no driver can write
  the stock loader.
- The NPU's power domain is powered only after the RK809 has raised
  `vdd_npu` to 900 mV; the first SD boot panicked when the NPU's IOMMU
  probed with the rail still off. A power domain that fails its first
  power-up now fails the attaching device's probe instead of panicking the
  kernel.
- The SD card runs at 3.3 V high speed instead of UHS-I SDR104, with its
  supply and I/O rail always on. A `reboot` from SDR104 brought the unit up
  in stock Home Assistant OS: the card's shutdown had switched both rails
  off, the restart does not reset the PMIC, and the stock SPL skipped the
  card. A panic or watchdog reset would have left the card and its I/O
  rail at 1.8 V, a state that SPL is not built to boot from.
- Soft lockups and RCU stalls panic, and so reboot, leaving a ramoops
  record; hung tasks are only logged.
- U-Boot v2026.04 with Home Assistant OS's Green patches, built as a FIT
  (`u-boot.itb`) only. The board's stock first-stage loader loads it from
  LBA 16384 of the SD card, else the eMMC. A compiled-in boot script picks
  the boot disk, handles the A/B state block, and shows its progress on the
  user LED and in breadcrumb files on the SD card's boot FAT.
- Disk layout and fwup tasks that never write eMMC LBA 64-16383 or the SPI
  NOR. `green-install-emmc` refuses unless the eMMC looks like a stock Green,
  writes the eMMC's partition table itself (fwup would rewrite LBA 0-255 with
  it), runs `fwup -t install`, and verifies that the loader area is unchanged
  and the FIT matches the SD card's. `expand-data` is SD-only; the eMMC's
  table is final at install.
- Build guards (`scripts/`) for the loader area, the FIT against the stock
  SPL's contract, the kernel config, env/layout consistency, GUID lookups
  and loader binaries, each with a failing test in `scripts/test-guards.sh`.
