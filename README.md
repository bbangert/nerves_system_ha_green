# Nerves System for the Home Assistant Green (Rockchip RK3566)

A [Nerves](https://nerves-project.org) system for the Home Assistant Green:
Rockchip RK3566 (4x Cortex-A55, Mali-G52, 0.8 TOPS NPU), 4 GiB RAM, eMMC,
Gigabit Ethernet, USB, HDMI and a microSD slot. It is a base system for
Vagus. The kernel is Rockchip's vendor 6.1 kernel (`armbian/linux-rockchip`
`rk-6.1-rkr7.2`); U-Boot is v2026.04 with Home Assistant OS's Green patches.

**Status: nothing here has been run on a board yet.** This describes what the
build is designed to do and what its guards enforce; statements about the
hardware are marked as such.

The design rule behind everything else: the Green's own first-stage loader
stays exactly as it is, and nothing this system builds, installs or upgrades
ever writes it. There is no maskrom tooling and no serial console in this
project's workflow, so a damaged loader could not be repaired.

## Boot chain

1. The RK3566 BootROM starts the board's stock first stage, which this
   system never writes. Read out of one unit running stock Home Assistant OS
   18.4 (read out, not boot-tested with this system): the SPI NOR holds a
   loader with `U-Boot SPL 2023.07.02`, the eMMC holds `U-Boot SPL 2026.04`
   (LBA 64-16383), and the device trees of both SPLs carry the boot order SD
   card, eMMC, SPI NOR (`u-boot,spl-boot-order`). Which of the two SPLs the
   BootROM runs is inferred, not proven: the running system had no
   `u-boot,spl-boot-device` in `/chosen`, which a 2026.04 SPL would set, so
   the NOR's SPL probably ran.
2. Either SPL is designed to load `u-boot.itb` (our U-Boot and BL31, a FIT)
   from raw LBA 16384 of the SD card if the card carries FDT magic there,
   otherwise of the eMMC, so a prepared SD card is designed to take over the
   boot, and pulling it is designed to return the unit to what the eMMC
   holds. Nothing here has been booted on hardware yet.
   Our U-Boot boots from the disk a 2026.04 SPL says it loaded U-Boot from
   (`u-boot,spl-boot-device`); the older SPL gives no such hint, so there it
   repeats the FDT-magic probe, and a card with FDT magic at LBA 16384 whose
   FIT that SPL rejected is still selected: remove the card to boot the eMMC.
3. Our U-Boot runs one boot script (its compiled-in environment,
   `uboot/uboot.env`), loads the state block, and boots `Image.<slot>` with
   the matching device tree from the boot FAT, with the root filesystem on
   the same disk. After reading the state block the script trusts its own
   variables only once they are shown equal to a copy taken before the
   import; otherwise, or if the import fails part-way, it boots as if there
   were no block: slot A on defaults, nothing saved.

A restart (`reboot`, a panic, the watchdog) resets the SoC but not the RK809
PMIC, and the stock SPL neither programs the PMIC nor switches to 1.8 V
signalling, so the SD card has to come out of any reset as that SPL expects
it. The card therefore runs at 3.3 V high speed (50 MHz, at most 25 MB/s on
the bus), not UHS-I SDR104 (148.5 MHz here, at most 74 MB/s), and its supply
(`vcc3v3_sd`) and I/O rail (`vccio_sd`, fixed at 3.3 V) are always on. Seen
on a board with an earlier build: after `reboot` from SDR104, with both
rails switched off by the card's shutdown, the unit booted the eMMC. The
eMMC needs none of this: its supply and its 1.8 V I/O rail are always on,
and CMD0 returns it from HS200 to its default timing.

The boot script, the config delta against Home Assistant OS's U-Boot and the
FIT contract are in [uboot/README.md](uboot/README.md).

## What is never written, and why

| Never written | Why |
| --- | --- |
| eMMC LBA 64-16383 | The stock first-stage loader. |
| The SPI NOR | May hold the factory loader that runs first. |

Enforced, not just intended:

