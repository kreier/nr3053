#!/usr/bin/env python3
"""
Bilingual (EN / VI) multi-page static site builder for https://kreier.github.io/nr3053/

Features:
- 3-column docs layout inspired by kreier/H3C_MAGIC_RT3000:
  * Left sidebar: categorized multi-page navigation with labeled sections
  * Center: focused page content + breadcrumbs + Prev/Next pager
  * Right sidebar: auto-extracted "On this page" (h2/h3) scrollspy TOC
- Top-right language switcher (English <-> Tiếng Việt) preserving the exact page,
  GitHub repository button, and Light/Dark theme toggle. English is the default root.
- Pure Python 3 standard library only (zero npm/Ruby dependencies).
- Built-in validation: contiguous 256 MiB SPI NAND partition math, zero broken internal
  links/anchors/assets across all generated EN & VI pages.

Usage:
    python3 site/build.py
    python3 -m http.server -d site/dist 8000
"""

import html
import json
import posixpath
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "site"
DIST_DIR = SITE_DIR / "dist"
DATA_FILE = SITE_DIR / "data" / "partitions.json"
LAYOUT_FILE = SITE_DIR / "layout.html"
PAGES_DIR = SITE_DIR / "pages"
ASSETS_SRC = SITE_DIR / "assets"
IMAGES_SRC = ROOT / "images"

