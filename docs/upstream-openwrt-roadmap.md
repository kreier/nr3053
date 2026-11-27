# Upstream OpenWrt (`ubootmod`) Roadmap & Recovery Notes

This document captures the architectural trade-offs, hardware recovery guarantees, and step-by-step plan for developing official upstream **OpenWrt (`mediatek/filogic`)** support for the **Viettel SDMC NR3053** (`MT7981BA` + `MT7976CN` + `MT7531AE` + `Dosilicon DS35Q2GBS` 256 MiB SPI NAND).

---

## 1. Hardware Unbrick Guarantee & Restoring Stock

The **MediaTek MT7981BA** includes an immutable, read-only **Stage 0 BootROM (`BROM`)** in silicon:

- Even if the entire 256 MiB SPI NAND chip (`BL2`, `FIP`, partition table, UBI) is erased or corrupted, BootROM still executes on power-on and listens on the exposed 4-pin **3.3 V TTL UART** header (`GND`, `TX`, `RX`, `VCC`) for a handshake from `mtk_uartboot`.
- Using a **3.3 V USB-to-TTL adapter** (*never* 5 V, and do *not* connect `VCC`), `mtk_uartboot` loads a RAM-only `BL2` + `FIP` (U-Boot) directly into the 512 MiB DDR3L RAM, completely bypassing NAND so you can TFTP-boot `initramfs` and reflash any partition.
- **Only two things cause permanent damage:** electrical damage (e.g., 5 V on UART) or losing your unit's unique **`Factory`** (`mtd3`) partition without a backup.

### Backup Privacy & Optional Full-Chip Snapshot

> [!CAUTION]
> **Never commit MTD backups (`.bin` / `.7z`) to a public Git repository.**
> `mtd3_Factory.bin` / `mtd9_Factory_backup.bin` contain your router's unique MAC addresses and RF calibration EEPROM; `mtd7_rootfs_data.bin` / `mtd8_rootfs_data_backup.bin` contain `/overlay` configuration (passwords, Wi-Fi keys, logs, ISP settings). Keep your `nr3053-stock.7z` backup private and offline.

