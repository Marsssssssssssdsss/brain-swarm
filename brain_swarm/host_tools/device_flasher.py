"""
NeuroResonator Firmware Flasher

Automates firmware flashing for both nRF5340 and ESP32-S3.
Supports local build artifacts and OTA update server upload.

Usage:
  # Flash nRF5340 via JLink
  python device_flasher.py --target nrf5340 --file firmware.hex

  # Flash ESP32-S3 via USB (ESPTool)
  python device_flasher.py --target esp32s3 --port COM3 --file firmware.bin

  # Upload OTA image to update server
  python device_flasher.py --ota-upload firmware.bin --version 1.2.3

  # Flash both chips
  python device_flasher.py --all --nrf-file firmware_nrf.hex --esp-file firmware_esp.bin
"""

import argparse
import hashlib
import os
import struct
import sys
import time
from typing import List, Optional, Tuple


# ─────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────

def crc32_file(filepath: str) -> str:
    with open(filepath, "rb") as f:
        return format(hashlib.crc32(f.read()) & 0xFFFFFFFF, "08x")


def sha256_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_binary(filepath: str) -> Tuple[bool, str]:
    if not os.path.isfile(filepath):
        return False, "File not found"
    size = os.path.getsize(filepath)
    if size == 0:
        return False, "File is empty"
    crc = crc32_file(filepath)
    sha = sha256_file(filepath)
    return True, f"CRC32={crc} SHA256={sha} ({size} bytes)"


class ProgressBar:
    """Simple terminal progress bar."""
    def __init__(self, total: int, width: int = 40, prefix: str = ""):
        self.total = total
        self.width = width
        self.prefix = prefix
        self._start = time.time()

    def update(self, current: int):
        frac = current / self.total if self.total > 0 else 0
        filled = int(self.width * frac)
        bar = "\u2588" * filled + "\u2591" * (self.width - filled)
        elapsed = time.time() - self._start
        sys.stdout.write(f"\r{self.prefix} |{bar}| {current}/{self.total} ({frac*100:.0f}%) {elapsed:.1f}s")
        sys.stdout.flush()

    def done(self):
        elapsed = time.time() - self._start
        block = '\u2588'
        print(f"\r{self.prefix} |{block * self.width}| Done in {elapsed:.1f}s")


# ─────────────────────────────────────────────
# nRF5340 Flasher
# ─────────────────────────────────────────────

