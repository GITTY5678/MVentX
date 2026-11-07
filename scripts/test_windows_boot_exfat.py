"""Empirical investigation of Windows Boot Manager execution and exFAT compatibility in QEMU/OVMF.

Tests:
1. Genuine Microsoft Windows Boot Manager (`bootmgfw.efi`) chainloaded from exFAT (MVX_DATA).
2. Genuine Microsoft BCD and boot.sdi detection.
3. Behavior under OVMF UEFI firmware.
4. Screendump and serial log capture.
"""

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.boot_config.generator import GrubMenuGenerator
from core.iso_manager.registration import ISORegistrationManager
from core.storage.storage_manager import StorageManager
from scripts.test_qemu_harness import find_firmware, find_qemu
from scripts.windows.make_dual_partition_disk import build_dual_partition_gpt_disk


def test_windows_boot():
    print("=== Milestone 11: Windows Boot Compatibility Empirical Test ===")
    
    qemu_exe = find_qemu()
    if not qemu_exe:
        print("ERROR: QEMU not found")
        return False
        
    code_fd, vars_template_fd = find_firmware(qemu_exe)
    if not code_fd or not vars_template_fd:
        print("ERROR: OVMF firmware not found")
        return False

    temp_dir = Path(tempfile.mkdtemp(prefix="mventx_win_verify_"))
    try:
        boot_mount = temp_dir / "boot"
        data_mount = temp_dir / "data"
        boot_mount.mkdir(parents=True)
        data_mount.mkdir(parents=True)

        # 1. Setup MVX_BOOT (ESP)
        (boot_mount / "EFI" / "BOOT").mkdir(parents=True)
        (boot_mount / "boot" / "grub" / "fonts").mkdir(parents=True)
        (boot_mount / "boot" / "grub" / "x86_64-efi").mkdir(parents=True)

        base_efi = REPO_ROOT / "boot" / "efi" / "BOOTX64.EFI"
        base_grub_cfg = REPO_ROOT / "boot" / "grub" / "grub.cfg"
        shutil.copy2(base_efi, boot_mount / "EFI" / "BOOT" / "BOOTX64.EFI")
        shutil.copy2(base_grub_cfg, boot_mount / "boot" / "grub" / "grub.cfg")

        font_src = REPO_ROOT / "boot" / "grub" / "fonts" / "unicode.pf2"
        if font_src.exists():
            shutil.copy2(font_src, boot_mount / "boot" / "grub" / "fonts" / "unicode.pf2")

        grub_modules_dir = REPO_ROOT / "boot" / "grub" / "x86_64-efi"
        if grub_modules_dir.is_dir():
            for f in grub_modules_dir.glob("*"):
                shutil.copy2(f, boot_mount / "boot" / "grub" / "x86_64-efi" / f.name)

        # 2. Setup MVX_DATA (exFAT)
        (data_mount / "config").mkdir(parents=True)
        (data_mount / "metadata").mkdir(parents=True)
        (data_mount / "backups").mkdir(parents=True)
        (data_mount / "windows" / "win11" / "efi" / "boot").mkdir(parents=True)
        (data_mount / "windows" / "win11" / "efi" / "microsoft" / "boot").mkdir(parents=True)
        (data_mount / "windows" / "win11" / "boot").mkdir(parents=True)
        (data_mount / "windows" / "win11" / "sources").mkdir(parents=True)

        # Copy genuine Microsoft Windows Boot binaries
        win_bootmgfw = Path(r"C:\Windows\Boot\EFI\bootmgfw.efi")
        win_bcd = Path(r"C:\Windows\Boot\DVD\EFI\BCD")
        win_sdi = Path(r"C:\Windows\Boot\DVD\EFI\boot.sdi")
        win_bootres = Path(r"C:\Windows\Boot\Resources\bootres.dll")

        if not win_bootmgfw.exists():
            print(f"ERROR: {win_bootmgfw} does not exist on host!")
            return False

        print(f"Staging genuine Microsoft Boot Manager: {win_bootmgfw} ({win_bootmgfw.stat().st_size} bytes)")
        target_bootx64 = data_mount / "windows" / "win11" / "efi" / "boot" / "bootx64.efi"
        shutil.copy2(win_bootmgfw, target_bootx64)

        if win_bcd.exists():
            print(f"Staging genuine Microsoft BCD database: {win_bcd}")
            shutil.copy2(win_bcd, data_mount / "windows" / "win11" / "efi" / "microsoft" / "boot" / "BCD")
            shutil.copy2(win_bcd, data_mount / "windows" / "win11" / "boot" / "BCD")

        if win_sdi.exists():
            print(f"Staging genuine Microsoft boot.sdi: {win_sdi}")
            shutil.copy2(win_sdi, data_mount / "windows" / "win11" / "boot" / "boot.sdi")

        if win_bootres.exists():
            (data_mount / "windows" / "win11" / "sources").mkdir(parents=True, exist_ok=True)
            shutil.copy2(win_bootres, data_mount / "windows" / "win11" / "sources" / "bootres.dll")

        # Also place BCD and boot.sdi at the root of MVX_DATA as tested in BCD analysis
        (data_mount / "EFI" / "Microsoft" / "Boot").mkdir(parents=True, exist_ok=True)
        (data_mount / "Boot").mkdir(parents=True, exist_ok=True)
        (data_mount / "sources").mkdir(parents=True, exist_ok=True)
        if win_bcd.exists():
            shutil.copy2(win_bcd, data_mount / "EFI" / "Microsoft" / "Boot" / "BCD")
            shutil.copy2(win_bcd, data_mount / "Boot" / "BCD")
        if win_sdi.exists():
            shutil.copy2(win_sdi, data_mount / "Boot" / "boot.sdi")

        # Create a mock boot.wim and install.wim if no real WIM is available
        (data_mount / "sources" / "boot.wim").write_bytes(b"MS_WINPE_IMAGE_DATA_PLACEHOLDER")
        (data_mount / "sources" / "install.wim").write_bytes(b"MS_INSTALL_IMAGE_DATA_PLACEHOLDER")

        # 3. Initialize StorageManager and Register
        storage_mgr = StorageManager(data_mount=data_mount, boot_mount=boot_mount)
        storage_mgr.initialize_data_partition_structure()
        reg_mgr = ISORegistrationManager(storage_mgr)

        rec = reg_mgr.register_windows_media(
            display_name="Windows 11 Setup (Genuine MS Bootloader)",
            bootloader_path="/efi/boot/bootx64.efi",
            staged_subfolder="windows/win11",
            enabled=True,
        )
        print(f"Registered environment: {rec.display_name} (ID: {rec.id})")

        # Configure GRUB for automatic non-interactive boot with serial logging
        grub_cfg = boot_mount / "boot" / "grub" / "grub.cfg"
        grub_lines = grub_cfg.read_text(encoding="utf-8")
        patched = (
            "insmod serial\n"
            "serial --unit=0 --speed=115200\n"
            "terminal_input console serial\n"
            "terminal_output console serial\n"
            "set default=0\n"
            "set timeout=1\n"
            f"{grub_lines}"
        )
        grub_cfg.write_text(patched, encoding="utf-8")

        # 4. Build 256MB dual-partition disk
        disk_img = temp_dir / "mventx_win_test.img"
        print("Building dual-partition GPT disk image (ESP FAT32 + DATA exFAT)...")
        build_dual_partition_gpt_disk(
            esp_source_dir=boot_mount,
            output_path=disk_img,
            disk_size_mb=256,
            esp_size_mb=64,
            data_source_dir=data_mount,
        )
        print(f"Disk image generated: {disk_img} ({disk_img.stat().st_size} bytes)")

        # 5. Launch QEMU with OVMF firmware
        serial_log = temp_dir / "serial.log"
        screen_ppm = temp_dir / "screen.ppm"
        screen_png = REPO_ROOT / "docs" / "screenshots" / "windows_boot_screen.png"
        vars_fd = temp_dir / "mventx_vars.fd"
        shutil.copy2(vars_template_fd, vars_fd)

        cmd = [
            str(qemu_exe),
            "-m", "1024M",
            "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={code_fd}",
            "-drive", f"if=pflash,format=raw,unit=1,file={vars_fd}",
            "-drive", f"file={disk_img},format=raw,index=0,media=disk",
            "-serial", f"file:{serial_log}",
            "-monitor", "stdio",
            "-display", "none",
        ]

        print("Launching QEMU virtual machine...")
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        time.sleep(14)
        try:
            proc.stdin.write(f"screendump {screen_ppm.as_posix()}\n")
            proc.stdin.flush()
            time.sleep(2)
            proc.stdin.write("quit\n")
            proc.stdin.flush()
        except Exception as e:
            print(f"Monitor communication error: {e}")

        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        # Read serial log
        serial_output = ""
        if serial_log.exists():
            serial_output = serial_log.read_text(encoding="utf-8", errors="ignore")
            print("--- Serial Log Output ---")
            print(serial_output[:2000])
            print("-------------------------")

        # Convert ppm screenshot to png if generated
        if screen_ppm.exists():
            print(f"Screen PPM captured: {screen_ppm.stat().st_size} bytes")
            try:
                from PySide6.QtGui import QImage
                qimg = QImage(str(screen_ppm))
                qimg.save(str(screen_png))
                print(f"Saved PNG screenshot to {screen_png} ({qimg.width()}x{qimg.height()})")
            except Exception as e:
                shutil.copy2(screen_ppm, screenshot_dir / "windows_boot_screen.ppm")
                print(f"Saved PPM fallback to docs/screenshots/windows_boot_screen.ppm: {e}")

        return serial_output

    finally:
        pass


if __name__ == "__main__":
    test_windows_boot()
