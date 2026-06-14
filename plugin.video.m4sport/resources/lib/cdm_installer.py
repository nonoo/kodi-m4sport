#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Self-contained Widevine CDM installer for Kodi.

Covers:
  - Linux x86_64 / x86      → Google Chrome component update API (CRX3, ~21 MB)
  - Windows x64 / x86       → same
  - macOS x86_64 / arm64    → same
  - Linux arm / arm64        → ChromeOS recovery image extraction (~600 MB–2 GB)
  - Android                  → CDM is built-in; nothing to do

Installation target: special://home/cdm/   (inputstream.adaptive default path)
"""

import io
import json
import os
import platform
import struct
import sys
import tempfile
import zipfile
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Kodi imports are mocked when this module is imported outside Kodi (tests).
# ---------------------------------------------------------------------------
try:
    import xbmc
    import xbmcgui
    import xbmcvfs
    _IN_KODI = True
except ImportError:
    _IN_KODI = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CRX_APP_ID = "oimompecagnajdejgnnjijobebaeigek"
_CRX_UPDATE_URL = "https://update.googleapis.com/service/update2/json"
_CDM_FILENAME = {
    "linux":   "libwidevinecdm.so",
    "windows": "widevinecdm.dll",
    "darwin":  "libwidevinecdm.dylib",
    "android": "libwidevinecdm.so",
}
_CDM_INSTALL_DIR = "special://home/cdm/"

# ChromeOS recovery image – board names that ship an ARM Widevine CDM.
# Prefer smaller/faster images at the front of the list.
_CHROMEOS_ARM_BOARDS   = ["scarlet", "bob", "nicky", "oak"]   # 32-bit ARM
_CHROMEOS_ARM64_BOARDS = ["kevin", "bob", "gru"]               # 64-bit ARM

# Path of libwidevinecdm.so inside the ChromeOS ext2 ROOT-A filesystem.
_CHROMEOS_CDM_PATHS = [
    "opt/google/chrome/WidevineCdm/_platform_specific/cros_arm64/libwidevinecdm.so",
    "opt/google/chrome/WidevineCdm/_platform_specific/cros_arm/libwidevinecdm.so",
    "opt/google/chrome/WidevineCdm/libwidevinecdm.so",
]

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_CHUNK_SIZE = 1024 * 256   # 256 KB per progress-update chunk


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def detect_platform():
    """Return (os_name, arch) tuple with normalised names.

    os_name : 'linux' | 'windows' | 'darwin' | 'android'
    arch    : 'x64' | 'x86' | 'arm' | 'arm64'
    """
    system = platform.system().lower()

    # Android detection (Kodi running under Android via Python for Android)
    if "ANDROID_ROOT" in os.environ or os.path.isdir("/system/app"):
        return "android", "arm64"

    if system == "windows":
        os_name = "windows"
    elif system == "darwin":
        os_name = "darwin"
    else:
        os_name = "linux"

    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("i386", "i686", "x86"):
        arch = "x86"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    elif machine.startswith("arm"):
        arch = "arm"
    else:
        arch = "x64"   # safe default for unknown

    return os_name, arch


def cdm_install_path():
    """Absolute path to the Kodi CDM directory."""
    if _IN_KODI:
        return xbmcvfs.translatePath(_CDM_INSTALL_DIR)
    return os.path.join(tempfile.gettempdir(), "kodi_cdm")


def is_widevine_installed():
    """Return True if the CDM shared library already exists in the CDM dir."""
    os_name, _ = detect_platform()
    filename = _CDM_FILENAME.get(os_name, "libwidevinecdm.so")
    cdm_path = os.path.join(cdm_install_path(), filename)
    return os.path.isfile(cdm_path)


# ---------------------------------------------------------------------------
# Progress / dialog helpers
# ---------------------------------------------------------------------------

class _Progress:
    """Thin wrapper around xbmcgui.DialogProgress (or no-op outside Kodi)."""

    def __init__(self, title, message=""):
        self._dp = None
        if _IN_KODI:
            self._dp = xbmcgui.DialogProgress()
            self._dp.create(title, message)

    def update(self, pct, message=""):
        if self._dp:
            self._dp.update(pct, message)

    def close(self):
        if self._dp:
            self._dp.close()

    def is_cancelled(self):
        return bool(self._dp and self._dp.iscanceled())


def _notify_error(message):
    if _IN_KODI:
        xbmc.log(f"[cdm_installer] ERROR: {message}", xbmc.LOGERROR)
        xbmcgui.Dialog().ok("Widevine CDM", message)
    else:
        print(f"ERROR: {message}", file=sys.stderr)


def _log(message):
    if _IN_KODI:
        xbmc.log(f"[cdm_installer] {message}", xbmc.LOGINFO)
    else:
        print(message)


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _atomic_write(dest, data):
    """Write *data* to *dest* atomically: write to a sibling .tmp then rename.

    Prevents a truncated file being left behind if the process is killed
    mid-write, which would cause is_widevine_installed() to return True while
    dlopen() silently fails.
    """
    tmp_path = dest + ".tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(data)
        os.chmod(tmp_path, 0o755)
        os.replace(tmp_path, dest)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _http_get_json(url, post_data=None, content_type="application/json"):
    headers = {"User-Agent": _USER_AGENT}
    if post_data is not None:
        if isinstance(post_data, str):
            post_data = post_data.encode()
        headers["Content-Type"] = content_type
    req = Request(url, data=post_data, headers=headers)
    with urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace").lstrip(")]}'\n"))


def _download_with_progress(url, progress, label, total_size=0):
    """Download *url* and return bytes; update *progress* as data arrives."""
    headers = {"User-Agent": _USER_AGENT}
    req = Request(url, headers=headers)
    data = io.BytesIO()
    received = 0
    with urlopen(req, timeout=120) as r:
        if not total_size:
            content_len = r.getheader("Content-Length")
            total_size = int(content_len) if content_len else 0
        while True:
            if progress.is_cancelled():
                return None
            chunk = r.read(_CHUNK_SIZE)
            if not chunk:
                break
            data.write(chunk)
            received += len(chunk)
            if total_size:
                pct = min(99, int(received * 100 / total_size))
                mb_done = received / 1024 / 1024
                mb_total = total_size / 1024 / 1024
                progress.update(pct, f"{label}: {mb_done:.1f} / {mb_total:.1f} MB")
            else:
                mb_done = received / 1024 / 1024
                progress.update(0, f"{label}: {mb_done:.1f} MB")
    return data.getvalue()


# ---------------------------------------------------------------------------
# CRX3 / Google component update path  (x86 / x64 / macOS / Windows)
# ---------------------------------------------------------------------------

_CRX_OS_MAP = {"linux": "Linux", "windows": "win", "darwin": "mac", "android": "Linux"}


def _get_crx_download_url(os_name, arch):
    crx_os   = _CRX_OS_MAP.get(os_name, "Linux")
    crx_arch = arch if arch in ("x64", "x86", "arm64", "arm") else "x64"
    payload  = json.dumps({
        "request": {
            "@os": "", "@updater": "",
            "acceptformat": "crx3,download,puff,run,xz,zucc",
            "apps": [{
                "appid":         _CRX_APP_ID,
                "installsource": "ondemand",
                "updatecheck":   {},
                "version":       "1.4.9.1088",
            }],
            "dedup":          "cr",
            "ismachine":      False,
            "arch":           crx_arch,
            "os":             {"arch": crx_arch, "platform": crx_os},
            "protocol":       "4.0",
            "updaterversion": "142.0.7444.175",
        }
    })
    resp = _http_get_json(_CRX_UPDATE_URL, post_data=payload)
    uc   = resp["response"]["apps"][0]["updatecheck"]
    if uc.get("status") in ("noupdate", "error-unknownApplication"):
        return None, 0
    ops  = uc["pipelines"][0]["operations"][0]
    size = ops.get("size", 0)
    # Prefer HTTPS URLs (index 1 if available, fallback to 0)
    urls = ops["urls"]
    url  = next((u["url"] for u in urls if u["url"].startswith("https")), urls[0]["url"])
    return url, size


def _extract_cdm_from_crx3(crx_data, os_name):
    """Strip CRX3 header and extract the CDM .so/.dll/.dylib from the ZIP."""
    if crx_data[:4] != b"Cr24":
        raise ValueError("Not a CRX3 file")
    header_size = struct.unpack_from("<I", crx_data, 8)[0]
    zip_offset  = 12 + header_size
    cdm_name = _CDM_FILENAME.get(os_name, "libwidevinecdm.so")
    with zipfile.ZipFile(io.BytesIO(crx_data[zip_offset:])) as zf:
        for name in zf.namelist():
            if name.endswith(cdm_name):
                return cdm_name, zf.read(name)
    raise ValueError(f"CDM file '{cdm_name}' not found in CRX3 package")


def _install_via_crx(os_name, arch):
    """Download CDM via Google component update API and install it."""
    progress = _Progress("Widevine CDM", "Querying Google update server…")
    try:
        progress.update(1, "Querying Google update server…")
        url, size = _get_crx_download_url(os_name, arch)
        if not url:
            _notify_error(
                "Google's CDM update server returned 'no update' for this platform.\n"
                "Your platform may not support automatic Widevine installation."
            )
            return False

        _log(f"CRX3 download URL: {url}  size: {size}")
        progress.update(2, "Starting download…")
        crx_data = _download_with_progress(url, progress, "Downloading Widevine CDM", size)
        if crx_data is None:
            return False   # cancelled

        progress.update(97, "Extracting CDM…")
        filename, cdm_bytes = _extract_cdm_from_crx3(crx_data, os_name)

        install_dir = cdm_install_path()
        os.makedirs(install_dir, exist_ok=True)
        dest = os.path.join(install_dir, filename)
        _atomic_write(dest, cdm_bytes)

        _log(f"CDM installed: {dest} ({len(cdm_bytes):,} bytes)")
        progress.update(100, "Done!")
        return True

    except Exception as exc:
        _notify_error(f"CDM installation failed:\n{exc}")
        return False
    finally:
        progress.close()


# ---------------------------------------------------------------------------
# ChromeOS recovery image path  (ARM / ARM64 on Linux)
# ---------------------------------------------------------------------------

_RECOVERY_JSON_URL = (
    "https://dl.google.com/dl/edgedl/chromeos/recovery/recovery.json"
)


def _find_recovery_entry(recovery_list, board_names):
    """Return the recovery entry dict for the first matching board name."""
    for board in board_names:
        for entry in recovery_list:
            if entry.get("hwidmatch", "").lower().startswith(board):
                return entry
            if entry.get("name", "").lower() == board:
                return entry
    return None


# --- minimal GPT / ext2 parser -------------------------------------------

def _parse_gpt_for_root_a(disk_data):
    """Return (byte_offset, byte_length) of the ROOT-A GPT partition."""
    # GPT header at LBA 1 (offset 512)
    hdr_off = 512
    sig = disk_data[hdr_off:hdr_off + 8]
    if sig != b"EFI PART":
        raise ValueError("GPT signature not found")
    part_entry_start = struct.unpack_from("<Q", disk_data, hdr_off + 72)[0]
    num_entries       = struct.unpack_from("<I", disk_data, hdr_off + 80)[0]
    entry_size        = struct.unpack_from("<I", disk_data, hdr_off + 84)[0]

    ROOT_A_NAME = "ROOT-A".encode("utf-16-le")
    for i in range(num_entries):
        off = part_entry_start * 512 + i * entry_size
        entry = disk_data[off:off + entry_size]
        if len(entry) < entry_size:
            break
        name_bytes = entry[56:56 + 72].rstrip(b"\x00")
        if name_bytes == ROOT_A_NAME:
            start_lba  = struct.unpack_from("<Q", entry, 32)[0]
            end_lba    = struct.unpack_from("<Q", entry, 40)[0]
            byte_off   = start_lba * 512
            byte_len   = (end_lba - start_lba + 1) * 512
            return byte_off, byte_len
    raise ValueError("ROOT-A partition not found in GPT")


class _Ext2:
    """Minimal read-only ext2/ext4 filesystem reader (pure Python)."""

    def __init__(self, data):
        self._d = data
        sb = self._d[1024:2048]
        self.block_size     = 1024 << struct.unpack_from("<I", sb, 0x18)[0]
        self.inode_size     = struct.unpack_from("<H", sb, 0x58)[0] or 128
        self.inodes_per_grp = struct.unpack_from("<I", sb, 0x28)[0]
        self.has_64bit      = bool(struct.unpack_from("<H", sb, 0x60)[0] & 0x0002 and
                                   struct.unpack_from("<H", sb, 0x5C)[0] >= 4)
        # block group descriptor table starts at the block after the superblock
        bgdt_block = 1 if self.block_size > 1024 else 2
        self._bgdt_off = bgdt_block * self.block_size
        self._bgdt_size = 64 if self.has_64bit else 32

    def _block(self, n):
        off = n * self.block_size
        return self._d[off:off + self.block_size]

    def _inode(self, num):
        grp   = (num - 1) // self.inodes_per_grp
        idx   = (num - 1) %  self.inodes_per_grp
        bgd   = self._d[self._bgdt_off + grp * self._bgdt_size:
                        self._bgdt_off + grp * self._bgdt_size + self._bgdt_size]
        it_lo = struct.unpack_from("<I", bgd, 8)[0]
        it_hi = struct.unpack_from("<I", bgd, 40)[0] if self.has_64bit else 0
        it    = (it_hi << 32 | it_lo) * self.block_size
        off   = it + idx * self.inode_size
        return self._d[off:off + self.inode_size]

    def _read_blocks(self, inode_data, size):
        """Read file data via direct, single-indirect, and double-indirect blocks."""
        buf = io.BytesIO()
        block_ids = struct.unpack_from("<15I", inode_data, 40)  # i_block[0..14]
        remaining = size
        # direct blocks
        for bid in block_ids[:12]:
            if not bid or remaining <= 0:
                break
            blk_data = self._block(bid)
            chunk = min(self.block_size, remaining)
            buf.write(blk_data[:chunk])
            remaining -= chunk
        # single indirect
        if remaining > 0 and block_ids[12]:
            indirect = self._block(block_ids[12])
            refs = struct.unpack_from(f"<{self.block_size // 4}I", indirect)
            for bid in refs:
                if not bid or remaining <= 0:
                    break
                blk_data = self._block(bid)
                chunk = min(self.block_size, remaining)
                buf.write(blk_data[:chunk])
                remaining -= chunk
        # double indirect (needed for ~20 MB CDM when block_size == 1024)
        if remaining > 0 and block_ids[13]:
            dbl = self._block(block_ids[13])
            l1  = struct.unpack_from(f"<{self.block_size // 4}I", dbl)
            for l1_bid in l1:
                if not l1_bid or remaining <= 0:
                    break
                indirect = self._block(l1_bid)
                refs = struct.unpack_from(f"<{self.block_size // 4}I", indirect)
                for bid in refs:
                    if not bid or remaining <= 0:
                        break
                    blk_data = self._block(bid)
                    chunk = min(self.block_size, remaining)
                    buf.write(blk_data[:chunk])
                    remaining -= chunk
        return buf.getvalue()

    def _list_dir(self, dir_inode_data):
        """Yield (name, child_inode_num) pairs from a directory inode."""
        size = struct.unpack_from("<I", dir_inode_data, 4)[0]
        raw  = self._read_blocks(dir_inode_data, size)
        off  = 0
        while off < len(raw):
            if off + 8 > len(raw):
                break
            child_ino  = struct.unpack_from("<I", raw, off)[0]
            rec_len    = struct.unpack_from("<H", raw, off + 4)[0]
            name_len   = raw[off + 6]
            if rec_len == 0:
                break
            if child_ino:
                name = raw[off + 8:off + 8 + name_len].decode("utf-8", "replace")
                yield name, child_ino
            off += rec_len

    def find_file(self, path):
        """Traverse *path* (e.g. 'opt/google/chrome/libwidevinecdm.so') from root.
        Return file bytes or None if not found."""
        parts   = [p for p in path.split("/") if p]
        cur_ino = 2  # root inode
        for part in parts:
            cur_data = self._inode(cur_ino)
            found    = False
            for name, child_ino in self._list_dir(cur_data):
                if name == part:
                    cur_ino = child_ino
                    found   = True
                    break
            if not found:
                return None
        # cur_ino is the file inode
        file_inode = self._inode(cur_ino)
        size       = struct.unpack_from("<I", file_inode, 4)[0]
        return self._read_blocks(file_inode, size)


def _install_via_chromeos(arch):
    """Download a ChromeOS recovery image and extract the Widevine CDM."""
    progress = _Progress("Widevine CDM", "Fetching ChromeOS recovery list…")
    try:
        progress.update(1, "Fetching ChromeOS recovery list…")
        recovery_list = _http_get_json(_RECOVERY_JSON_URL)

        board_names = _CHROMEOS_ARM64_BOARDS if arch == "arm64" else _CHROMEOS_ARM_BOARDS
        entry = _find_recovery_entry(recovery_list, board_names)
        if not entry:
            _notify_error(
                "Could not find a ChromeOS recovery image for this ARM device.\n"
                "Please install Widevine CDM manually."
            )
            return False

        image_url  = entry["url"]
        image_size = int(entry.get("filesize", 0))
        board_name = entry.get("name", "unknown")
        _log(f"Using ChromeOS board: {board_name}  URL: {image_url}  size: {image_size}")
        progress.update(2, "Downloading ChromeOS recovery image…")
        zip_data = _download_with_progress(
            image_url, progress, "Downloading ChromeOS image", image_size
        )
        if zip_data is None:
            return False

        progress.update(90, "Extracting disk image from ZIP…")
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            bin_names = [n for n in zf.namelist() if n.endswith(".bin")]
            if not bin_names:
                raise ValueError("No .bin file found in ChromeOS recovery ZIP")
            disk_data = zf.read(bin_names[0])

        progress.update(93, "Parsing partition table…")
        part_off, _part_len = _parse_gpt_for_root_a(disk_data)
        ext2_data = disk_data[part_off:]

        progress.update(95, "Searching for libwidevinecdm.so…")
        fs  = _Ext2(ext2_data)
        cdm = None
        for path in _CHROMEOS_CDM_PATHS:
            cdm = fs.find_file(path)
            if cdm:
                _log(f"Found CDM at: {path} ({len(cdm):,} bytes)")
                break

        if not cdm:
            raise ValueError(
                "libwidevinecdm.so not found in ChromeOS ROOT-A partition.\n"
                "The recovery image may not contain a Widevine CDM."
            )

        progress.update(98, "Installing CDM…")
        install_dir = cdm_install_path()
        os.makedirs(install_dir, exist_ok=True)
        dest = os.path.join(install_dir, "libwidevinecdm.so")
        _atomic_write(dest, cdm)

        _log(f"CDM installed: {dest}")
        progress.update(100, "Done!")
        return True

    except Exception as exc:
        _notify_error(f"ChromeOS CDM extraction failed:\n{exc}")
        return False
    finally:
        progress.close()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def ensure_widevine_cdm():
    """Check for the Widevine CDM and install it if missing.

    Returns True if the CDM is present (already installed or just installed),
    False if installation failed or was cancelled.
    """
    if is_widevine_installed():
        _log("Widevine CDM already installed.")
        return True

    os_name, arch = detect_platform()
    _log(f"Detected platform: os={os_name}  arch={arch}")

    if os_name == "android":
        # Android always has Widevine built in — nothing to install.
        _log("Android platform: Widevine is built in.")
        return True

    if os_name == "linux" and arch in ("arm", "arm64"):
        # ARM needs a ChromeOS recovery image (600 MB – 2 GB); warn about size.
        if _IN_KODI:
            ok = xbmcgui.Dialog().yesno(
                "Widevine CDM required",
                "M4 Sport uses Widevine DRM. Your device requires a ChromeOS recovery "
                "image to extract the Widevine CDM (download: 600 MB – 2 GB).\n\nProceed?",
            )
            if not ok:
                return False
        return _install_via_chromeos(arch)

    # Ask user permission before downloading anything (~20 MB).
    if _IN_KODI:
        ok = xbmcgui.Dialog().yesno(
            "Widevine CDM required",
            "M4 Sport uses Widevine DRM.\n"
            "The Widevine CDM (~20 MB) needs to be downloaded and installed.\n\nProceed?",
        )
        if not ok:
            return False

    return _install_via_crx(os_name, arch)