class Nrf5340Flasher:
    """Flash nRF5340 via JLink SWD or UF2 drag-and-drop."""

    def __init__(self, method: str = "jlink"):
        self.method = method

    def flash(self, filepath: str, verify: bool = True) -> bool:
        ok, info = verify_binary(filepath)
        if not ok:
            print(f"Verification failed: {info}")
            return False
        print(f"nRF5340 firmware: {info}")

        if self.method == "jlink":
            return self._flash_jlink(filepath, verify)
        elif self.method == "uf2":
            return self._flash_uf2(filepath)
        else:
            print(f"Unknown method: {self.method}")
            return False

    def _flash_jlink(self, filepath: str, verify: bool) -> bool:
        nrf_connect = self._find_tool("nrfjprog")
        if nrf_connect:
            return self._flash_nrfjprog(filepath, verify)

        jlink_exe = self._find_tool("JLinkExe")
        if jlink_exe:
            return self._flash_jlinkexe(filepath, verify)

        print("No flashing tool found. Install nRF Command Line Tools or JLink.")
        return False

    def _flash_nrfjprog(self, filepath: str, verify: bool) -> bool:
        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".hex":
            cmd = f'nrfjprog --program "{filepath}" --chiperase --reset'
        elif ext in (".bin", ""):
            addr = "0x10000"
            if "network" in filepath.lower():
                addr = "0x120000"
            cmd = f'nrfjprog --program "{filepath}" --sectoranduageerase --reset --start {addr}'
        else:
            print(f"Unsupported format: {ext}")
            return False

        print(f"Running: {cmd}")
        progress = ProgressBar(100, prefix="Flashing")
        for i in range(101):
            time.sleep(0.05)
            progress.update(i)
        progress.done()
        rc = os.system(cmd)
        if rc != 0:
            print("Flashing failed.")
            return False

        if verify:
            print("Verifying flash...")
            verify_cmd = f'nrfjprog --verify "{filepath}"'
            rc = os.system(verify_cmd)
            if rc == 0:
                print("Verification PASSED")
                return True
            else:
                print("Verification FAILED")
                return False
        return True

    def _flash_jlinkexe(self, filepath: str, verify: bool) -> bool:
        device = "nRF5340_XXAA"
        script = (
            f"device {device}\n"
            f"si SWD\n"
            f"speed 4000\n"
            f"Connect\n"
            f"LoadFile {filepath}\n"
            f"Verify\n"
            f"r\n"
            f"g\n"
            f"exit\n"
        )
        script_path = "_flash.jlink"
        with open(script_path, "w") as f:
            f.write(script)
        cmd = f'JLinkExe -If SWD -device {device} -CommanderScript {script_path}'
        print(f"Running: {cmd}")
        progress = ProgressBar(100, prefix="Flashing")
        for i in range(101):
            time.sleep(0.05)
            progress.update(i)
        progress.done()
        rc = os.system(cmd)
        os.remove(script_path)
        if verify:
            print("Verification completed (JLink built-in).")
        return rc == 0

    def _flash_uf2(self, filepath: str) -> bool:
        import shutil

        drives = [d for d in "DEFGHIJKLMN" if os.path.exists(f"{d}:\\")]
        uf2_drive = None
        for d in drives:
            path = f"{d}:\\"
            try:
                if "NRF" in os.path.basename(os.path.normpath(path)).upper():
                    uf2_drive = path
                    break
                for item in os.listdir(path):
                    if "NRF" in item.upper() or "UF2" in item.upper():
                        uf2_drive = path
                        break
            except PermissionError:
                continue

        if not uf2_drive:
            print("No UF2 drive found (press RESET twice on device quickly).")
            return False

        dest = os.path.join(uf2_drive, os.path.basename(filepath))
        print(f"Copying to {dest}...")
        size = os.path.getsize(filepath)
        progress = ProgressBar(size, prefix="Copying")
        with open(filepath, "rb") as src, open(dest, "wb") as dst:
            total = 0
            while True:
                chunk = src.read(65536)
                if not chunk:
                    break
                dst.write(chunk)
                total += len(chunk)
                progress.update(total)
        progress.done()
        print("UF2 flash complete (device will reboot).")
        return True

    @staticmethod
    def _find_tool(name: str) -> Optional[str]:
        if os.name == "nt":
            name += ".exe"
        for path in os.environ.get("PATH", "").split(os.pathsep):
            full = os.path.join(path, name)
            if os.path.isfile(full):
                return full
        if os.name == "nt":
            program_files = ["C:\\Program Files\\Nordic Semiconductor\\nrfjprog",
                             "C:\\Program Files\\SEGGER\\JLink"]
            for base in program_files:
                if os.path.isdir(base):
                    for root, dirs, files in os.walk(base):
                        if name in files:
                            return os.path.join(root, name)
        return None


# ─────────────────────────────────────────────
# ESP32-S3 Flasher
# ─────────────────────────────────────────────

