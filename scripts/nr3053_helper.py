#!/usr/bin/env python3
"""
Viettel SDMC NR3053 helper v0.2.0 for a Linux/macOS host on the same LAN.

Modeled on H3C_MAGIC_RT3000/scripts/rt3000_helper.py, supporting BOTH Telnet (port 23)
and SSH (port 22) with zero external dependencies (Python 3 standard library only):

  - probe          : Read-only probe of /proc/mtd, sysfs NAND geometry, bad_blocks,
                     kernel/OS release, cmdline, mounts, DHCP leases, and dmesg.
  - backup         : Stream all MTD partitions (mtd1..mtd10 by default, or mtd0..mtd10
                     with --include-full) directly to local disk (without filling router
                     /tmp RAM), verify exact byte counts and SHA-256/MD5 against the
                     router, save router_info.txt, and optionally pack .7z.
  - inspect-backup : Offline analysis of a local backup directory or .7z archive:
                     computes non-0xFF used bytes/KiB, payload span, magic/format headers
                     (BL2, u-boot-env, Factory EEPROM, TF-A FIP & U-Boot banner, FIT/UBI/
                     SquashFS), compares redundant pairs (Factory vs Factory_backup,
                     FIP vs fip2, firmware vs firmware2), and prints a redacted report
                     suitable for public documentation.

By design, this script NEVER writes to flash.
"""

import argparse
import getpass
import hashlib
import http.server
import json
import os
import re
import select
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path

__version__ = "0.2.1"

SENSITIVE_ENV_KEYS = (
    "mac",
    "ethaddr",
    "pass",
    "pwd",
    "secret",
    "key",
    "sn",
    "serial",
    "imei",
    "pin",
    "psk",
    "wpa",
    "token",
)