- The kernel has Rockchip vendor storage (which writes eMMC LBA 7168-7679,
  inside the loader area), the SFC controller and the SPI-NOR driver turned
  off, and also `/dev/mem` (a path to the SFC's registers, so to the NOR) and
  the MMC test driver (raw test patterns to whatever card it binds), so no
  Linux driver path reaches either place. The FIQ debugger is off too.
- U-Boot is built without the `sf` command and with no environment storage
  driver; its only raw write is the Nerves state block.
- Only tasks that can never run against the eMMC write a partition table
  (`complete` for SD cards and image files, the SD-only `expand-data`, and
  `emmc-gpt` into a scratch file); the eMMC's table is copied by
  `green-install-emmc`. The FIT window is written only by `complete` and
  `install`, and nothing else writes below LBA 32768. No idblock, SPL or TPL
  is built into `images/`, a `.fw` or the rootfs.
- The build runs guards from `scripts/` and fails if any of this stops being
  true: `check-loader-area.sh` (resolves every offset in the built `.fw`
  files, applies `complete` to a pattern-filled image, models fwup's 128 KiB
  segments, and, when `strace` is available, fails on any write syscall below
  8 MiB from a task that can reach the eMMC),
  `check-fit.sh` (the FIT against the stock SPL's contract),
  `check-kernel-config.sh`, `check-env-config.sh`, `check-no-guid-lookup.sh`
  and `check-no-loader-binaries.sh`. `scripts/test-guards.sh` shows that each
  one fails when its invariant is broken.

## Disk layout

The same layout on the SD card and the eMMC (plain GPT, 512-byte sectors);
only the GUIDs differ.

| LBA | What |
| --- | --- |
| 0-33 | Protective MBR and GPT |
| 64-16383 | Stock loader, never written |
| 16384 | `u-boot.itb`, raw; window reserved up to LBA 32767 (8 MiB) |
| 32768 | p1 state: U-Boot environment in the first 128 KiB (1 MiB partition) |
| 34816 | p2 boot FAT, 256 MiB: `Image.a`, `Image.b`, `rk3566-ha-green.a.dtb`, `rk3566-ha-green.b.dtb`, and an empty `medium.sd` or `medium.mmc` |
| 559104 | p3 rootfs A, squashfs, 512 MiB |
| 1607680 | p4 rootfs B, squashfs, 512 MiB |
| 2656256 | p5 application data, ext4, grows to the end of the disk |

The root device is chosen by name in the boot script, never by GUID or label.
U-Boot and Linux number the MMC devices alike: `mmcblk0` is the eMMC and
`mmcblk1` the SD card.

### fwup tasks

| Task | Writes |
| --- | --- |
| `complete` | The whole layout to an SD card or an image file. `complete.refuse-emmc` refuses a destination that holds `medium.mmc`. |
| `install` | The eMMC, all but its partition table; only through `green-install-emmc`. |
| `emmc-gpt` | The eMMC's partition table, into the scratch file `green-install-emmc` copies from. |
| `upgrade` | A/B OTA: p2-p4 and the state block. |

Operations (`fwup-ops.conf`, installed as `/usr/share/fwup/ops.fw`):
`expand-data` (SD card only; the eMMC's table is final at install),
`factory-reset`, `prevent-revert`, `revert`, `status` and `validate`.

`complete` leaves an empty `medium.sd` and `install` an empty `medium.mmc` in
the boot FAT. They are how the tasks tell an SD card from an installed eMMC;
do not delete them. fwup cannot identify the device it is aimed at, so never
point `fwup -t complete` (or `emmc-gpt`) at `/dev/mmcblk0` by hand: on a
stock eMMC that would rewrite LBA 0-255, with identical bytes in the loader
part. Use only `green-install-emmc`.

## LED stage codes

With no console, the user LED shows how far a boot got:

| User LED | Meaning |
| --- | --- |
| solid | Our U-Boot reached its boot script |
| off for 10 s, then solid again | U-Boot could not load or boot the kernel; it retries |
| heartbeat | Our kernel is running |

The activity LED blinks on eMMC I/O only (kernel trigger `mmc0`); SD card
I/O does not drive it. If stock Home Assistant OS comes up
with the prepared card inserted, the stock SPL did not take our FIT.

## Breadcrumb files

When U-Boot booted from the SD card it leaves three files on the card's boot
FAT (partition 2, label `BOOT`), each rewritten only when its content changes.
Read them by putting the card in a PC:

| File | Content |
| --- | --- |
| `uboot-vars.txt` | Boot disk, state-block result, slot, A/B variables, the SPL's boot-device property if it set one, serial, MAC, U-Boot version, kernel command line |
| `uboot-fdt.dtb` | U-Boot's control device tree as the SPL handed it over; `dtc -I dtb -O dts uboot-fdt.dtb` shows every `/chosen` property |
| `ramoops.bin` | The previous boot's kernel log from the ramoops RAM region, only if a kernel left its signature there (so only if RAM survived the reset); `strings ramoops.bin` |

Nothing is written to the eMMC's FAT this way.

## Install

1. Build the product firmware (`mix firmware`) and write the SD card with the
   `complete` task, for example `mix burn` or
   `fwup -a -d /dev/sdX -t complete -i product.fw`. The card then carries the
   FIT at LBA 16384 and the whole layout.
2. Put the card in the Green and power it on. The system is designed to boot
   from the card, with the hostname `nerves-` plus the last four characters
   of the board serial (`rootfs_overlay/etc/erlinit.config`). The serial is the
   EEPROM one from the device tree (`rootfs_overlay/etc/boardid.config`). SSH and mDNS
   come from the product firmware, not from this system.
3. Installing replaces Home Assistant OS on the eMMC: the partition table,
   the system and all data. Back up first. Copy the same `.fw` to the unit
   and run `green-install-emmc <fw>`. The `.fw` must be the build the SD card
   was written from: the final check compares the eMMC's FIT with the SD
   card's, so a different build ends in `FAIL` even though the install
   completed.

   The tool refuses, writing nothing, unless all of these hold: the system
   runs from the SD card; `/dev/mmcblk0` is a non-removable MMC device with
   512-byte blocks and at least 3704866 sectors (the layout's end plus the
   backup table), is not the boot disk, and is not mounted or used as swap;
   an `RKNS` loader header sits at eMMC LBA 64; the `.fw` is for platform
   `ha_green`; and the SD card carries a FIT. A refusal reads
   `green-install-emmc: REFUSED: <reason>; nothing was written to the eMMC.`

   If there is no `RKNS` header (a blank, erased or non-stock eMMC), this
   system cannot be installed on that unit: we never write a loader. Restore
   it first with the official installer card (see Restore below).

   Then the tool:

   1. hashes eMMC LBA 64-16383 (the loader area) with direct I/O;
   2. has fwup render the eMMC's partition table into a scratch file
      (task `emmc-gpt`), checks that it lies in LBA 0-33 and in the backup
      sectors at the end of the disk, and copies only those sectors to the
      eMMC itself, backup first, then primary, and reads them back;
   3. runs `fwup -t install`, which writes no partition table;
   4. hashes the loader area again, compares the FIT window (LBA
      16384-32767) with the SD card's, and re-checks the partition table.

   fwup does not write the table because it writes in 128 KiB segments:
   writing the 34 table sectors would rewrite LBA 0-255, which includes the
   start of the stock loader, even with identical bytes.

   A good run prints, among the hashes:

   ```
   eMMC GPT: written and read back identical
   loader area: unchanged
   FIT window: identical
   eMMC GPT: unchanged by the install
   PASS
   ```

   Anything else ends with
   `FAIL: do not reboot into the eMMC; no repair was attempted`.
   After an interruption the tool can simply be run again; the SD card stays
   bootable throughout.
4. Remove the card and reboot. Without the card the stock SPL is designed to
   fall through to the eMMC's copy of our FIT.

U-Boot is not part of an over-the-air upgrade: upgrades write only p2-p4 and
the state block. A new U-Boot arrives only through `complete` or `install`.

## A/B slots and the state block

The state block (the first 128 KiB of p1; written by fwup and `fw_setenv`,
read and saved by the boot script) holds the Nerves A/B variables. A slot that
boots twice without being validated is reverted. U-Boot saves the block only
back to the disk whose block it read and CRC-checked in the same boot, so a
disk without our state block, such as a stock eMMC, is never written by
U-Boot.

If the block cannot be read (a torn save after a power cut, corruption),
U-Boot boots slot A on its built-in defaults. That can be an older firmware
than the one last validated, running against `/data` written by a newer one.

## Watchdog

The SoC watchdog is not started by default. `fw_setenv nerves_watchdog 1`
makes the boot script start it just before the kernel, with its longest
timeout (about 89 s) and a reset on expiry, so a hung kernel resets and the
next SD boot saves its ramoops log. Once started it cannot be stopped until it
resets the board, so enable it only after Linux has been seen to take it over
(`heart` opening `/dev/watchdog0`); otherwise the board resets about 89 s into
every boot. It is off because U-Boot's driver cannot probe on RK3568 (the
script writes the registers directly) and because of that one-way behaviour.
While it is off, a hard kernel hang needs a power cycle and the ramoops log is
probably lost; panics still reboot after 10 s, and the kernel panics on a soft
lockup or an RCU stall (a hung task is only logged).

## Restore to stock Home Assistant OS

Restoring Home Assistant OS is done with the official Home Assistant Green
installer card, which rewrites the whole eMMC, loader included, with Nabu
Casa's own images; data on the eMMC is lost. This system provides no `dd`
path back to stock of its own.

## Hardware

| Feature | Intended support | Status |
| --- | --- | --- |
| Ethernet (GbE) | `stmmac` driver | planned, not yet verified on hardware |
| USB | USB 2.0 host | planned, not yet verified on hardware |
| HDMI display and audio | DRM/KMS, ALSA | planned, not yet verified on hardware |
| NPU | Rockchip `rknpu` driver 0.9.8 | planned, not yet verified on hardware |
| GPU | Mali-G52, `kbase` g29p1 | planned, not yet verified on hardware |
| Video codecs | Rockchip MPP (`/dev/mpp_service`) | planned, not yet verified on hardware |
| RGA 2D blitter | `/dev/rga`; driver enabled; not relied on: the RGA2 core addresses only the first 4 GiB of RAM, and a buffer above that fails the job | planned, not yet verified on hardware |
| RTC | PCF8563 | planned, not yet verified on hardware |

No onboard WiFi or Bluetooth (the vendor kernel config still includes USB Bluetooth support). No serial console is needed; the kernel console is on
`tty0` and `ttyS2`.

The kernel provides drivers and device nodes only. The userspace libraries
(`librknnrt`, the Mali userspace driver, MPP and RGA libraries) are not part
of this system; they live in the application images that use them.

## Building

```sh
mix deps.get
mix compile    # builds the system with Buildroot
```

The guards run inside the build and need only `python3` and the build's own
`fwup`. The test suites run on a host:

```sh
scripts/check-env-config.sh
scripts/check-no-guid-lookup.sh
sh scripts/test-guards.sh                  # fwup, python3; strace optional, without it the write trace is skipped
sh scripts/test-green-install-emmc.sh      # also with --break-test
scripts/test-boot-script.sh --cache <dir>  # builds U-Boot's sandbox
```

`test-boot-script.sh` needs gcc, make, bison, flex, python3 and fwup, plus
either `DTC` and `PYTHON3` pointing at a dtc and a Python with libfdt, or
swig and python3 setuptools. The header of each script under `scripts/` says
what it takes as arguments.

## Licenses and provenance

See [NOTICE](NOTICE) and `LICENSES/`. The U-Boot patches are Home Assistant
OS's Green patches, unmodified ([uboot/patches/README.md](uboot/patches/README.md)).
The kernel patch `linux/0001-arm64-dts-rockchip-add-ha-green.patch` adds
`rk3566-ha-green.dts` (`GPL-2.0+ OR MIT`, Copyright Seeed Co., Ltd.), ported
from the Green device tree in Home Assistant OS 18.4.
`linux/0002-soc-rockchip-pm-domains-fail-power-up-without-panic.patch`
makes a power domain that fails its first power-up fail the attaching
device's probe instead of panicking the kernel.
The DDR and BL31 blobs are Rockchip's rkbin
([uboot/rkbin/README.md](uboot/rkbin/README.md)). The build scaffolding and
fwup configuration derive from `nerves_system_rock_4d`.