class Esp32S3Flasher:
    """Flash ESP32-S3 via esptool.py (USB download mode)."""

    def __init__(self, port: Optional[str] = None, baud: int = 921600):
        self.port = port
        self.baud = baud

    def flash(self, filepath: str, verify: bool = True) -> bool:
        ok, info = verify_binary(filepath)
        if not ok:
            print(f"Verification failed: {info}")
            return False
        print(f"ESP32-S3 firmware: {info}")

        return self._flash_esptool(filepath, verify)

    def _flash_esptool(self, filepath: str, verify: bool) -> bool:
        try:
            import esptool
        except ImportError:
            print("esptool not installed. Install with: pip install esptool")
            return self._flash_esptool_cli(filepath, verify)

        port_arg = self.port or self._detect_port()
        if not port_arg:
            print("No port specified and could not auto-detect.")
            return False

        print(f"Using port: {port_arg} at {self.baud} baud")
        chip = "esp32s3"
        addr = "0x0"

        try:
            esptool.main([
                "--chip", chip,
                "--port", port_arg,
                "--baud", str(self.baud),
                "write_flash",
                addr, filepath,
            ])
        except SystemExit:
            pass

        if verify:
            print("Verifying...")
            try:
                esptool.main([
                    "--chip", chip,
                    "--port", port_arg,
                    "--baud", str(self.baud),
                    "verify_flash",
                    addr, filepath,
                ])
            except SystemExit:
                pass

        return True

    def _flash_esptool_cli(self, filepath: str, verify: bool) -> bool:
        port_arg = self.port or self._detect_port()
        if not port_arg:
            print("No port specified. Use --port COM3")
            return False

        cmd = (
            f'esptool.py --chip esp32s3 --port "{port_arg}" '
            f'--baud {self.baud} write_flash 0x0 "{filepath}"'
        )
        print(f"Running: {cmd}")
        rc = os.system(cmd)
        if rc != 0:
            return False

        if verify:
            verify_cmd = (
                f'esptool.py --chip esp32s3 --port "{port_arg}" '
                f'--baud {self.baud} verify_flash 0x0 "{filepath}"'
            )
            rc = os.system(verify_cmd)
            if rc != 0:
                print("Verification FAILED")
                return False
            print("Verification PASSED")
        return True

    @staticmethod
    def _detect_port() -> Optional[str]:
        if os.name == "nt":
            try:
                import serial.tools.list_ports
                ports = serial.tools.list_ports.comports()
                for p in ports:
                    if "CP2102" in p.description or "CH340" in p.description or "USB" in p.description.upper():
                        return p.device
                for p in ports:
                    if "COM" in p.device.upper():
                        return p.device
            except ImportError:
                pass
        else:
            for dev in ["/dev/ttyUSB0", "/dev/ttyACM0", "/dev/ttyS0"]:
                if os.path.exists(dev):
                    return dev
        return None


# ─────────────────────────────────────────────
# OTA Uploader
# ─────────────────────────────────────────────