# Navigation tree (ordered — also defines Prev/Next pager order)
NAV_GROUPS = [
    {
        "group": None,
        "items": [
            {
                "path": "index.html",
                "en": (
                    "Home",
                    "Complete OpenWrt 24.10, ImmortalWrt 25.12, and KeeneticOS guide for the Viettel SDMC NR3053 (MediaTek MT7981BA).",
                ),
                "vi": (
                    "Trang chủ",
                    "Hướng dẫn đầy đủ cài đặt OpenWrt 24.10, ImmortalWrt 25.12 và KeeneticOS cho Viettel SDMC NR3053 (MediaTek MT7981BA).",
                ),
            },
        ],
    },
    {
        "group": {"en": "Hardware", "vi": "Phần cứng"},
        "items": [
            {
                "path": "hardware/index.html",
                "en": (
                    "Hardware & Teardown",
                    "Full teardown photos, PCB components (MT7981BA, 512 MiB DDR3L, 256 MiB Dosilicon SPI NAND, MT7976CN, MT7531AE), UART & GPIO pinout.",
                ),
                "vi": (
                    "Phần cứng & Mổ xẻ",
                    "Ảnh tháo vỏ và bo mạch PCB (MT7981BA, 512 MiB DDR3L, 256 MiB Dosilicon SPI NAND, MT7976CN, MT7531AE), chân UART và GPIO.",
                ),
            },
            {
                "path": "hardware/partitions.html",
                "en": (
                    "256 MiB Partition Layout",
                    "Interactive 256 MiB Dosilicon DS35Q2GBS SPI NAND flash map comparing ImmortalWrt Native UBI, Stock Viettel, and KeeneticOS layouts.",
                ),
                "vi": (
                    "Bố cục phân vùng 256 MiB",
                    "Sơ đồ tương tác SPI NAND 256 MiB Dosilicon DS35Q2GBS so sánh layout ImmortalWrt Native UBI, ROM gốc Viettel và KeeneticOS.",
                ),
                "embed_partitions": True,
            },
        ],
    },
    {
        "group": {"en": "Bootloader & Firmware", "vi": "Bootloader & Firmware"},
        "items": [
            {
                "path": "bootloader/index.html",
                "en": (
                    "Boot Process & U-Boot",
                    "MediaTek MT7981B boot chain (BootROM, BL2, FIP/U-Boot, FIT/UBI), Stock Web Failsafe vs ImmortalWrt U-Boot bootmenu.",
                ),
                "vi": (
                    "Quy trình Boot & U-Boot",
                    "Chuỗi khởi động MediaTek MT7981B (BootROM, BL2, FIP/U-Boot, FIT/UBI), Web Failsafe gốc và bootmenu U-Boot ImmortalWrt.",
                ),
            },
            {
                "path": "firmware/index.html",
                "en": (
                    "Firmware Options",
                    "Compare ImmortalWrt 25.12 (v2.5.0), stock-U-Boot OpenWrt 24.10, and DIY KeeneticOS 5.01 ports for the NR3053.",
                ),
                "vi": (
                    "Lựa chọn Firmware",
                    "So sánh ImmortalWrt 25.12 (v2.5.0), OpenWrt 24.10 tương thích U-Boot gốc và bản port tự build KeeneticOS 5.01 cho NR3053.",
                ),
            },
        ],
    },
    {
        "group": {"en": "Installation", "vi": "Cài đặt"},
        "items": [
            {
                "path": "install/web-failsafe.html",
                "en": (
                    "1. Stock Web Failsafe (No SSH)",
                    "Easiest method: hold MESH at power-on to enter stock U-Boot Web Failsafe and flash OpenWrt 24.10 or custom FIP without SSH.",
                ),
                "vi": (
                    "1. Flash qua Web Failsafe gốc",
                    "Cách đơn giản nhất: giữ nút MESH khi cắm nguồn vào Web Failsafe gốc để nạp OpenWrt 24.10 hoặc FIP mà không cần mở SSH.",
                ),
            },
            {
                "path": "install/ssh-immortalwrt.html",
                "en": (
                    "2. Unlock SSH & ImmortalWrt",
                    "Factory reset, unlock SSH via nr3053-tools, back up Factory MTD, flash ImmortalWrt FIP to mtd4, and install sysupgrade.itb.",
                ),
                "vi": (
                    "2. Mở SSH & cài ImmortalWrt",
                    "Factory reset, mở SSH bằng nr3053-tools, sao lưu phân vùng Factory, ghi FIP vào mtd4 và nạp sysupgrade.itb qua Web Failsafe.",
                ),
            },
            {
                "path": "install/uart-recovery.html",
                "en": (
                    "3. UART Recovery & TFTP Boot",
                    "3.3 V TTL UART pinout, 2-window mtk_uartboot unbrick procedure, TFTP RAM boot test, permanent NAND install, and NMBM cleanup.",
                ),
                "vi": (
                    "3. Cứu Brick UART & Nạp TFTP",
                    "Sơ đồ chân UART 3.3 V TTL, quy trình 2 cửa sổ mtk_uartboot cứu brick, test RAM qua TFTP, cài vĩnh viễn NAND và xử lý NMBM.",
                ),
            },
        ],
    },
    {
        "group": {"en": "Tuning & Tools", "vi": "Tối ưu & Công cụ"},
        "items": [
            {
                "path": "use/tuning.html",
                "en": (
                    "Wi-Fi Roaming & SQM Tuning",
                    "Optimize ImmortalWrt 25.12 on MT7981B for seamless 802.11r/k/v roaming and Bufferbloat Grade A with SQM/cake.",
                ),
                "vi": (
                    "Tối ưu Wi-Fi Roaming & SQM",
                    "Cấu hình ImmortalWrt 25.12 trên MT7981B với Wi-Fi roaming 802.11r/k/v mượt mà và SQM/cake đạt Bufferbloat Grade A.",
                ),
            },
            {
                "path": "tools/index.html",
                "en": (
                    "Tools & Troubleshooting",
                    "Reference for nr3053-tools (unlock & Factory through-wall patch) plus fixes for common Web Failsafe, SCP, TFTP, and UART issues.",
                ),
                "vi": (
                    "Công cụ & Gỡ lỗi",
                    "Hướng dẫn công cụ nr3053-tools (mở SSH & patch Factory through-wall) và cách xử lý lỗi Web Failsafe, SCP, TFTP và UART.",
                ),
            },
        ],
    },
]

LANG_META = {
    "en": {
        "prefix": "",
        "brand_sub": "OpenWrt Guide",
        "alt_code": "vi",
        "alt_label": "Tiếng Việt",
        "menu_label": "Menu",
        "nav_aria": "Documentation navigation",
        "toc_title": "On this page",
        "prev_label": "Previous",
        "next_label": "Next",
    },
    "vi": {
        "prefix": "vi/",
        "brand_sub": "Hướng dẫn OpenWrt",
        "alt_code": "en",
        "alt_label": "English",
        "menu_label": "Danh mục",
        "nav_aria": "Menu tài liệu",
        "toc_title": "Trên trang này",
        "prev_label": "Trang trước",
        "next_label": "Trang sau",
    },
}


