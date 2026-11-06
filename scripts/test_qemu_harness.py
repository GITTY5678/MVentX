"""MVentX Automated QEMU UEFI Boot & GRUB Menu Verification Harness (Milestone 6).

Automates the entire lifecycle:
1. Populates test configurations across integration scenarios.
2. Compiles native GRUB dynamic menu scripts (mventx_menu.cfg).
3. Stages EFI boot files (BOOTX64.EFI, grub.cfg, fonts).
4. Generates valid GPT dual-partition virtual disks.
5. Launches QEMU with OVMF firmware in headless mode.
6. Captures high-resolution screendumps of the active GRUB boot menu.
7. Analyzes pixel distributions to verify gfxterm menu rendering.
8. Exports PNG screenshots to docs/screenshots/.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtGui import QImage

from core.boot_config.generator import GrubMenuGenerator
from core.storage.disk_validator import DiskValidator
from scripts.windows.make_dual_partition_disk import build_dual_partition_gpt_disk


def find_qemu() -> Optional[Path]:
    """Locate qemu-system-x86_64 executable."""
    cmd = shutil.which("qemu-system-x86_64")
    if cmd:
        return Path(cmd).resolve()

    common_paths = [
        Path(r"C:\Program Files\qemu\qemu-system-x86_64.exe"),
        Path(r"C:\Program Files (x86)\qemu\qemu-system-x86_64.exe"),
    ]
    for p in common_paths:
        if p.is_file():
            return p
    return None


def find_firmware(qemu_exe: Path) -> Tuple[Optional[Path], Optional[Path]]:
    """Locate EDK2 code and variable-store template firmware."""
    qemu_dir = qemu_exe.parent
    code_candidates = [
        qemu_dir / "share" / "edk2-x86_64-code.fd",
        qemu_dir / "share" / "qemu" / "edk2-x86_64-code.fd",
        qemu_dir / "edk2-x86_64-code.fd",
        Path(r"C:\Program Files\qemu\share\edk2-x86_64-code.fd"),
    ]
    vars_candidates = [
        qemu_dir / "share" / "edk2-i386-vars.fd",
        qemu_dir / "share" / "qemu" / "edk2-i386-vars.fd",
        qemu_dir / "edk2-i386-vars.fd",
        Path(r"C:\Program Files\qemu\share\edk2-i386-vars.fd"),
    ]

    found_code = next((p for p in code_candidates if p.is_file()), None)
    found_vars = next((p for p in vars_candidates if p.is_file()), None)
    return found_code, found_vars


class QemuBootHarness:
    """Manages virtual disk generation, QEMU boot execution, and menu verification."""

    def __init__(
        self,
        qemu_exe: Path,
        code_fd: Path,
        vars_template_fd: Path,
        output_dir: Optional[Path] = None,
    ) -> None:
        self.qemu_exe = qemu_exe
        self.code_fd = code_fd
        self.vars_template_fd = vars_template_fd
        self.output_dir = output_dir or (REPO_ROOT / "docs" / "screenshots")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Prepare writable OVMF variables in build/ovmf
        self.ovmf_dir = REPO_ROOT / "build" / "ovmf"
        self.ovmf_dir.mkdir(parents=True, exist_ok=True)
        self.writable_vars = self.ovmf_dir / "mventx_vars.fd"
        if not self.writable_vars.exists() or self.writable_vars.stat().st_size != vars_template_fd.stat().st_size:
            shutil.copy2(vars_template_fd, self.writable_vars)

        # Base EFI components
        self.base_efi_bin = REPO_ROOT / "build" / "esp" / "EFI" / "BOOT" / "BOOTX64.EFI"
        if not self.base_efi_bin.exists():
            self.base_efi_bin = REPO_ROOT / "boot" / "efi" / "BOOTX64.EFI"

        self.base_grub_cfg = REPO_ROOT / "boot" / "grub" / "grub.cfg"
        self.base_font = REPO_ROOT / "build" / "esp" / "boot" / "grub" / "fonts" / "ascii.pf2"

    def stage_virtual_disk(
        self,
        staging_dir: Path,
        config_data: Dict[str, Any],
        disk_filename: str = "test_disk.img",
    ) -> Path:
        """Create a complete dual-partition GPT disk image with compiled menu."""
        esp_dir = staging_dir / "esp"
        esp_dir.mkdir(parents=True, exist_ok=True)
        (esp_dir / "EFI" / "BOOT").mkdir(parents=True, exist_ok=True)
        (esp_dir / "boot" / "grub" / "fonts").mkdir(parents=True, exist_ok=True)

        # Deploy bootloader and grub.cfg
        shutil.copy2(self.base_efi_bin, esp_dir / "EFI" / "BOOT" / "BOOTX64.EFI")
        shutil.copy2(self.base_grub_cfg, esp_dir / "boot" / "grub" / "grub.cfg")
        if self.base_font.exists():
            shutil.copy2(self.base_font, esp_dir / "boot" / "grub" / "fonts" / "ascii.pf2")

        # Deploy GRUB runtime modules if available
        mod_src = REPO_ROOT / "boot" / "grub" / "x86_64-efi"
        if not mod_src.exists():
            mod_src = REPO_ROOT / "build" / "grub-windows" / "grub-2.12-for-windows" / "x86_64-efi"
        if mod_src.is_dir():
            target_mods = esp_dir / "boot" / "grub" / "x86_64-efi"
            target_mods.mkdir(parents=True, exist_ok=True)
            for m in mod_src.glob("*"):
                shutil.copy2(m, target_mods / m.name)

        # Compile dynamic menu
        generator = GrubMenuGenerator()
        menu_path = esp_dir / "boot" / "grub" / "mventx_menu.cfg"
        generator.write_menu_file(config_data, menu_path)

        # Build 256MB GPT dual-partition image
        disk_path = staging_dir / disk_filename
        build_dual_partition_gpt_disk(
            esp_source_dir=esp_dir,
            output_path=disk_path,
            disk_size_mb=256,
            esp_size_mb=64,
        )

        # Validate structural integrity
        validator = DiskValidator(disk_path)
        validator.validate_all()

        return disk_path

    def boot_and_verify(
        self,
        disk_path: Path,
        scenario_name: str,
        wait_seconds: float = 6.0,
    ) -> Dict[str, Any]:
        """Boot disk in QEMU, capture screendump, and verify menu graphics."""
        ppm_file = self.output_dir / f"qemu_{scenario_name}.ppm"
        png_file = self.output_dir / f"qemu_{scenario_name}.png"

        # Create a dedicated fresh variable store copy for this boot
        run_vars = disk_path.parent / f"vars_{scenario_name}.fd"
        shutil.copy2(self.vars_template_fd, run_vars)

        cmd = [
            str(self.qemu_exe),
            "-m", "512M",
            "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={self.code_fd}",
            "-drive", f"if=pflash,format=raw,unit=1,file={run_vars}",
            "-drive", f"file={disk_path},format=raw,index=0,media=disk",
            "-net", "none",
            "-display", "none",
            "-monitor", "stdio",
        ]

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            time.sleep(wait_seconds)
            proc.stdin.write(f"screendump {ppm_file.as_posix()}\n")
            proc.stdin.flush()
            time.sleep(1.0)
            proc.stdin.write("quit\n")
            proc.stdin.flush()
            proc.communicate(timeout=10)
        except Exception:
            proc.kill()
            raise

        if not ppm_file.exists():
            raise RuntimeError(f"QEMU screendump was not generated: {ppm_file}")

        # Convert to PNG via PySide6
        qimg = QImage(str(ppm_file))
        qimg.save(str(png_file))

        # Pixel distribution inspection
        with open(ppm_file, "rb") as f:
            for _ in range(3):
                f.readline()
            pixels = f.read()

        total_pixels = len(pixels) // 3
        colors = Counter([pixels[i : i + 3] for i in range(0, len(pixels), 3)])

        # GRUB palette colors
        cyan_highlight = colors.get(b"\x54\xfe\xfe", 0)  # RGB(84, 254, 254)
        border_text = colors.get(b"\xa8\xa8\xa8", 0)     # RGB(168, 168, 168)
        black_bg = colors.get(b"\x00\x00\x00", 0)        # RGB(0, 0, 0)

        # Remove raw PPM to save space; keep high-res PNG
        try:
            ppm_file.unlink()
        except OSError:
            pass

        return {
            "scenario": scenario_name,
            "png_path": str(png_file),
            "png_size_bytes": png_file.stat().st_size if png_file.exists() else 0,
            "resolution": f"{qimg.width()}x{qimg.height()}",
            "total_pixels": total_pixels,
            "highlight_cyan_pixels": cyan_highlight,
            "border_text_pixels": border_text,
            "black_bg_pixels": black_bg,
            "grub_menu_detected": (cyan_highlight > 0 and border_text > 0),
        }


def get_scenario_configs() -> Dict[str, Dict[str, Any]]:
    """Return test configurations for integration scenarios."""
    return {
        "scenario_a_empty": {
            "version": "1.0.0",
            "meta": {"title": "MVentX Unconfigured Fallback", "timeout": 10},
            "environments": [],
        },
        "scenario_b_single": {
            "version": "1.0.0",
            "meta": {"title": "MVentX Single Environment", "timeout": 10},
            "environments": [
                {
                    "id": "ubuntu-24-04",
                    "display_name": "Ubuntu 24.04 LTS Desktop",
                    "type": "iso",
                    "boot_method": "linux_generic_loopback",
                    "enabled": True,
                    "order": 1,
                    "iso_path": "ISO/ubuntu-24.04-desktop-amd64.iso",
                }
            ],
        },
        "scenario_c_multi": {
            "version": "1.0.0",
            "meta": {"title": "MVentX Multi-Environment Boot", "timeout": 10},
            "environments": [
                {
                    "id": "alpine-3-20",
                    "display_name": "Alpine Linux 3.20 (Standard)",
                    "type": "iso",
                    "boot_method": "linux_generic_loopback",
                    "enabled": True,
                    "order": 1,
                    "iso_path": "ISO/alpine.iso",
                },
                {
                    "id": "debian-12",
                    "display_name": "Debian GNU/Linux 12 (Bookworm)",
                    "type": "iso",
                    "boot_method": "linux_generic_loopback",
                    "enabled": True,
                    "order": 2,
                    "iso_path": "ISO/debian.iso",
                },
                {
                    "id": "arch-2026",
                    "display_name": "Arch Linux Rolling (x86_64)",
                    "type": "iso",
                    "boot_method": "linux_generic_loopback",
                    "enabled": True,
                    "order": 3,
                    "iso_path": "ISO/arch.iso",
                },
            ],
        },
        "scenario_d_disabled": {
            "version": "1.0.0",
            "meta": {"title": "MVentX Active Filtered View", "timeout": 10},
            "environments": [
                {
                    "id": "alpine-active",
                    "display_name": "Active Alpine Minimal",
                    "type": "iso",
                    "boot_method": "linux_generic_loopback",
                    "enabled": True,
                    "order": 1,
                    "iso_path": "ISO/alpine.iso",
                },
                {
                    "id": "debian-disabled",
                    "display_name": "Debian Inactive (Hidden)",
                    "type": "iso",
                    "boot_method": "linux_generic_loopback",
                    "enabled": False,
                    "order": 2,
                    "iso_path": "ISO/debian.iso",
                },
            ],
        },
        "scenario_j_special_chars": {
            "version": "1.0.0",
            "meta": {"title": "MVentX Special Characters", "timeout": 15},
            "environments": [
                {
                    "id": "special-quoted",
                    "display_name": 'Alpine "Edge" [6.6-LTS] (x86_64)',
                    "type": "iso",
                    "boot_method": "linux_generic_loopback",
                    "enabled": True,
                    "order": 1,
                    "iso_path": "ISO/alpine.iso",
                }
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="MVentX QEMU UEFI Boot & GRUB Menu Verification Harness")
    parser.add_argument("--scenario", default="all", help="Scenario to run: empty, single, multi, disabled, special, or all")
    parser.add_argument("--output-dir", default=None, help="Directory to save screenshot PNGs")
    parser.add_argument("--timeout", type=float, default=6.0, help="Wait time in seconds before screendump")
    args = parser.parse_args()

    qemu_exe = find_qemu()
    if not qemu_exe:
        print("[FAIL] QEMU executable (qemu-system-x86_64) not found.", file=sys.stderr)
        return 1

    code_fd, vars_template_fd = find_firmware(qemu_exe)
    if not code_fd or not vars_template_fd:
        print("[FAIL] EDK2 OVMF firmware files not found.", file=sys.stderr)
        return 1

    out_dir = Path(args.output_dir) if args.output_dir else (REPO_ROOT / "docs" / "screenshots")
    harness = QemuBootHarness(
        qemu_exe=qemu_exe,
        code_fd=code_fd,
        vars_template_fd=vars_template_fd,
        output_dir=out_dir,
    )

    all_configs = get_scenario_configs()
    selected_scenarios = (
        list(all_configs.keys())
        if args.scenario == "all"
        else [k for k in all_configs if args.scenario in k]
    )

    if not selected_scenarios:
        print(f"[FAIL] Unknown scenario: {args.scenario}", file=sys.stderr)
        return 1

    print("==========================================================")
    print(" MVentX Milestone 6: QEMU Boot Verification Harness       ")
    print("==========================================================")
    print(f" QEMU:     {qemu_exe}")
    print(f" Code:     {code_fd}")
    print(f" Vars:     {harness.writable_vars}")
    print(f" Out Dir:  {out_dir}")
    print("==========================================================")

    results = []
    temp_dir = Path(tempfile.mkdtemp(prefix="mventx_qemu_harness_"))

    try:
        for sc in selected_scenarios:
            print(f"\n[*] Executing {sc}...")
            cfg = all_configs[sc]
            disk = harness.stage_virtual_disk(temp_dir, cfg, f"{sc}.img")
            res = harness.boot_and_verify(disk, sc, wait_seconds=args.timeout)
            results.append(res)
            status = "VERIFIED" if res["grub_menu_detected"] else "NOT DETECTED"
            print(f"    - Resolution:       {res['resolution']}")
            print(f"    - Highlight pixels: {res['highlight_cyan_pixels']} (Cyan)")
            print(f"    - Border pixels:    {res['border_text_pixels']} (Light Gray)")
            print(f"    - Screenshot:       {res['png_path']}")
            print(f"    - Result:           {status}")

        print("\n==========================================================")
        print(" Summary of QEMU Menu Verifications:")
        print("==========================================================")
        for r in results:
            tag = "[OK]" if r["grub_menu_detected"] else "[FAIL]"
            print(f" {tag} {r['scenario']:<28} Resolution: {r['resolution']} | Saved: {Path(r['png_path']).name}")
        print("==========================================================")
        return 0
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
