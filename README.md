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

1. The RK3566 BootROM starts the board's stock first stage: a factory SPL
   (U-Boot SPL 2023.07.02 was found in the SPI NOR of the unit examined)
   and/or the SPL in the eMMC at LBA 64-16383. Which one runs first is
   inferred from a missing `/chosen` property, not proven.
2. That SPL loads `u-boot.itb` (our U-Boot and BL31, a FIT) from raw LBA
   16384 of the SD card if the card carries FDT magic there, otherwise of the
   eMMC. This is the stock SPL's own rule, so a prepared SD card takes over
   the boot, and pulling it returns the unit to what the eMMC holds.
3. Our U-Boot runs one boot script (its compiled-in environment,
   `uboot/uboot.env`), loads the state block, and boots `Image.<slot>` with
   the matching device tree from the boot FAT, with the root filesystem on
   the same disk.

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
  off, so no driver can reach either place.
- U-Boot is built without the `sf` command and with no environment storage
  driver; its only raw write is the Nerves state block.
- No fwup task writes below LBA 32768 except the GPT (LBA 0-33 and its backup
  at the end of the disk) and the FIT window in `complete` and `install`. No
  idblock, SPL or TPL is built into `images/`, a `.fw` or the rootfs.
- The build runs guards from `scripts/` and fails if any of this stops being
  true: `check-loader-area.sh` (resolves every offset in the built `.fw`
  files and applies `complete` to a pattern-filled image),
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
| 34816 | p2 boot FAT, 256 MiB: `Image.a`, `Image.b`, `rk3566-ha-green.a.dtb`, `rk3566-ha-green.b.dtb` |
| 559104 | p3 rootfs A, squashfs, 512 MiB |
| 1607680 | p4 rootfs B, squashfs, 512 MiB |
| 2656256 | p5 application data, ext4, grows to the end of the disk |

The root device is chosen by name in the boot script, never by GUID or label.
U-Boot and Linux number the MMC devices alike: `mmcblk0` is the eMMC and
`mmcblk1` the SD card.

## LED stage codes

With no console, the user LED shows how far a boot got:

| User LED | Meaning |
| --- | --- |
| solid | Our U-Boot reached its boot script |
| off for 10 s, then solid again | U-Boot could not load or boot the kernel; it retries |
| heartbeat | Our kernel is running |

The activity LED shows eMMC and SD I/O. If stock Home Assistant OS comes up
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
2. Put the card in the Green and power it on. The stock first stage loads our
   U-Boot from the card; once Erlang is up the unit answers over SSH and mDNS
   (`nerves-<serial>`).
3. Copy the same `.fw` to the unit and run `green-install-emmc <fw>` to put
   the system on the eMMC. It refuses, writing nothing, unless the system runs
   from the SD card, `/dev/mmcblk0` is a non-removable MMC device that is not
   the boot disk and is not mounted or used as swap, an `RKNS` loader header
   sits at eMMC LBA 64 (a blank eMMC could not boot, and we never write a
   loader), the `.fw` is for platform `ha_green`, and the SD card carries a
   FIT. It then runs `fwup -t install` and prints the SHA-256 of eMMC LBA
   64-16383 before and after (they must match), the SHA-256 of the FIT window
   on the SD card and on the eMMC (they must match), and a final `PASS` or
   `FAIL`. After a `FAIL`, do not reboot into the eMMC; no repair is attempted.
4. Remove the card and reboot. Without the card the stock SPL falls through to
   the eMMC's copy of our FIT.

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
probably lost; panics still reboot after 10 s.

## Restore to stock Home Assistant OS

Boot the official Home Assistant Green installer SD card. It rewrites the
whole eMMC, loader included, with Nabu Casa's own images. This system provides
no `dd` path back to stock of its own.

## Hardware

| Feature | Intended support | Status |
| --- | --- | --- |
| Ethernet (GbE) | `stmmac` driver | planned, not yet verified on hardware |
| USB | USB 2.0 host | planned, not yet verified on hardware |
| HDMI display and audio | DRM/KMS, ALSA | planned, not yet verified on hardware |
| NPU | Rockchip `rknpu` driver 0.9.8 | planned, not yet verified on hardware |
| GPU | Mali-G52, `kbase` g29p1 | planned, not yet verified on hardware |
| Video codecs | Rockchip MPP (`/dev/mpp_service`) | planned, not yet verified on hardware |
| RGA 2D blitter | `/dev/rga`; scaling not supported | planned, not yet verified on hardware |
| RTC | PCF8563 | planned, not yet verified on hardware |

No WiFi or Bluetooth. No serial console is needed; the kernel console is on
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
`fwup`. To run the ones that take no build output by hand:

```sh
scripts/check-env-config.sh
scripts/check-no-guid-lookup.sh
scripts/test-guards.sh     # needs fwup
```

The header of each script under `scripts/` says what it takes as arguments.

## Licenses and provenance

See [NOTICE](NOTICE) and `LICENSES/`. The U-Boot patches are Home Assistant
OS's Green patches, unmodified ([uboot/patches/README.md](uboot/patches/README.md)).
The DDR and BL31 blobs are Rockchip's rkbin
([uboot/rkbin/README.md](uboot/rkbin/README.md)). The build scaffolding and
fwup configuration derive from `nerves_system_rock_4d`.