def detect_local_ip(remote_host: str, remote_port: int = 23) -> str:
    """Detect the local interface IP used to reach remote_host (no packets sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((remote_host, remote_port))
        return s.getsockname()[0]
    finally:
        s.close()


def tcp_port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class RawTelnet:
    """Minimal pure-Python Telnet client (works on Python 3.13+ where telnetlib was removed)."""

    IAC = 255
    DONT, DO, WONT, WILL, SB, SE = 254, 253, 252, 251, 250, 240

    def __init__(
        self,
        host: str,
        user: str = "root",
        port: int = 23,
        password: str | None = None,
        timeout: float = 10.0,
    ):
        self.host = host
        self.user = user
        self.port = port
        self.password = password
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.buf = bytearray()
        self._seq = 0

    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.setblocking(False)
        self._login()

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def _strip_iac(self, data: bytes) -> bytes:
        assert self.sock is not None
        out = bytearray()
        i = 0
        n = len(data)
        while i < n:
            b = data[i]
            if b == self.IAC and i + 1 < n:
                cmd = data[i + 1]
                if cmd in (self.DO, self.DONT, self.WILL, self.WONT) and i + 2 < n:
                    opt = data[i + 2]
                    if cmd == self.DO:
                        self.sock.sendall(bytes([self.IAC, self.WONT, opt]))
                    elif cmd == self.WILL:
                        self.sock.sendall(bytes([self.IAC, self.DONT, opt]))
                    i += 3
                    continue
                if cmd == self.SB:
                    end = data.find(bytes([self.IAC, self.SE]), i + 2)
                    i = (end + 2) if end != -1 else n
                    continue
                i += 2
                continue
            out.append(b)
            i += 1
        return bytes(out)

    def _pump(self, timeout: float = 0.2) -> bool:
        assert self.sock is not None
        r, _, _ = select.select([self.sock], [], [], timeout)
        if not r:
            return False
        data = self.sock.recv(4096)
        if not data:
            return False
        self.buf.extend(self._strip_iac(data))
        return True

    def read_until(self, markers, timeout: float = 10.0) -> str:
        if isinstance(markers, (bytes, str)):
            markers = [markers]
        bmarkers = [m.encode() if isinstance(m, str) else m for m in markers]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for m in bmarkers:
                idx = self.buf.find(m)
                if idx != -1:
                    end = idx + len(m)
                    chunk = bytes(self.buf[:end])
                    del self.buf[:end]
                    return chunk.decode("utf-8", errors="replace")
            wait = max(0.02, min(0.25, deadline - time.monotonic()))
            self._pump(wait)
        chunk = bytes(self.buf)
        self.buf.clear()
        return chunk.decode("utf-8", errors="replace")

    def drain(self, quiet_sec: float = 0.2) -> None:
        while self._pump(quiet_sec):
            pass
        self.buf.clear()

    def write(self, text: str) -> None:
        assert self.sock is not None
        self.sock.sendall(text.encode("utf-8"))

    def _login(self) -> None:
        banner = self.read_until(
            ["login:", "Login:", "Username:", "Password:", "# ", "#\r", "#\n", "$ ", "> "],
            timeout=3.0,
        )
        if not banner.strip():
            self.write("\n")
            banner = self.read_until(
                ["login:", "Login:", "Username:", "Password:", "# ", "#\r", "#\n", "$ ", "> "],
                timeout=3.0,
            )
        last_line = next((ln.strip().lower() for ln in reversed(banner.splitlines()) if ln.strip()), "")
        if last_line.endswith(("login:", "username:")):
            self.write(f"{self.user}\n")
            banner = self.read_until(
                ["Password:", "password:", "# ", "#\r", "#\n", "$ ", "incorrect", "failed"],
                timeout=5.0,
            )
            last_line = next((ln.strip().lower() for ln in reversed(banner.splitlines()) if ln.strip()), "")
        if last_line.endswith("password:"):
            pw = self.password
            if pw is None:
                pw = getpass.getpass(f"Telnet password for {self.user}@{self.host}: ")
            self.write(f"{pw}\n")
            banner = self.read_until(
                ["# ", "#\r", "#\n", "$ ", "incorrect", "failed", "login:"],
                timeout=5.0,
            )
            if "incorrect" in banner.lower() or "failed" in banner.lower():
                raise RuntimeError(f"Telnet login failed:\n{banner.strip()}")
        self.drain(0.3)
        uid = self.run_text("id -u", timeout=8.0)
        print(f"[*] Telnet shell ready (uid={uid.strip() or 'unknown'})")

    def run_text(self, cmd: str, check: bool = True, timeout: float = 30.0) -> str:
        """Run a command with split-quoted markers so TTY echo never prematurely matches."""
        self._seq += 1
        seq = self._seq
        beg_lit = f"__B{seq}__"
        end_lit = f"__E{seq}__"
        beg_tok = f"'__B''{seq}__'"
        end_tok = f"'__E''{seq}__'"

        self.drain(0.05)
        self.write(f"echo {beg_tok}; {cmd}; echo ''; echo {end_tok}\n")

        end_re = re.compile(rf"(?:^|\n){re.escape(end_lit)}(?:\r?\n|$)")
        beg_re = re.compile(rf"(?:^|\n){re.escape(beg_lit)}\r?\n")
        deadline = time.monotonic() + timeout
        collected = bytearray()

        while time.monotonic() < deadline:
            if self.buf:
                collected.extend(self.buf)
                self.buf.clear()
            text = collected.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
            m_beg = beg_re.search(text)
            if m_beg:
                m_end = end_re.search(text, m_beg.end())
                if m_end:
                    return text[m_beg.end() : m_end.start()].strip()
            wait = max(0.02, min(0.25, deadline - time.monotonic()))
            self._pump(wait)

        text = collected.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        m_beg = beg_re.search(text)
        if m_beg:
            return text[m_beg.end() :].strip()
        if check:
            raise RuntimeError(f"Telnet command timed out after {timeout}s: {cmd}")
        return text.strip()

    def stream_mtd_to_file(
        self,
        dev: str,
        dest_path: str,
        expected_size: int,
        local_ip: str,
        listen_port: int = 0,
        stream_method: str = "nc",
    ) -> tuple[int, str, str]:
        """Stream /dev/<dev> from router to local host via nc (or curl HTTP PUT fallback)."""
        if stream_method == "nc":
            return self._stream_via_nc(dev, dest_path, expected_size, local_ip, listen_port)
        if stream_method == "curl":
            return self._stream_via_http_put(dev, dest_path, expected_size, local_ip, listen_port)
        raise RuntimeError(f"Unsupported Telnet stream method: {stream_method}")

    def _stream_via_nc(
        self, dev: str, dest_path: str, expected_size: int, local_ip: str, listen_port: int
    ) -> tuple[int, str, str]:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((local_ip, listen_port))
        srv.listen(1)
        srv.settimeout(60.0)
        bound_port = srv.getsockname()[1]

        state = {"bytes": 0, "sha256": "", "md5": "", "err": None}

        def receiver():
            try:
                conn, _ = srv.accept()
                conn.settimeout(120.0)
                h_sha256 = hashlib.sha256()
                h_md5 = hashlib.md5()
                total = 0
                last_print = time.monotonic()
                with open(dest_path, "wb") as fp:
                    while True:
                        chunk = conn.recv(131072)
                        if not chunk:
                            break
                        fp.write(chunk)
                        h_sha256.update(chunk)
                        h_md5.update(chunk)
                        total += len(chunk)
                        now = time.monotonic()
                        if expected_size >= 8 * 1024 * 1024 and now - last_print >= 1.5:
                            pct = min(100.0, total / expected_size * 100.0)
                            print(
                                f"\r    receiving {dev}: {total / (1024*1024):6.1f} / "
                                f"{expected_size / (1024*1024):.1f} MiB ({pct:5.1f}%)",
                                end="",
                                flush=True,
                            )
                            last_print = now
                conn.close()
                if expected_size >= 8 * 1024 * 1024:
                    print("\r" + " " * 70 + "\r", end="", flush=True)
                state["bytes"] = total
                state["sha256"] = h_sha256.hexdigest()
                state["md5"] = h_md5.hexdigest()
            except Exception as exc:
                state["err"] = exc
            finally:
                srv.close()

        t = threading.Thread(target=receiver, daemon=True)
        t.start()

        self.run_text(
            f"dd if=/dev/{dev} bs=131072 2>/dev/null | nc {local_ip} {bound_port}",
            check=False,
            timeout=900.0,
        )
        t.join(timeout=30.0)
        if state["err"]:
            raise RuntimeError(
                f"Failed receiving {dev} via nc on {local_ip}:{bound_port} "
                f"(check local firewall if timed out): {state['err']}"
            )
        return state["bytes"], state["sha256"], state["md5"]

    def _stream_via_http_put(
        self, dev: str, dest_path: str, expected_size: int, local_ip: str, listen_port: int
    ) -> tuple[int, str, str]:
        state = {"bytes": 0, "sha256": "", "md5": "", "err": None}

        class PutHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_PUT(self):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    h_sha256 = hashlib.sha256()
                    h_md5 = hashlib.md5()
                    total = 0
                    with open(dest_path, "wb") as fp:
                        if length > 0:
                            rem = length
                            while rem > 0:
                                chunk = self.rfile.read(min(131072, rem))
                                if not chunk:
                                    break
                                fp.write(chunk)
                                h_sha256.update(chunk)
                                h_md5.update(chunk)
                                total += len(chunk)
                                rem -= len(chunk)
                        else:
                            while True:
                                chunk = self.rfile.read(131072)
                                if not chunk:
                                    break
                                fp.write(chunk)
                                h_sha256.update(chunk)
                                h_md5.update(chunk)
                                total += len(chunk)
                    state["bytes"] = total
                    state["sha256"] = h_sha256.hexdigest()
                    state["md5"] = h_md5.hexdigest()
                    self.send_response(200)
                    self.end_headers()
                except Exception as exc:
                    state["err"] = exc
                    self.send_response(500)
                    self.end_headers()

        httpd = http.server.HTTPServer((local_ip, listen_port), PutHandler)
        httpd.timeout = 60.0
        bound_port = httpd.server_address[1]
        t = threading.Thread(target=httpd.handle_request, daemon=True)
        t.start()

        self.run_text(
            f"dd if=/dev/{dev} bs=131072 2>/dev/null | curl -s -X PUT --data-binary @- "
            f"-H 'Content-Length: {expected_size}' http://{local_ip}:{bound_port}/{dev}",
            check=False,
            timeout=900.0,
        )
        t.join(timeout=30.0)
        httpd.server_close()
        if state["err"]:
            raise RuntimeError(f"HTTP PUT receive failed for {dev}: {state['err']}")
        return state["bytes"], state["sha256"], state["md5"]


class SSHRunner:
    """Thin wrapper around system `ssh` using a single ControlMaster socket."""

    def __init__(self, host: str, user: str = "root", port: int = 22):
        self.host = host
        self.user = user
        self.port = port
        self.tmpdir = tempfile.mkdtemp(prefix="nr3053_ssh_")
        self.ctl_path = os.path.join(self.tmpdir, "ctl.sock")
        self._master_started = False

    def _base_opts(self) -> list[str]:
        return [
            "ssh",
            "-p",
            str(self.port),
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ControlPath={self.ctl_path}",
            "-o",
            "ControlMaster=auto",
            "-o",
            "ControlPersist=120",
        ]

    def target(self) -> str:
        return f"{self.user}@{self.host}"

    def connect(self) -> None:
        cmd = [
            "ssh",
            "-p",
            str(self.port),
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ControlPath={self.ctl_path}",
            "-o",
            "ControlMaster=yes",
            "-o",
            "ControlPersist=120",
            "-fN",
            self.target(),
        ]
        subprocess.check_call(cmd)
        self._master_started = True

    def close(self) -> None:
        if self._master_started:
            subprocess.run(
                ["ssh", "-o", f"ControlPath={self.ctl_path}", "-O", "exit", self.target()],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            self._master_started = False
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def run_text(self, remote_cmd: str, check: bool = True, timeout: float = 120.0) -> str:
        cmd = self._base_opts() + [self.target(), remote_cmd]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"SSH command failed ({proc.returncode}): {remote_cmd}\n{proc.stderr.strip()}"
            )
        return proc.stdout

    def stream_mtd_to_file(
        self,
        dev: str,
        dest_path: str,
        expected_size: int,
        local_ip: str = "",
        listen_port: int = 0,
        stream_method: str = "ssh",
    ) -> tuple[int, str, str]:
        cmd = self._base_opts() + [self.target(), f"dd if=/dev/{dev} bs=131072 2>/dev/null"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        h_sha256 = hashlib.sha256()
        h_md5 = hashlib.md5()
        total = 0
        assert proc.stdout is not None
        assert proc.stderr is not None
        with open(dest_path, "wb") as fp:
            while True:
                chunk = proc.stdout.read(131072)
                if not chunk:
                    break
                fp.write(chunk)
                h_sha256.update(chunk)
                h_md5.update(chunk)
                total += len(chunk)
        stderr_out = proc.stderr.read().decode("utf-8", errors="replace").strip()
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"Remote SSH stream failed (rc={rc}): {stderr_out}")
        return total, h_sha256.hexdigest(), h_md5.hexdigest()


def open_client(args):
    transport = args.transport
    port = args.port
    if transport == "auto":
        if port == 23:
            transport = "telnet"
        elif port == 22:
            transport = "ssh"
        elif tcp_port_open(args.host, 23):
            transport = "telnet"
            port = 23
        elif tcp_port_open(args.host, 22):
            transport = "ssh"
            port = 22
        else:
            raise RuntimeError(
                f"Neither Telnet (23) nor SSH (22) responded on {args.host}. "
                "Check --host and network connection."
            )
    elif port is None:
        port = 23 if transport == "telnet" else 22

    print(f"[*] Connecting via {transport.upper()} to {args.user}@{args.host}:{port} ...")
    if transport == "telnet":
        client = RawTelnet(args.host, args.user, port, password=args.password)
    else:
        client = SSHRunner(args.host, args.user, port)
    client.connect()
    return transport, port, client


def parse_proc_mtd(mtd_text: str) -> list[dict]:
    parts = []
    for line in mtd_text.splitlines():
        m = re.match(
            r'^(mtd\d+):\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+"([^"]+)"',
            line.strip(),
        )
        if m:
            dev, size_hex, erase_hex, name = m.groups()
            parts.append(
                {
                    "dev": dev,
                    "index": int(dev[3:]),
                    "size": int(size_hex, 16),
                    "erasesize": int(erase_hex, 16),
                    "name": name,
                }
            )
    return parts


def collect_router_info(client) -> str:
    """Run short individual commands (<160 chars each) so BusyBox PTY line buffers never stall."""
    sections = [
        ("id & uname", "id; uname -a"),
        ("device-tree model", "tr -d '\\0' < /proc/device-tree/model 2>/dev/null; echo"),
        ("release", "cat /etc/openwrt_release /etc/os-release 2>/dev/null"),
        ("/proc/mtd", "cat /proc/mtd"),
        (
            "sysfs mtd offsets & sizes",
            'for m in /sys/class/mtd/mtd[0-9]*; do [ -f "$m/name" ] && '
            'echo "${m##*/} name=$(cat $m/name) off=$(cat $m/offset 2>/dev/null) size=$(cat $m/size)"; done',
        ),
        (
            "sysfs mtd geometry & bad_blocks",
            'for m in /sys/class/mtd/mtd[0-9]*; do [ -f "$m/name" ] && '
            'echo "${m##*/} ws=$(cat $m/writesize) es=$(cat $m/erasesize) oob=$(cat $m/oobsize) '
            'ecc=$(cat $m/ecc_strength) bad=$(cat $m/bad_blocks)"; done',
        ),
        ("/proc/cmdline", "cat /proc/cmdline 2>/dev/null"),
        ("df -h", "df -h 2>/dev/null"),
        ("mount", "mount 2>/dev/null"),
        ("ubi", "ubinfo -a 2>/dev/null || ls -la /dev/ubi* 2>/dev/null"),
        ("tools", "for t in dd nc curl tftp scp mtd sha256sum md5sum fw_printenv; do which $t 2>/dev/null; done"),
        ("dhcp leases & neighbors", "cat /tmp/dhcp.leases 2>/dev/null; ip neigh 2>/dev/null"),
        ("dmesg (nand/mtd/nmbm/spi)", "dmesg 2>/dev/null | grep -iE 'spi|nand|dosilicon|nmbm|mtd|ubi|0x00000' | head -n 60"),
    ]
    out_parts = []
    for title, cmd in sections:
        res = client.run_text(cmd, check=False, timeout=15.0)
        out_parts.append(f"=== {title} ===\n{res}")
    return "\n\n".join(out_parts) + "\n"


def cmd_probe(args) -> None:
    transport, port, client = open_client(args)
    try:
        info = collect_router_info(client)
    finally:
        client.close()
    print(info)
    if args.out:
        Path(args.out).write_text(info, encoding="utf-8")
        print(f"[*] Saved probe output to {args.out}")


def cmd_backup(args) -> None:
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[*] Target backup directory: {out_dir}")
    transport, port, client = open_client(args)
    try:
        uid_out = client.run_text("id -u").strip()
        if uid_out != "0":
            raise RuntimeError(
                f"Remote user '{args.user}' has uid={uid_out} (not root). "
                "Reading /dev/mtd* requires root."
            )

        info_text = collect_router_info(client)
        (out_dir / "router_info.txt").write_text(info_text, encoding="utf-8")
        print("[*] Saved router_info.txt")

        local_ip = ""
        stream_method = "ssh"
        if transport == "telnet":
            local_ip = args.local_ip or detect_local_ip(args.host, port)
            has_nc = bool(client.run_text("which nc 2>/dev/null", check=False).strip())
            has_curl = bool(client.run_text("which curl 2>/dev/null", check=False).strip())
            if has_nc:
                stream_method = "nc"
            elif has_curl:
                stream_method = "curl"
            else:
                raise RuntimeError(
                    "Neither `nc` nor `curl` found on the router for Telnet streaming. "
                    "Check `router_info.txt` -> === tools === to see available transfer utilities."
                )
            print(f"[*] Telnet streaming via `{stream_method}` -> local host {local_ip}")

        mtd_text = client.run_text("cat /proc/mtd")
        parts = parse_proc_mtd(mtd_text)
        if not parts:
            raise RuntimeError("Could not parse /proc/mtd from router.")

        has_sha256 = bool(client.run_text("which sha256sum 2>/dev/null", check=False).strip())
        has_md5 = bool(client.run_text("which md5sum 2>/dev/null", check=False).strip())
        hash_tool = "sha256sum" if has_sha256 else ("md5sum" if has_md5 else None)
        print(f"[*] Remote verification tool: {hash_tool or 'size-only (no sha256sum/md5sum on router)'}")

        selected = []
        for p in parts:
            if p["size"] == 0:
                print(f"[-] Skipping {p['dev']} ({p['name']}): size is 0 bytes")
                continue
            if p["dev"] == "mtd0" and p["name"] == "spi0.0" and not args.include_full:
                print(
                    "[-] Skipping mtd0 (spi0.0, 256 MiB raw chip duplicate). "
                    "Pass --include-full to also dump mtd0."
                )
                continue
            if args.only and p["dev"] not in args.only and p["name"] not in args.only:
                continue
            selected.append(p)

        sha256_lines = []
        md5_lines = []
        for p in selected:
            dev = p["dev"]
            name = p["name"]
            expected = p["size"]
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
            filename = f"{dev}_{safe_name}.bin"
            dest = out_dir / filename
            mib = expected / (1024 * 1024)
            print(f"[*] Streaming /dev/{dev} ({name}, {mib:.2f} MiB / {expected:,} B) -> {filename} ...")

            got_bytes, local_sha256, local_md5 = client.stream_mtd_to_file(
                dev=dev,
                dest_path=str(dest),
                expected_size=expected,
                local_ip=local_ip,
                listen_port=args.listen_port,
                stream_method=stream_method,
            )
            if got_bytes != expected:
                raise RuntimeError(
                    f"Size mismatch for {dev} ({name}): got {got_bytes:,} B, expected {expected:,} B"
                )

            verified = "size-only"
            if hash_tool == "sha256sum":
                remote_hash = client.run_text(
                    f"dd if=/dev/{dev} bs=131072 2>/dev/null | sha256sum", timeout=300.0
                ).split()[0]
                if remote_hash != local_sha256:
                    raise RuntimeError(
                        f"SHA-256 mismatch for {dev}: local={local_sha256} remote={remote_hash}"
                    )
                verified = "SHA-256 verified"
            elif hash_tool == "md5sum":
                remote_hash = client.run_text(
                    f"dd if=/dev/{dev} bs=131072 2>/dev/null | md5sum", timeout=300.0
                ).split()[0]
                if remote_hash != local_md5:
                    raise RuntimeError(
                        f"MD5 mismatch for {dev}: local={local_md5} remote={remote_hash}"
                    )
                verified = "MD5 verified"

            print(f"    OK  {got_bytes:>11,} B  sha256={local_sha256[:16]}... ({verified})")
            sha256_lines.append(f"{local_sha256}  {filename}")
            md5_lines.append(f"{local_md5}  {filename}")

        (out_dir / "sha256sums.txt").write_text("\n".join(sha256_lines) + "\n", encoding="utf-8")
        (out_dir / "md5sums.txt").write_text("\n".join(md5_lines) + "\n", encoding="utf-8")
        print(f"[*] Wrote sha256sums.txt and md5sums.txt ({len(sha256_lines)} files)")
    finally:
        client.close()

    if args.sevenzip:
        if not shutil.which("7z"):
            raise RuntimeError("`7z` not found in PATH. Install p7zip-full / 7zip first.")
        archive_path = str(out_dir) + ".7z"
        print(f"[*] Compressing backup to {archive_path} ...")
        subprocess.check_call(["7z", "a", "-t7z", "-mx=9", archive_path, "."], cwd=str(out_dir))
        print(f"[*] Created {archive_path}")

    print("\n[*] Next step: inspect sizes & contents (redacted for docs) with:")
    print(f"    python3 scripts/nr3053_helper.py inspect-backup {out_dir}")


def _last_non_ff_index(data: bytes) -> int:
    """Return 1-based length up to the last byte != 0xFF (0 if all 0xFF)."""
    i = len(data)
    chunk = 65536
    ff_block = b"\xff" * chunk
    while i >= chunk and data[i - chunk : i] == ff_block:
        i -= chunk
    while i > 0 and data[i - 1] == 0xFF:
        i -= 1
    return i


def _non_ff_count(data: bytes) -> int:
    return len(data) - data.count(0xFF)


def _extract_uboot_banner(data: bytes) -> str | None:
    m = re.search(rb"U-Boot\s+[0-9]{4}\.[0-9]{2}[^\x00\r\n]{0,80}", data)
    return m.group(0).decode("ascii", errors="replace").strip() if m else None


def _parse_uboot_env(data: bytes) -> tuple[bool | None, list[str]]:
    if len(data) < 8:
        return None, []
    stored_crc = struct.unpack_from("<I", data, 0)[0]
    if stored_crc == 0xFFFFFFFF:
        return None, []
    crc_ok = None
    for env_size in (0x10000, 0x20000, 0x40000, len(data)):
        if env_size <= len(data):
            calc = zlib.crc32(data[4:env_size]) & 0xFFFFFFFF
            if calc == stored_crc:
                crc_ok = True
                break
    if crc_ok is None:
        crc_ok = False
    entries = []
    for raw in data[4:65536].split(b"\x00"):
        if not raw or raw.startswith(b"\xff"):
            break
        s = raw.decode("ascii", errors="ignore")
        if "=" not in s:
            continue
        k, _, _ = s.partition("=")
        if any(tok in k.lower() for tok in SENSITIVE_ENV_KEYS):
            entries.append(f"{k}=<REDACTED>")
        else:
            entries.append(s)
    return crc_ok, entries


def _detect_content(name: str, data: bytes) -> str:
    notes = []
    span = _last_non_ff_index(data)
    if span == 0:
        return "Empty (all 0xFF)"

    if b"MMM\x01" in data[:4096] or b"BRLYT" in data[:4096] or b"BOOTLOADER!" in data[:4096]:
        notes.append("MediaTek BL2/BRLYT header")

    fip_off = data.find(b"\x01\x00\x64\xaa")
    if 0 <= fip_off < 4096:
        notes.append(f"TF-A FIP TOC @0x{fip_off:x}")
    banner = _extract_uboot_banner(data)
    if banner:
        notes.append(banner)

    if "factory" in name.lower():
        magic_le = struct.unpack_from("<H", data, 0)[0]
        notes.append(f"EEPROM magic=0x{magic_le:04x}")

    if "env" in name.lower():
        crc_ok, entries = _parse_uboot_env(data)
        if entries:
            notes.append(f"U-Boot env ({len(entries)} vars, crc_ok={crc_ok})")

    if data[:4] == b"\xd0\x0d\xfe\xed":
        fdt_size = struct.unpack_from(">I", data, 4)[0]
        notes.append(f"FIT/FDT image (hdr_size={fdt_size:,} B)")
    if data[:4] == b"UBI#":
        ec_count = sum(1 for off in range(0, len(data), 131072) if data[off : off + 4] == b"UBI#")
        notes.append(f"UBI container ({ec_count} PEBs with UBI# header)")
    sq_off = data.find(b"hsqs")
    if sq_off != -1:
        notes.append(f"SquashFS @0x{sq_off:x}")
    if data[:2] == b"\x85\x19":
        notes.append("JFFS2 filesystem")
    if b"\x31\x18\x10\x06" in data[:262144]:
        notes.append("UBIFS nodes present")

    if len(data) >= 32 * 1024 * 1024 and b"NMBM" in data[-16 * 1024 * 1024 :]:
        nmbm_off = data.find(b"NMBM", len(data) - 16 * 1024 * 1024)
        notes.append(f"NMBM table @0x{nmbm_off:08x}")

    return "; ".join(notes) if notes else f"Binary data (head={data[:8].hex()})"


def cmd_inspect_backup(args) -> None:
    target = Path(args.path).resolve()
    if not target.exists():
        sys.exit(f"Path not found: {target}")

    tmpdir = None
    try:
        if target.is_dir():
            work_dir = target
        elif target.suffix.lower() == ".7z":
            if not shutil.which("7z"):
                sys.exit("`7z` is required to inspect a .7z archive.")
            tmpdir = tempfile.mkdtemp(prefix="nr3053_insp_")
            subprocess.check_call(["7z", "x", "-y", f"-o{tmpdir}", str(target)], stdout=subprocess.DEVNULL)
            work_dir = Path(tmpdir)
        else:
            sys.exit(f"Expected a backup directory or .7z archive, got: {target}")

        bins = sorted(
            work_dir.glob("mtd*.bin"),
            key=lambda p: int(re.match(r"mtd(\d+)", p.name).group(1))
            if re.match(r"mtd(\d+)", p.name)
            else 999,
        )
        if not bins:
            sys.exit(f"No mtd*.bin files found in {work_dir}")

        results = {}
        print(f"=== Offline MTD Backup Inspection: {target} ===\n")
        print(
            f"{'File':<30} {'Size':>10} {'Non-FF':>10} {'Span':>10} {'Fill%':>6}  Content / Signature"
        )
        print("-" * 110)

        for bf in bins:
            data = bf.read_bytes()
            size = len(data)
            non_ff = _non_ff_count(data)
            span = _last_non_ff_index(data)
            fill_pct = (non_ff / size * 100.0) if size else 0.0
            sha = hashlib.sha256(data).hexdigest()
            desc = _detect_content(bf.name, data)
            results[bf.name] = {
                "file": bf.name,
                "size_bytes": size,
                "size_kib": round(size / 1024, 2),
                "non_ff_bytes": non_ff,
                "used_kib": round(non_ff / 1024, 1),
                "span_bytes": span,
                "span_kib": round(span / 1024, 1),
                "fill_pct": round(fill_pct, 2),
                "sha256": sha,
                "summary": desc,
            }
            print(
                f"{bf.name:<30} {size/1024:>8.0f} K {non_ff/1024:>8.1f} K {span/1024:>8.1f} K {fill_pct:>5.1f}%  {desc}"
            )

        print("\n=== Redundant Partition Comparisons ===")
        pairs = [
            ("mtd3_Factory.bin", "mtd9_Factory_backup.bin"),
            ("mtd4_FIP.bin", "mtd10_fip2.bin"),
            ("mtd5_firmware.bin", "mtd6_firmware2.bin"),
            ("mtd7_rootfs_data.bin", "mtd8_rootfs_data_backup.bin"),
        ]
        for a, b in pairs:
            if a in results and b in results:
                same = results[a]["sha256"] == results[b]["sha256"]
                status = "IDENTICAL (same SHA-256)" if same else "DIFFERENT"
                print(f"  {a:<26} vs {b:<28}: {status}")

        env_file = work_dir / "mtd2_u-boot-env.bin"
        if env_file.is_file():
            crc_ok, entries = _parse_uboot_env(env_file.read_bytes())
            if entries:
                print(f"\n=== Redacted u-boot-env (crc_ok={crc_ok}) ===")
                for e in entries:
                    print(f"  {e}")

        if args.json:
            Path(args.json).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
            print(f"\n[*] Wrote JSON report to {args.json}")
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def add_conn_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--host", default="10.1.1.1", help="Router IP (default: 10.1.1.1)")
    p.add_argument("--user", default="root", help="Login username (default: root)")
    p.add_argument(
        "--transport",
        choices=["auto", "telnet", "ssh"],
        default="auto",
        help="Connection transport (default: auto — prefers Telnet:23, falls back to SSH:22)",
    )
    p.add_argument("--port", type=int, default=None, help="Port override (default: 23 for telnet, 22 for ssh)")
    p.add_argument("--password", default=None, help="Optional Telnet password (prompted if needed)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=f"Viettel SDMC NR3053 Linux helper v{__version__} (read-only Telnet/SSH probe, backup & inspection)"
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_probe = sub.add_parser("probe", help="Read-only probe of MTD layout, NAND geometry, DHCP leases, and OS info")
    add_conn_args(p_probe)
    p_probe.add_argument("--out", help="Optional path to save probe output")
    p_probe.set_defaults(func=cmd_probe)

    p_bak = sub.add_parser("backup", help="Stream all MTD partitions over Telnet (nc/curl) or SSH and verify checksums")
    add_conn_args(p_bak)
    p_bak.add_argument("--local-ip", default=None, help="Local Linux host IP for Telnet streaming (auto-detected if omitted)")
    p_bak.add_argument("--listen-port", type=int, default=0, help="Fixed local TCP port for Telnet streaming (default: ephemeral)")
    p_bak.add_argument("--out", default="./backups/nr3053-stock", help="Output directory (default: ./backups/nr3053-stock)")
    p_bak.add_argument(
        "--include-full",
        action="store_true",
        help="Also dump mtd0 (full 256 MiB raw spi0.0 chip including 16 MiB NMBM tail)",
    )
    p_bak.add_argument(
        "--only",
        nargs="+",
        help="Optional subset of devices or names to dump (e.g. --only mtd1 mtd2 mtd3 mtd4)",
    )
    p_bak.add_argument("--7z", dest="sevenzip", action="store_true", help="Also create a .7z archive")
    p_bak.set_defaults(func=cmd_backup)

    p_insp = sub.add_parser("inspect-backup", help="Offline inspection of a backup directory or .7z archive")
    p_insp.add_argument("path", help="Backup directory or .7z archive path")
    p_insp.add_argument("--json", help="Optional path to write JSON analysis summary")
    p_insp.set_defaults(func=cmd_inspect_backup)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
