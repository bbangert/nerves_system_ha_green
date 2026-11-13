# U-Boot

Boot chain:

1. BootROM → the board's stock TPL/SPL (SPI NOR, else eMMC LBA 64–16383; never written by us).
2. The SPL loads our `u-boot.itb` from raw LBA 16384 of the SD card if it carries FDT magic there, else of the eMMC.
3. Our U-Boot runs the boot script below and boots `Image.<slot>` with `booti`.

U-Boot proper is v2026.04 plus Home Assistant OS's Green patches
(`patches/`), `green_defconfig`, and `green.fragment`: HAOS's own Green
fragments plus `ENV_SIZE=0x20000`, `VERSION_VARIABLE` and no `sf` command.
The whole default environment, and so the whole boot logic, is
`uboot.env`, compiled in through `BR2_TARGET_UBOOT_DEFAULT_ENV_FILE`. Only
`u-boot.itb` is installed. The stock SPL's FIT contract must hold in the
resolved config: `SPL_FIT_SIGNATURE=y`, `SPL_SHA256=y`, no `SPL_GZIP` or
`SPL_LZMA`.

## Boot script

There is no console, so the script never stops at a prompt, and its only
outputs are an LED, files on the SD card, and whether Linux comes up.
`scripts/test-boot-script.sh` runs this exact file in U-Boot's sandbox
against two disk images and checks every case below.

1. **Boot disk.** The SD card (`mmc 1`) iff it reads and holds FDT magic
   at LBA 0x4000, the SPL's own rule; otherwise the eMMC (`mmc 0`).
   `/chosen/u-boot,spl-boot-device` is not used: older SPLs, including the
   one in the NOR, do not set it. Nothing is looked up by GUID or name.
2. **State.** 256 blocks at LBA 0x8000 of the boot disk (the first
   128 KiB of p1, what `fw_env.config` and fwup address) are imported with
   `env import -c`. A missing, corrupt or foreign block fails its CRC, and
   the boot goes on with the compiled-in defaults: slot A, validated.
   After a good import the script's own variables (the list in
   `nerves_owned`) go back to their compiled-in values, and the values
   U-Boot computed this boot (`ver`, `fdtcontroladdr`, `serial#`,
   `ethaddr`, `nerves_disk`) come back from a RAM stash, so whatever an
   older export left in the block cannot change this boot.
3. **A/B,** the Nerves rule: the first boot of a slot sets
   `nerves_fw_booted=1`; a slot that boots again with
   `nerves_fw_validated=0` is reverted (`nerves_fw_active` flips,
   `nerves_fw_validated=1`). `nerves_fw_autovalidate=1` validates on first
   boot.
4. **Save.** The only raw write U-Boot makes: `env export -c` of the whole
   environment to LBA 0x8000 of the boot disk, only when step 3 changed
   something and only if the import from that same disk passed its CRC in
   this boot. **A disk without our valid state block is never written by
   U-Boot**; this is what keeps a stock eMMC untouched while the system
   runs from SD. U-Boot cannot hold an empty value, so a key fwup wrote as
   `""` is gone from the block after a U-Boot save (as on every Nerves
   board).
5. **Kernel.** `Image.<slot>` and `rk3566-ha-green.<slot>.dtb` from
   `mmc <disk>:2`; `root=/dev/mmcblk<disk>p<3|4> rootfstype=squashfs
   rootwait console=ttyS2,1500000n8 console=tty0`, then
   `uboot.boot_disk=mmcblk<disk>` and `uboot.version=<ver>` (spaces and
   parentheses replaced by `_`), then `${cmdline}` (empty; settable with
   `fw_setenv`). Linux ignores the two `uboot.*` parameters; read them
   from `/proc/cmdline`.
6. **Failure.** If a load or `booti` fails: user LED off, 10 s pause (so a
   persistent failure does not hammer the disks), `reset`.

LED code (user LED): solid from the first line of the script; off for
10 s before each retry when U-Boot cannot boot; heartbeat once the kernel
runs (kernel DTS).

### Breadcrumbs

Only when the boot disk is the SD card, on its FAT partition (`mmc 1:2`,
a literal, so they can never land on the eMMC). Fixed names, and each is
written only if it differs from the copy already on the card, so
steady-state boots and retry loops write nothing:

| File | Content |
| --- | --- |
| `uboot-vars.txt` | boot disk, state result, slot, A/B variables, `nerves_spl_boot_device` (`/chosen/u-boot,spl-boot-device` or `absent`), `serial#`, `ethaddr`, `ver`, `bootargs` |
| `uboot-fdt.dtb` | U-Boot's control FDT as the SPL handed it over (`dtc -I dtb` shows every `/chosen` property) |
| `ramoops.bin` | RAM 0x110000–0x1fffff, the kernel's ramoops region, only if a kernel left its signature there: the previous boot's console log, when RAM survived the reset |

### Watchdog

Off by default; `fw_setenv nerves_watchdog 1` turns it on. The script then
starts the SoC watchdog (dw-wdt, 0xfe600000) just before `booti` with its
longest timeout, 2^31 cycles of 24 MHz (about 89 s), reset on first
expiry, so a hung kernel resets and the next SD boot saves its ramoops
log. These are plain register writes: U-Boot's driver cannot probe on
RK3568 (the clock driver has no enable op). Once started the dw-wdt only
stops on reset, so turn it on only after Linux is seen to take it over
(`dw_wdt` built in, `WATCHDOG_HANDLE_BOOT_ENABLED=y`, then `heart`);
otherwise the board resets about 89 s into every boot.