- A verified backup of `mtd1`–`mtd10` (`240 MiB`, `0x00000000`–`0x0f000000`) covers every named stock partition. `ubootmod` leaves the upper `16 MiB` (`0x0f000000`–`0x10000000`, where MediaTek's NMBM management table sits) untouched.
- Optionally, capture a single raw full-chip snapshot of `mtd0` (`spi0.0`, `256 MiB` including the 16 MiB NMBM tail) before any flash layout changes:
  ```bash
  python3 scripts/nr3053_helper.py backup --host 10.1.1.1 --only mtd0 --include-full --out ./backups/nr3053-stock-full
  ```
- **Reverting to stock:** Boot into U-Boot or a RAM-booted `initramfs`, write back `mtd4_FIP.bin`, `mtd2_u-boot-env.bin`, `mtd5_firmware.bin` (and optionally `mtd6`–`mtd10`), sync, and reboot.

---

## 2. Bootloader Options & Why Upstream OpenWrt Prefers `ubootmod`

### Comparison of Available NR3053 Bootloaders

| Bootloader | Web UI / Recovery Features | Keeps Viettel `mtd5`/`mtd6` Dual Layout? | LED-Blink A/B Slot Selector? |
| --- | --- | --- | --- |
| **Stock Viettel `U-Boot 2022.07-rc3`** | Basic static-IP Web Failsafe (`http://192.168.1.1/`, `/uboot.html`) | **Yes** (`mtd5` + `mtd6`, switched via `u-boot-env` `bootimage=1\|2`) | **No** |
| **`quytttb/bl-mt798x-dhcpd` (`v1.1.0`, U-Boot `2025.07`)** | Bootstrap Web UI with built-in **DHCPD**, System Info, Factory update, Flash Backup download, Env Manager, Web Terminal, Flash Editor, UBI Volume Manager | **No** — merges flash above `0x600000` into a single `ubi` partition | **No** (single OS layout) |
| **`linhsayshii/keeneticos-nr3053`** | Keenetic recovery | Uses custom Keenetic A/B slots (`Firmware_1`/`Firmware_2`), incompatible with Viettel stock | **No** (automatic failover only) |
| **Mainline OpenWrt `uboot-mediatek` (`ubootmod`)** *(target for upstream PR)* | Upstream U-Boot serial `bootmenu`, TFTP, plus **built-in NAND `recovery` UBI volume** (boots OpenWrt `initramfs` directly from NAND) | **No** — single 234 MiB Native UBI (no NMBM) | **No** (production `fit` + built-in `recovery` volume) |

### Why Upstream OpenWrt Does Not Dual-Boot Stock + OpenWrt on MediaTek Filogic

Unlike Qualcomm IPQ dual-slot routers (e.g., H3C Magic RT3000, where `rootfs` and `rootfs_1` are completely self-contained UBI worlds switched via `0:BOOTCONFIG`), stock MediaTek SDK firmware on the NR3053:

1. Uses **MediaTek NMBM**, a proprietary out-of-tree bad-block translation layer that mainline Linux and upstream OpenWrt explicitly reject in favor of native Linux UBI.
2. Splits the OS across **4 separate UBI partitions** (`mtd5`/`mtd6` + shared `mtd7` `rootfs_data` + `mtd8` `rootfs_data_backup`), creating overlay conflicts if two different OSes share `mtd7`.

Accordingly, upstream OpenWrt's modern gold standard on `mediatek/filogic` is **`ubootmod` + Single Native UBI**:

| UBI Volume (inside 234 MiB `ubi`) | Purpose |
| --- | --- |
| `ubootenv` + `ubootenv2` | Redundant, wear-leveled U-Boot environment |
| `recovery` | Self-contained OpenWrt `initramfs` recovery system stored in NAND |
| `fit` | Active production OpenWrt FIT image (kernel + DTB + SquashFS) |
| `rootfs_data` | Dynamic UBIFS writable overlay using all remaining flash (~200+ MiB) |

*(OpenWrt also sometimes ships a separate `stock` profile alongside `ubootmod` for users who prefer not to replace the vendor bootloader.)*

---

## 3. Step-by-Step Plan for Upstream OpenWrt Support (`openwrt/openwrt`)

Zero new kernel drivers are required—`MT7981BA`, `MT7976CN` (`mt76`), `MT7531AE` (`mt7530` DSA), and `Dosilicon DS35Q2GBS` are already supported on `mediatek/filogic`.

### Step 1 — Capture Stock UART Log & Verify `mtk_uartboot` (~1–2 hours)
- Connect the 3.3 V USB-to-TTL adapter (`GND`, `TX` ↔ `RX`, `115200 8N1`, leave `VCC` disconnected).
- Capture a complete stock boot log confirming:
  - Exact SPI NAND JEDEC ID for `Dosilicon DS35Q2GBS` (`2048 B` page, `128 KiB` erase block, `128 B` OOB, `8-bit` ECC).
  - Exact stock MAC addresses for WAN, LAN, 2.4 GHz, and 5 GHz vs. bottom label and `Factory` offsets (`0x4`, `0x0a`, etc.).
- Verify `mtk_uartboot` successfully handshakes and loads a RAM image before writing anything to NAND.

### Step 2 — Set Up OpenWrt `main` Build Environment (~1–2 hours + ~1 hr first build)
- Clone `https://github.com/openwrt/openwrt` (`main` branch — all new device PRs must target `main`).
- Build an existing MT7981 `ubootmod` reference target (e.g., `cudy_tr3000-v1-ubootmod` or `xiaomi_mi-router-ax3000t-ubootmod`) to warm up the toolchain and use as a structural template.

### Step 3 — Add Board Support to ATF & U-Boot (~0.5–1 day)
- Confirm `DS35Q2GBS` SPI-NAND support in upstream `package/boot/arm-trusted-firmware-mediatek` and `package/boot/uboot-mediatek` (add JEDEC entry if missing).
- Add `mt7981_viettel_nr3053` board DTS and defconfig in `package/boot/uboot-mediatek` configuring:
  - Single 234 MiB Native UBI (`0x00600000`–`0x0f000000`) with `ubootenv`, `ubootenv2`, `recovery`, `fit`, `rootfs_data`.
  - Upper 16 MiB (`0x0f000000`–`0x10000000`) reserved tail.
  - GPIOs: `wps` (GPIO 0), `reset` (GPIO 1), `green:status` (GPIO 4), `red:status` (GPIO 6), MT7531 reset (GPIO 39).
  - Serial `bootmenu` + TFTP + button-triggered boot into NAND `recovery` volume.
- Register the `bl2` (RAM + SPI-NAND) and `fip` build targets in `package/boot/arm-trusted-firmware-mediatek/Makefile` and `package/boot/uboot-mediatek/Makefile`.

### Step 4 — Add Linux Device Tree & `filogic` Target Files (~0.5–1 day)
- Create `target/linux/mediatek/dts/mt7981b-viettel-nr3053.dts` following OpenWrt DTS conventions:
  - `nvmem-cells` for `Factory` Wi-Fi EEPROM (`0x0..0x5000`) and MAC addresses.
  - `mt7531` DSA switch ports (`wan`, `lan1`, `lan2`, `lan3`, CPU `2500base-x`).
  - Standard `chosen` LED aliases (`led-boot`, `led-failsafe`, `led-running`, `led-upgrade`).
- Register the device in:
  - `target/linux/mediatek/image/filogic.mk` (`preloader.bin`, `bl31-uboot.fip`, `initramfs-recovery.itb`, `squashfs-sysupgrade.itb`)
  - `target/linux/mediatek/filogic/base-files/etc/board.d/02_network`
  - `target/linux/mediatek/filogic/base-files/etc/hotplug.d/ieee80211/11_fix_wifi_mac` (only if Wi-Fi MAC derivation is needed)
  - `target/linux/mediatek/filogic/base-files/lib/upgrade/platform.sh` (verify generic FIT/UBI `sysupgrade` hook)

### Step 5 — Non-Destructive RAM-Boot Testing (~1–2 hours)
- Boot compiled `bl2-ram` + `fip` + `initramfs-recovery.itb` purely in RAM via `mtk_uartboot` (zero NAND writes).
- Verify checklist:
  - [ ] All 4 Gigabit Ethernet ports (`wan`, `lan1`–`lan3`) link at 1000 Mbps and pass traffic
  - [ ] 2.4 GHz & 5 GHz Wi-Fi (`mt76`) come up cleanly with calibration loaded and MACs matching sticker
  - [ ] Both LEDs (`green:status`, `red:status`) and both buttons (`reset`, `wps`) respond properly
  - [ ] SPI-NAND (`DS35Q2GBS`) detected with zero ECC errors and exact expected partitions

### Step 6 — Permanent NAND Flash & End-to-End Verification (~2–3 hours)
- Flash `bl31-uboot.fip`, install `recovery` + `squashfs-sysupgrade.itb` into `ubi`.
- Verify checklist:
  - [ ] Cold boot into production `fit` image
  - [ ] Button-triggered boot into NAND `recovery` volume
  - [ ] `sysupgrade` from OpenWrt to OpenWrt (with settings preserved)
  - [ ] Hardware reset button factory reset
  - [ ] Wi-Fi 6 stability & throughput (including 5 GHz 160 MHz)
  - [ ] Clean revert-to-stock test (optional sanity check)

### Step 7 — Submit Clean PR to `openwrt/openwrt` (~2–3 hours)
- Clean, properly split commits with `Signed-off-by:` (DCO) and subject `mediatek: filogic: add support for Viettel NR3053`.
- Include in commit message & PR description:
  - Full hardware specs, verified MAC address table, clean reproducible install instructions (no proprietary blobs), and full serial boot log.
  - Zero DTS compiler warnings (`make target/linux/compile V=s`).

### Step 8 — Maintainer Review & Merge
- **Hands-on engineering (Steps 1–7):** ~2–4 focused days (1–2 weekends).
- **Upstream PR review & merge:** Typically **2 weeks to 3 months** depending on `mediatek/filogic` maintainer queue (usually 1–3 rounds of DTS/commit feedback). Once merged into `main`, official snapshots build nightly within 24–48 hours and the device ships in the next official OpenWrt release.
