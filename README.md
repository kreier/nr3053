# nr3053
Details about the Vietel SDMC NR3053 Wifi6 router and firmwares like KeeneticOS and OpenWRT.

## Unlock the Router

You might want to unlock SSH first. After a **factory reset** (press and hold the Reset button for more than 10 seconds until the light turns off and then back on), the website admin password will now be the Serial Number (S/N) printed on the bottom of the router. Taken from [aiot.io.vn](https://aiot.io.vn/firmware/viettel/viettel-sdmc-nr3053-posts/huong-dan-mo-ssh-flash-firmware-rn3053/) 2026-10-16

### Unlock tool

```
git clone https://github.com/quytttb/nr3053-tools.git
cd nr3053-tools
pip3 install -r requirements.txt
python3 unlock_nr3053.py --password SERIAL_NUMBER_CUA_BAN
```

## Hardware

More here:

- https://aiot.io.vn/serial/disassemble/kham-pha-phan-cung-viettel-nr3053/ 14.10.2026
- https://aiot.io.vn/firmware/viettel/viettel-sdmc-nr3053-posts/h%C6%B0%C6%A1ng-dan-flash-openwrt-khong-can-mo-ssh-nr3053/ flash firmware 26.10.2026

## Firmware

### ImmortalWrt 2.5.0

- https://github.com/quytttb/immortalwrt-mt798x-rebase - if you already have the USB mod - 2026-10-30
- https://github.com/quytttb/immortalwrt-mt798x-rebase/releases/tag/v2.4.5 - 2026-10-16

### KeeneticOS

- https://github.com/linhsayshii/keeneticos-nr3053 with claude - 2026-10-20
- https://github.com/linhsayshii/keeneticos-nr3053/releases/tag/v32

### OpenWRT 24.10 (no 25.12 yet)

- https://aiot.io.vn/firmware/viettel/viettel-sdmc-nr3053-posts/h%c6%b0%c6%a1ng-dan-flash-openwrt-khong-can-mo-ssh-nr3053/