def get_git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out or "local"
    except Exception:
        return "local"


def validate_partitions(data: dict) -> None:
    chip = data["chip"]
    total_kib = chip["total_kib"]
    total_bytes = total_kib * 1024
    cursor = 0
    sum_kib = 0
    for p in data["partitions"]:
        if p["start"] != cursor:
            raise ValueError(
                f"Non-contiguous partition {p['name']}: expected 0x{cursor:08x}, got 0x{p['start']:08x}"
            )
        cursor += p["kib"] * 1024
        sum_kib += p["kib"]
    if sum_kib != total_kib or cursor != total_bytes:
        raise ValueError(f"Partition sum mismatch: {sum_kib} KiB != {total_kib} KiB")
    for mode, layout in data["layouts"].items():
        seg_cursor = 0
        seg_sum = 0
        for s in layout["segments"]:
            if s["start"] != seg_cursor:
                raise ValueError(
                    f"Layout '{mode}' segment {s['name']} non-contiguous: expected 0x{seg_cursor:08x}, got 0x{s['start']:08x}"
                )
            seg_cursor += s["kib"] * 1024
            seg_sum += s["kib"]
        if seg_sum != total_kib:
            raise ValueError(f"Layout '{mode}' sums to {seg_sum} KiB, expected {total_kib} KiB")


def render_partition_rows(data: dict, lang: str) -> str:
    total_kib = data["chip"]["total_kib"]
    rows = []
    for p in data["partitions"]:
        start = p["start"]
        end = start + p["kib"] * 1024
        kib = p["kib"]
        pct = (kib / total_kib) * 100.0
        size_str = f"{kib:,} KiB ({kib / 1024:.2f} MiB)"
        cls = "part-row uboot-row" if p.get("highlight") else "part-row"
        swatch_style = (
            "background: repeating-linear-gradient(-45deg, transparent, transparent 3px, var(--muted) 3px, var(--muted) 6px); border: 1px solid var(--border);"
            if p.get("hatch")
            else f"background: {p['color']};"
        )
        name_text = p.get("name_vi") if lang == "vi" and p.get("name_vi") else p["name"]
        desc_text = p.get("desc_vi") if lang == "vi" and p.get("desc_vi") else p["desc"]
        name_html = f"<strong>{html.escape(name_text)}</strong>" if p.get("highlight") else html.escape(name_text)
        rows.append(
            f'      <tr class="{cls}" data-mtd="{html.escape(p["mtd"])}">\n'
            f'        <td class="mono">{html.escape(p["mtd"])}</td>\n'
            f'        <td class="mono"><span class="swatch" style="{swatch_style};vertical-align:middle;margin-right:6px;"></span>{name_html}</td>\n'
            f'        <td class="mono">0x{start:08x}–0x{end:08x}</td>\n'
            f'        <td class="num">{size_str}</td>\n'
            f'        <td class="num">{pct:.2f}%</td>\n'
            f'        <td>{html.escape(desc_text)}</td>\n'
            f"      </tr>"
        )
    return "\n".join(rows)


def rel_href(from_out_path: str, to_out_path: str) -> str:
    """Compute a clean relative href between two paths inside site/dist/."""
    from_dir = posixpath.dirname(from_out_path) or "."
    rel = posixpath.relpath(to_out_path, from_dir)
    if rel == "index.html":
        return "./"
    if rel.endswith("/index.html"):
        return rel[: -len("index.html")]
    return rel


def root_prefix(out_path: str) -> str:
    depth = len(Path(out_path).parts) - 1
    return "../" * depth


def extract_right_toc(body_html: str) -> str:
    pattern = re.compile(r'<(h[23])\s+[^>]*id="([^"]+)"[^>]*>(.*?)</\1>', re.DOTALL)
    entries = []
    for tag, hid, inner in pattern.findall(body_html):
        text = re.sub(r"<[^>]+>", "", inner).strip()
        cls = ' class="sub"' if tag == "h3" else ""
        entries.append(f'        <li{cls}><a href="#{html.escape(hid)}">{text}</a></li>')
    if not entries:
        return "      <ul></ul>"
    return "      <ul>\n" + "\n".join(entries) + "\n      </ul>"


