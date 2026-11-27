# Viettel SDMC NR3053 — OpenWrt / ImmortalWrt Guide

Documentation, hardware teardown photos, and flashing guides for installing **OpenWrt 24.10**, **ImmortalWrt 25.12**, or **KeeneticOS** on the **Viettel SDMC NR3053** Wi-Fi 6 (AX3000) router.

> [!TIP]
> **Bilingual Documentation & Interactive 256 MiB Flash Map:** View the GitHub Pages site at **[https://kreier.github.io/nr3053/](https://kreier.github.io/nr3053/)** (**English**, default) or **[https://kreier.github.io/nr3053/vi/](https://kreier.github.io/nr3053/vi/)** (**Tiếng Việt**), built from [`site/`](site/) via [`.github/workflows/pages.yml`](.github/workflows/pages.yml).

![Viettel SDMC NR3053 exterior](images/nr3053-exterior.jpg)

---

## Hardware Overview & Teardown

Manufactured by **Shenzhen SDMC Technology Co., Ltd.** for Viettel, the NR3053 offers strong hardware for OpenWrt/ImmortalWrt:

| Component | Specification | Notes |
| --- | --- | --- |
| **SoC / CPU** | MediaTek **MT7981BA** (Filogic 820) | Dual-core ARM Cortex-A53 @ 1.3 GHz, HNAT / WED |
| **RAM** | **512 MiB** DDR3L | Linkage `4GF4MLF-GJS` (4 Gbit) |
| **SPI NAND Flash** | **256 MiB** SPI NAND | Dosilicon `DS35Q2GBS` (2 Gbit, WSON-8, 128 KiB erase block) |
| **Wi-Fi 6 RF** | MediaTek **MT7976CN** | AX3000 (2.4 GHz 574 Mbps + 5 GHz 2402 Mbps @ 160 MHz, internal FEMs) |
| **Ethernet Switch** | MediaTek **MT7531AE** | 4× Gigabit RJ45 (`1 WAN + 3 LAN`), `2500base-x` CPU link |
| **Antennas** | 4× external 5 dBi | 5 RF leads on PCB: 3× 5 GHz (`CONG3–5`) + 2× 2.4 GHz (`CONG1–2`) |
| **Serial Debug** | 4-pin **3.3 V TTL UART** | Clearly silkscreened `GND`, `TX`, `RX`, `VCC` @ `115200 8N1` |
| **Buttons / LEDs** | WPS/Mesh (GPIO 0), Reset (GPIO 1) | Status LED Green (GPIO 4) / Red (GPIO 6), active-low |

### Inside the Router

| Front PCB (Heatsink, 5× RF Leads & Exposed UART) | Core ICs (`MT7981BA`, `MT7531AE`, `MT7976CN`, 512 MiB RAM) |
| :---: | :---: |
| ![Front PCB with heatsink and UART header](images/nr3053-pcb-heatsink.jpg) | ![Close-up of main ICs on NR3053 PCB](images/nr3053-pcb-chips.jpg) |

| Back PCB (`Dosilicon DS35Q2GBS` 256 MiB SPI NAND) | Stock U-Boot 2022.07-rc3 Web Failsafe |
| :---: | :---: |
| ![Back PCB with Dosilicon DS35Q2GBS SPI NAND](images/nr3053-pcb-back-nand.jpg) | ![Stock U-Boot Web Failsafe screen](images/nr3053-stock-uboot.jpg) |

*Teardown and U-Boot Web Failsafe photos sourced from [AIoT.IO.VN (Ng Nghia Phan)](https://aiot.io.vn/serial/disassemble/kham-pha-phan-cung-viettel-nr3053/).*

---

## Installation Paths

### 1. Stock Web Failsafe (No SSH Required — Easiest)

Stock U-Boot (`2022.07-rc3`) includes a built-in Web Failsafe server:

1. Unplug power, connect PC to a **LAN** port, hold **MESH** (WPS), and plug power back in for ~5 seconds until the LED turns from red to **solid green**.
2. Set PC static IP to `192.168.1.2/24` and open `http://192.168.1.1/`.
3. Upload a stock-U-Boot-compatible **OpenWrt 24.10** image (`openwrt-mediatek-filogic-viettel_nr3053-stock-squashfs-factory_fix.bin`), verify MD5, and click **Update**.
   *(Alternatively, flash the custom ImmortalWrt `.fip` via `http://192.168.1.1/uboot.html`, then set PC IP to `192.168.1.254/24` and upload `squashfs-sysupgrade.itb`.)*

### 2. Unlock SSH & Install ImmortalWrt 25.12 (234 MiB Native UBI)

1. Perform a **factory reset** (hold `Reset` > 10 s until the LED turns off and back on). The stock web password becomes the **Serial Number (S/N)** printed on the bottom label.
2. Unlock SSH with [`quytttb/nr3053-tools`](https://github.com/quytttb/nr3053-tools):
   ```bash
   git clone https://github.com/quytttb/nr3053-tools.git
   cd nr3053-tools
   pip3 install -r requirements.txt
   python3 unlock_nr3053.py --password YOUR_SERIAL_NUMBER
   ```
3. Log in (`ssh root@192.168.1.1`, password `admin`), back up `mtd1`–`mtd4` (**especially `mtd3` `Factory`**), copy `immortalwrt-mediatek-filogic-viettel_nr3053-bl31-uboot.fip` to `/tmp/fip.bin`, and flash it:
   ```sh
   mtd write /tmp/fip.bin FIP
   sync && reboot
   ```
4. Set PC static IP to **`192.168.1.254/24`**, open `http://192.168.1.1/`, and flash `immortalwrt-mediatek-filogic-viettel_nr3053-squashfs-sysupgrade.itb`.

> [!WARNING]
> **Never erase the `Factory` partition (`0x200000`, 2 MiB)** — it stores your unique Wi-Fi RF calibration EEPROM and Ethernet MAC addresses.

---

## Firmware & Community Sources

### ImmortalWrt 25.12 (v2.5.0)
- [`quytttb/immortalwrt-mt798x-rebase`](https://github.com/quytttb/immortalwrt-mt798x-rebase) — latest `v2.5.0` (2026-10-30, includes USB-mod support) and [`v2.4.5`](https://github.com/quytttb/immortalwrt-mt798x-rebase/releases/tag/v2.4.5) (2026-10-16)
- Bootloader source: [`quytttb/bl-mt798x-dhcpd`](https://github.com/quytttb/bl-mt798x-dhcpd)
- Wi-Fi Roaming (802.11r/k/v) & SQM Anti-Bufferbloat tuning: [hashcott Gist](https://gist.github.com/hashcott/68e337ac52f3410e6d19a177fb5bc1bf)

### OpenWrt 24.10 (Stock-U-Boot Compatible)
- [AIoT.IO.VN — Flash OpenWrt without SSH](https://aiot.io.vn/firmware/viettel/viettel-sdmc-nr3053-posts/h%C6%B0%C6%A1ng-dan-flash-openwrt-khong-can-mo-ssh-nr3053/) (2026-10-21)
- [AIoT.IO.VN — Unlock SSH & Flash Guide](https://aiot.io.vn/firmware/viettel/viettel-sdmc-nr3053-posts/huong-dan-mo-ssh-flash-firmware-rn3053/) (2026-10-16)
- [AIoT.IO.VN — Hardware Teardown](https://aiot.io.vn/serial/disassemble/kham-pha-phan-cung-viettel-nr3053/) (2026-10-14)

### KeeneticOS 5.01 (DIY Port)
- [`linhsayshii/keeneticos-nr3053`](https://github.com/linhsayshii/keeneticos-nr3053) — SDK patches & [Bootloader `v32`](https://github.com/linhsayshii/keeneticos-nr3053/releases/tag/v32) (2026-10-20)

---

## Building the GitHub Pages Site Locally

Requires Python 3 (standard library only, zero external packages):

```bash
python3 site/build.py
python3 -m http.server -d site/dist 8000
```