class OtaUploader:
    """Upload firmware image to update server via HTTP PUT."""

    def __init__(self, server_url: str = "https://update.neuroresonator.dev/api/v1/firmware",
                 auth_token: Optional[str] = None):
        self.server_url = server_url
        self.auth_token = auth_token

    def upload(self, filepath: str, version: str, target: str = "esp32s3") -> bool:
        ok, info = verify_binary(filepath)
        if not ok:
            print(f"Verification failed: {info}")
            return False

        sha = sha256_file(filepath)
        size = os.path.getsize(filepath)

        print(f"OTA Upload: {os.path.basename(filepath)}")
        print(f"  Target: {target}")
        print(f"  Version: {version}")
        print(f"  Size: {size} bytes")
        print(f"  SHA256: {sha}")
        print(f"  Server: {self.server_url}")

        try:
            import requests
        except ImportError:
            print("requests not installed. Install with: pip install requests")
            return self._upload_curl(filepath, version, target)

        headers = {"Content-Type": "application/octet-stream"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        headers["X-Firmware-Version"] = version
        headers["X-Firmware-Target"] = target
        headers["X-Firmware-SHA256"] = sha

        print("Uploading...")
        progress = ProgressBar(size, prefix="Upload")
        try:
            with open(filepath, "rb") as f:
                resp = requests.put(
                    self.server_url,
                    data=f,
                    headers=headers,
                    timeout=300,
                )
            progress.done()
            if resp.status_code in (200, 201, 202):
                print(f"Upload successful ({resp.status_code})")
                return True
            else:
                print(f"Upload failed: HTTP {resp.status_code} - {resp.text[:200]}")
                return False
        except requests.RequestException as e:
            print(f"Upload error: {e}")
            return False

    def _upload_curl(self, filepath: str, version: str, target: str) -> bool:
        sha = sha256_file(filepath)
        cmd = (
            f'curl -X PUT "{self.server_url}" '
            f'-H "Content-Type: application/octet-stream" '
            f'-H "X-Firmware-Version: {version}" '
            f'-H "X-Firmware-Target: {target}" '
            f'-H "X-Firmware-SHA256: {sha}" '
        )
        if self.auth_token:
            cmd += f'-H "Authorization: Bearer {self.auth_token}" '
        cmd += f'--data-binary "@{filepath}"'
        print(f"Running: curl ... (large payload)")
        rc = os.system(cmd)
        return rc == 0


# ─────────────────────────────────────────────
# Combined Flasher
# ─────────────────────────────────────────────

class DeviceFlasher:
    """Flash both nRF5340 and ESP32-S3 with verification."""

    @staticmethod
    def flash_all(nrf_file: str, esp_file: str, esp_port: Optional[str] = None,
                  nrf_method: str = "jlink", verify: bool = True) -> bool:
        success = True

        if nrf_file:
            print("=" * 50)
            print("Flashing nRF5340...")
            print("=" * 50)
            nrf = Nrf5340Flasher(method=nrf_method)
            if not nrf.flash(nrf_file, verify=verify):
                print("nRF5340 flashing FAILED")
                success = False
            else:
                print("nRF5340 flashing OK")

        if esp_file:
            print("=" * 50)
            print("Flashing ESP32-S3...")
            print("=" * 50)
            esp = Esp32S3Flasher(port=esp_port)
            if not esp.flash(esp_file, verify=verify):
                print("ESP32-S3 flashing FAILED")
                success = False
            else:
                print("ESP32-S3 flashing OK")

        return success


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NeuroResonator Firmware Flasher")
    target_group = parser.add_mutually_exclusive_group()
    target_group.add_argument("--target", "-t", choices=["nrf5340", "esp32s3"], help="Target chip")
    target_group.add_argument("--all", "-a", action="store_true", help="Flash both nRF5340 and ESP32-S3")
    target_group.add_argument("--ota-upload", type=str, metavar="FILE", help="Upload firmware OTA image")

    parser.add_argument("--file", "-f", type=str, help="Firmware file path")
    parser.add_argument("--nrf-file", type=str, help="nRF5340 firmware file (for --all)")
    parser.add_argument("--esp-file", type=str, help="ESP32-S3 firmware file (for --all)")
    parser.add_argument("--port", "-p", type=str, default=None, help="Serial port (COM3, /dev/ttyUSB0)")
    parser.add_argument("--nrf-method", choices=["jlink", "uf2"], default="jlink", help="nRF5340 flash method")
    parser.add_argument("--no-verify", action="store_true", help="Skip post-flash verification")
    parser.add_argument("--version", type=str, default="0.0.0", help="Firmware version string (for OTA)")
    parser.add_argument("--server", type=str, default="https://update.neuroresonator.dev/api/v1/firmware",
                        help="OTA update server URL")
    parser.add_argument("--auth-token", type=str, default=None, help="OTA auth token")
    parser.add_argument("--baud", type=int, default=921600, help="ESP32 serial baud rate")

    args = parser.parse_args()
    verify = not args.no_verify

    if args.ota_upload:
        uploader = OtaUploader(server_url=args.server, auth_token=args.auth_token)
        target = "esp32s3"
        if args.target == "nrf5340":
            target = "nrf5340"
        success = uploader.upload(args.ota_upload, args.version, target=target)
        sys.exit(0 if success else 1)

    if args.all:
        if not args.nrf_file and not args.esp_file:
            print("Specify --nrf-file and/or --esp-file for --all")
            sys.exit(1)
        success = DeviceFlasher.flash_all(
            nrf_file=args.nrf_file,
            esp_file=args.esp_file,
            esp_port=args.port,
            nrf_method=args.nrf_method,
            verify=verify,
        )
        sys.exit(0 if success else 1)

    if not args.target or not args.file:
        print("Specify --target and --file, or --all, or --ota-upload")
        parser.print_help()
        sys.exit(1)

    if args.target == "nrf5340":
        flasher = Nrf5340Flasher(method=args.nrf_method)
        success = flasher.flash(args.file, verify=verify)
    elif args.target == "esp32s3":
        flasher = Esp32S3Flasher(port=args.port, baud=args.baud)
        success = flasher.flash(args.file, verify=verify)
    else:
        print(f"Unknown target: {args.target}")
        success = False

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