def build_sidebar(lang: str, current_rel: str, current_out: str) -> str:
    prefix = LANG_META[lang]["prefix"]
    parts = []
    for grp in NAV_GROUPS:
        if grp["group"]:
            parts.append(f'      <p class="g">{html.escape(grp["group"][lang])}</p>')
        parts.append("      <ul>")
        for item in grp["items"]:
            target_out = prefix + item["path"]
            href = rel_href(current_out, target_out)
            title = item[lang][0]
            on = ' class="on"' if item["path"] == current_rel else ""
            parts.append(f'        <li><a href="{href}"{on}>{html.escape(title)}</a></li>')
        parts.append("      </ul>")
    return "\n".join(parts)


def build_pager(flat_items: list, idx: int, lang: str, current_out: str) -> str:
    prefix = LANG_META[lang]["prefix"]
    meta = LANG_META[lang]
    links = []
    if idx > 0:
        prev_item = flat_items[idx - 1]
        href = rel_href(current_out, prefix + prev_item["path"])
        links.append(
            f'      <a class="prev" href="{href}"><small>← {meta["prev_label"]}</small>'
            f'<strong>{html.escape(prev_item[lang][0])}</strong></a>'
        )
    if idx + 1 < len(flat_items):
        next_item = flat_items[idx + 1]
        href = rel_href(current_out, prefix + next_item["path"])
        links.append(
            f'      <a class="next" href="{href}"><small>{meta["next_label"]} →</small>'
            f'<strong>{html.escape(next_item[lang][0])}</strong></a>'
        )
    if not links:
        return ""
    return '      <nav class="pager" aria-label="Page navigation">\n' + "\n".join(links) + "\n      </nav>"


def verify_links(dist_dir: Path) -> int:
    html_files = sorted(dist_dir.rglob("*.html"))
    ids_by_file = {}
    for f in html_files:
        txt = f.read_text(encoding="utf-8")
        ids_by_file[f.resolve()] = set(re.findall(r'\bid="([^"]+)"', txt))

    checked = 0
    for f in html_files:
        txt = f.read_text(encoding="utf-8")
        leftover = re.findall(r"\{\{[A-Z0-9_]+\}\}", txt)
        if leftover:
            raise RuntimeError(f"Unreplaced placeholders in {f.relative_to(dist_dir)}: {leftover}")

        for attr, ref in re.findall(r'(href|src)="([^"]+)"', txt):
            if ref.startswith(("http://", "https://", "mailto:", "data:")):
                continue
            checked += 1
            if ref.startswith("#"):
                anchor = ref[1:]
                if anchor and anchor not in ids_by_file[f.resolve()]:
                    raise RuntimeError(f"Broken anchor {ref} in {f.relative_to(dist_dir)}")
                continue
            path_part, _, anchor = ref.partition("#")
            target = (f.parent / path_part).resolve()
            if target.is_dir():
                target = (target / "index.html").resolve()
            if not target.is_file():
                raise FileNotFoundError(
                    f'Broken local {attr}="{ref}" in {f.relative_to(dist_dir)} (resolved: {target})'
                )
            if anchor and target.suffix == ".html":
                if anchor not in ids_by_file.get(target, set()):
                    raise RuntimeError(
                        f"Broken anchor {ref} in {f.relative_to(dist_dir)} (missing #{anchor} in {target.relative_to(dist_dir)})"
                    )
    return checked


def build() -> None:
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    validate_partitions(data)
    layout_tpl = LAYOUT_FILE.read_text(encoding="utf-8")
    git_sha = get_git_sha()

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    (DIST_DIR / "assets").mkdir(parents=True)
    (DIST_DIR / "images").mkdir(parents=True)
    (DIST_DIR / "data").mkdir(parents=True)

    for src in ASSETS_SRC.iterdir():
        if src.is_file():
            shutil.copy2(src, DIST_DIR / "assets" / src.name)

    if not IMAGES_SRC.is_dir():
        raise FileNotFoundError(f"Missing required images directory: {IMAGES_SRC}")
    copied_images = 0
    for img in sorted(IMAGES_SRC.iterdir()):
        if img.is_file():
            shutil.copy2(img, DIST_DIR / "images" / img.name)
            copied_images += 1
    if copied_images == 0:
        raise RuntimeError(f"No images found in {IMAGES_SRC}")

    shutil.copy2(DATA_FILE, DIST_DIR / "data" / "partitions.json")

    flat_items = []
    group_by_path = {}
    for grp in NAV_GROUPS:
        for item in grp["items"]:
            flat_items.append(item)
            group_by_path[item["path"]] = grp["group"]

    built_count = 0
    for lang in ("en", "vi"):
        meta = LANG_META[lang]
        alt_lang = meta["alt_code"]
        alt_prefix = LANG_META[alt_lang]["prefix"]

        for idx, item in enumerate(flat_items):
            rel_path = item["path"]
            out_rel = meta["prefix"] + rel_path
            src_file = PAGES_DIR / lang / rel_path
            if not src_file.is_file():
                raise FileNotFoundError(f"Missing page content file: {src_file}")

            root = root_prefix(out_rel)
            body = src_file.read_text(encoding="utf-8")
            body = body.replace("{{ROOT}}", root)
            if item.get("embed_partitions"):
                body = body.replace("{{PARTITION_ROWS}}", render_partition_rows(data, lang))
                extra_scripts = (
                    f'  <script id="partition-data" type="application/json">'
                    f'{json.dumps(data, separators=(",", ":"))}</script>'
                )
            else:
                extra_scripts = ""

            grp = group_by_path[rel_path]
            home_href = rel_href(out_rel, meta["prefix"] + "index.html")
            if grp:
                crumbs = (
                    f'      <div class="crumbs"><a href="{home_href}">{html.escape(flat_items[0][lang][0])}</a>'
                    f'<span class="sep">/</span><span>{html.escape(grp[lang])}</span></div>'
                )
            else:
                crumbs = ""

            title, desc = item[lang]
            alt_href = rel_href(out_rel, alt_prefix + rel_path)
            rendered = (
                layout_tpl.replace("{{LANG}}", lang)
                .replace("{{TITLE}}", html.escape(title))
                .replace("{{DESCRIPTION}}", html.escape(desc))
                .replace("{{ROOT}}", root)
                .replace("{{HOME_HREF}}", home_href)
                .replace("{{BRAND_SUB}}", html.escape(meta["brand_sub"]))
                .replace("{{ALT_LANG_HREF}}", alt_href)
                .replace("{{ALT_LANG_CODE}}", alt_lang)
                .replace("{{ALT_LANG_LABEL}}", meta["alt_label"])
                .replace("{{MENU_LABEL}}", meta["menu_label"])
                .replace("{{NAV_ARIA}}", meta["nav_aria"])
                .replace("{{TOC_TITLE}}", meta["toc_title"])
                .replace("{{SIDEBAR_HTML}}", build_sidebar(lang, rel_path, out_rel))
                .replace("{{CRUMBS_HTML}}", crumbs)
                .replace("{{BODY_HTML}}", body)
                .replace("{{PAGER_HTML}}", build_pager(flat_items, idx, lang, out_rel))
                .replace("{{RIGHT_TOC_HTML}}", extract_right_toc(body))
                .replace("{{EXTRA_SCRIPTS}}", extra_scripts)
                .replace("{{GIT_SHA}}", html.escape(git_sha))
            )

            out_file = DIST_DIR / out_rel
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(rendered, encoding="utf-8")
            built_count += 1

    (DIST_DIR / ".nojekyll").write_text("", encoding="utf-8")
    checked_links = verify_links(DIST_DIR)
    print(
        f"Built site/dist successfully: {built_count} pages (EN + VI), "
        f"{copied_images} images copied, "
        f"{len(data['partitions'])} partition entries (256 MiB verified across {len(data['layouts'])} layouts), "
        f"{checked_links} local links/anchors verified, git={git_sha}"
    )


if __name__ == "__main__":
    build()
