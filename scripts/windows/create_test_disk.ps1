<#
.SYNOPSIS
    MVentX Native Windows Dual-Partition Virtual Disk Builder (Milestone 2).

.DESCRIPTION
    Constructs a bootable UEFI GPT raw disk image with two distinct partitions:
      - Partition 1: MVX_BOOT (FAT32 EFI System Partition)
      - Partition 2: MVX_DATA (exFAT User Data Partition)
    without requiring WSL2, Linux, or Administrator privileges.

.PARAMETER Empty
    Generate an empty environment configuration to verify the fallback menu.

.PARAMETER DiskPath
    Path to the output raw virtual disk image. Default is 'build\mventx_test.img'.

.PARAMETER EspDir
    Path to the staged EFI filesystem directory. Default is 'build\esp'.

.PARAMETER DataDir
    Path to the staged data partition directory. Default is 'build\data'.

.PARAMETER DiskSizeMB
    Virtual disk size in megabytes. Default is 256.

.PARAMETER EspSizeMB
    ESP partition size in megabytes. Default is 64.

.PARAMETER DownloadGrub
    Attempt to download official GNU GRUB for Windows to compile BOOTX64.EFI natively.

.PARAMETER UseDiskpart
    Optional: Use native Windows diskpart to generate a VHD (Requires Administrator).
#>

[CmdletBinding()]
param(
    [switch]$Empty,
    [string]$DiskPath = "build\mventx_test.img",
    [string]$EspDir = "build\esp",
    [string]$DataDir = "build\data",
    [int]$DiskSizeMB = 256,
    [int]$EspSizeMB = 64,
    [switch]$DownloadGrub,
    [switch]$UseDiskpart
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path "$ScriptDir\..\..").Path

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " MVentX Native Windows: Dual-Partition Disk Preparation    " -ForegroundColor Cyan
Write-Host "   Partition 1: MVX_BOOT (FAT32, $EspSizeMB MB)            " -ForegroundColor Cyan
Write-Host "   Partition 2: MVX_DATA (exFAT, $($DiskSizeMB - $EspSizeMB) MB)           " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# ------------------------------------------------------------------------------
# 1. Verify Python 3
# ------------------------------------------------------------------------------
$PythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonCmd) {
    Write-Error @"
Error: Python 3 was not found in your PATH.
Please install Python 3.10+ on Windows:
    winget install Python.Python.3.12
or download from https://www.python.org/downloads/windows/
"@
}
Write-Host "[*] Python 3 detected: $($PythonCmd.Source)" -ForegroundColor Green

# ------------------------------------------------------------------------------
# 2. Setup Staging Directories for Both Partitions
# ------------------------------------------------------------------------------
$ResolvedEspDir = Join-Path $RepoRoot $EspDir
$ResolvedDataDir = Join-Path $RepoRoot $DataDir
$ResolvedDiskPath = Join-Path $RepoRoot $DiskPath
$BuildDir = Split-Path -Parent $ResolvedDiskPath

New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null

# Staging MVX_BOOT (ESP)
New-Item -ItemType Directory -Path "$ResolvedEspDir\EFI\BOOT" -Force | Out-Null
New-Item -ItemType Directory -Path "$ResolvedEspDir\boot\grub\fonts" -Force | Out-Null

# Staging MVX_DATA (User Data)
New-Item -ItemType Directory -Path "$ResolvedDataDir\ISO" -Force | Out-Null
New-Item -ItemType Directory -Path "$ResolvedDataDir\config" -Force | Out-Null
New-Item -ItemType Directory -Path "$ResolvedDataDir\metadata" -Force | Out-Null
New-Item -ItemType Directory -Path "$ResolvedDataDir\backups" -Force | Out-Null

# ------------------------------------------------------------------------------
# 3. Setup Authoritative Configuration on MVX_DATA and Sync to MVX_BOOT
# ------------------------------------------------------------------------------
$DataConfigPath = "$ResolvedDataDir\config\mventx.json"
$MenuOutput = "$ResolvedEspDir\boot\grub\mventx_menu.cfg"

Write-Host "[*] Initializing authoritative mventx.json on MVX_DATA..." -ForegroundColor Yellow
$GenerateArgs = @("$RepoRoot\scripts\generate_test_menu.py", "--output", $MenuOutput)
if ($Empty) {
    $GenerateArgs += "--empty"
    Write-Host "[*] Mode: Empty fallback configuration" -ForegroundColor Yellow
    # Write empty config to MVX_DATA
    $EmptyConfig = @{
        version = "1.0.0"
        meta = @{ title = "MVentX Multiboot System (Empty Configuration)"; timeout = 10 }
        environments = @()
    } | ConvertTo-Json -Depth 5
    Set-Content -Path $DataConfigPath -Value $EmptyConfig -Encoding UTF8
} else {
    Write-Host "[*] Mode: Sample registered environments" -ForegroundColor Yellow
    # Copy project sample/default config to MVX_DATA
    $DefaultsJson = "$RepoRoot\core\config\defaults.json"
    if (Test-Path $DefaultsJson) {
        Copy-Item -Path $DefaultsJson -Destination $DataConfigPath -Force
    }
}

# Compile dynamic menu to MVX_BOOT
& python @GenerateArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to generate dynamic menu configuration."
}

# ------------------------------------------------------------------------------
# 4. Copy Primary GRUB Configuration to MVX_BOOT
# ------------------------------------------------------------------------------
$GrubCfgSource = "$RepoRoot\boot\grub\grub.cfg"
$GrubCfgTarget = "$ResolvedEspDir\boot\grub\grub.cfg"
if (Test-Path $GrubCfgSource) {
    Copy-Item -Path $GrubCfgSource -Destination $GrubCfgTarget -Force
    Write-Host "[*] Deployed primary grub.cfg to MVX_BOOT staging" -ForegroundColor Green
} else {
    Write-Error "Could not find primary grub.cfg at $GrubCfgSource"
}

# ------------------------------------------------------------------------------
# 5. Locate or Obtain BOOTX64.EFI
# ------------------------------------------------------------------------------
$TargetBootEfi = "$ResolvedEspDir\EFI\BOOT\BOOTX64.EFI"
$FoundBootEfi = $null

$CandidatePaths = @(
    "$BuildDir\BOOTX64.EFI",
    "$RepoRoot\boot\efi\BOOTX64.EFI"
)

foreach ($cand in $CandidatePaths) {
    if (Test-Path $cand) {
        $FoundBootEfi = $cand
        break
    }
}

if (-not $FoundBootEfi -and $DownloadGrub) {
    Write-Host "[*] -DownloadGrub specified. Attempting to download official GNU GRUB for Windows..." -ForegroundColor Yellow
    $GrubZipUrl = "https://ftp.gnu.org/gnu/grub/grub-2.12-for-windows.zip"
    $GrubZipPath = "$BuildDir\grub-2.12-for-windows.zip"
    $GrubExtractDir = "$BuildDir\grub-windows"
    
    try {
        Write-Host "    Downloading: $GrubZipUrl"
        Invoke-WebRequest -Uri $GrubZipUrl -OutFile $GrubZipPath -UseBasicParsing
        Expand-Archive -Path $GrubZipPath -DestinationPath $GrubExtractDir -Force
        
        $GrubMkimage = "$GrubExtractDir\grub-2.12-for-windows\grub-mkimage.exe"
        $GrubModules = "$GrubExtractDir\grub-2.12-for-windows\x86_64-efi"
        
        if (Test-Path $GrubMkimage) {
            Write-Host "[*] Compiling BOOTX64.EFI via native Windows grub-mkimage.exe..." -ForegroundColor Green
            $MkimageArgs = @(
                "-d", $GrubModules,
                "-O", "x86_64-efi",
                "-o", "$BuildDir\BOOTX64.EFI",
                "-p", "/boot/grub",
                "part_gpt", "part_msdos", "fat", "exfat", "ext2", "ntfs", "iso9660",
                "search", "search_fs_file", "search_label", "test", "echo", "normal",
                "configfile", "sleep", "efi_gop", "efi_uga", "font", "gfxterm", "gfxmenu"
            )
            & $GrubMkimage @MkimageArgs
            if (Test-Path "$BuildDir\BOOTX64.EFI") {
                $FoundBootEfi = "$BuildDir\BOOTX64.EFI"
            }
        }
    } catch {
        Write-Warning "Could not download GNU GRUB automatically: $_"
    }
}

# Deploy fonts if available
$FontCandidates = @(
    "$BuildDir\grub-windows\grub-2.12-for-windows\ascii.pf2",
    "$RepoRoot\boot\assets\ascii.pf2",
    "$RepoRoot\boot\assets\unicode.pf2"
)
foreach ($fc in $FontCandidates) {
    if (Test-Path $fc) {
        Copy-Item -Path $fc -Destination "$ResolvedEspDir\boot\grub\fonts\$(Split-Path $fc -Leaf)" -Force
        Write-Host "[*] Deployed font: $(Split-Path $fc -Leaf)" -ForegroundColor Green
        break
    }
}

# Deploy dynamic GRUB modules (loopback, linux, iso9660, etc.)
$ModuleCandidates = @(
    "$RepoRoot\boot\grub\x86_64-efi",
    "$BuildDir\grub-windows\grub-2.12-for-windows\x86_64-efi"
)
foreach ($mc in $ModuleCandidates) {
    if (Test-Path $mc) {
        $EspModuleDir = "$ResolvedEspDir\boot\grub\x86_64-efi"
        if (-not (Test-Path $EspModuleDir)) {
            New-Item -ItemType Directory -Path $EspModuleDir -Force | Out-Null
        }
        Copy-Item -Path "$mc\*" -Destination $EspModuleDir -Force
        Write-Host "[*] Deployed GRUB x86_64-efi runtime modules from $mc" -ForegroundColor Green
        break
    }
}

if ($FoundBootEfi) {
    Copy-Item -Path $FoundBootEfi -Destination $TargetBootEfi -Force
    Write-Host "[*] Deployed BOOTX64.EFI from $FoundBootEfi" -ForegroundColor Green

    # Patch MinGW upstream relocation symbol mismatch (__stack_chk_guard -> _stack_chk_guard) if present
    try {
        $EfiBytes = [System.IO.File]::ReadAllBytes($TargetBootEfi)
        $Target1 = [System.Text.Encoding]::ASCII.GetBytes("`0__stack_chk_fail`0")
        $Target2 = [System.Text.Encoding]::ASCII.GetBytes("`0__stack_chk_guard`0")
        
        $Idx1 = -1
        $Idx2 = -1
        for ($i = 0; $i -lt ($EfiBytes.Length - 20); $i++) {
            if ($Idx1 -eq -1) {
                $m1 = $true
                for ($j = 0; $j -lt $Target1.Length; $j++) {
                    if ($EfiBytes[$i + $j] -ne $Target1[$j]) { $m1 = $false; break }
                }
                if ($m1) { $Idx1 = $i }
            }
            if ($Idx2 -eq -1) {
                $m2 = $true
                for ($j = 0; $j -lt $Target2.Length; $j++) {
                    if ($EfiBytes[$i + $j] -ne $Target2[$j]) { $m2 = $false; break }
                }
                if ($m2) { $Idx2 = $i }
            }
            if ($Idx1 -ne -1 -and $Idx2 -ne -1) { break }
        }

        if ($Idx1 -ne -1 -and $Idx2 -ne -1) {
            $Rep1 = [System.Text.Encoding]::ASCII.GetBytes("`0_stack_chk_fail`0`0")
            $Rep2 = [System.Text.Encoding]::ASCII.GetBytes("`0_stack_chk_guard`0`0")
            [System.Array]::Copy($Rep1, 0, $EfiBytes, $Idx1, $Rep1.Length)
            [System.Array]::Copy($Rep2, 0, $EfiBytes, $Idx2, $Rep2.Length)
            [System.IO.File]::WriteAllBytes($TargetBootEfi, $EfiBytes)
            Write-Host "[*] Applied MinGW stack_chk symbol compatibility patch to BOOTX64.EFI" -ForegroundColor Green
        }
    } catch {
        Write-Warning "Could not check/patch stack_chk symbol: $_"
    }
} else {
    Write-Host ""
    Write-Host "[!] Note: BOOTX64.EFI is not yet present in $TargetBootEfi" -ForegroundColor Magenta
    Write-Host "    To obtain or build a native BOOTX64.EFI on Windows:" -ForegroundColor Cyan
    Write-Host "    Option A: Run this script with -DownloadGrub to fetch official GNU GRUB binaries:" -ForegroundColor Gray
    Write-Host "              .\scripts\windows\create_test_disk.ps1 -DownloadGrub" -ForegroundColor White
    Write-Host "    Option B: Place any pre-compiled 64-bit GRUB EFI binary at:" -ForegroundColor Gray
    Write-Host "              boot\efi\BOOTX64.EFI" -ForegroundColor White
    Write-Host ""
    
    if (-not (Test-Path $TargetBootEfi)) {
        [System.IO.File]::WriteAllBytes($TargetBootEfi, [System.Text.Encoding]::ASCII.GetBytes("MVENTX_EFI_PLACEHOLDER_STUB"))
        Write-Host "[*] Created staging placeholder at $TargetBootEfi" -ForegroundColor Gray
    }
}

# ------------------------------------------------------------------------------
# 6. Build Dual-Partition Virtual Disk Image
# ------------------------------------------------------------------------------
if ($UseDiskpart) {
    # Check for elevation
    $IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $IsAdmin) {
        Write-Error "-UseDiskpart requires elevated Administrator privileges. Please run PowerShell as Administrator or omit -UseDiskpart to use the default rootless builder."
    }

    $VhdPath = [System.IO.Path]::ChangeExtension($ResolvedDiskPath, ".vhd")
    Write-Host "[*] Creating native dual-partition VHD via diskpart: $VhdPath..." -ForegroundColor Yellow

    $DataSizeMB = $DiskSizeMB - $EspSizeMB
    $DiskpartScript = @"
create vdisk file="$VhdPath" maximum=$DiskSizeMB type=expandable
select vdisk file="$VhdPath"
attach vdisk
convert gpt
create partition efi size=$EspSizeMB
format fs=fat32 quick label="MVX_BOOT"
assign letter=Y
create partition primary size=$DataSizeMB
format fs=exfat quick label="MVX_DATA"
assign letter=Z
"@
    $ScriptTemp = New-TemporaryFile
    Set-Content -Path $ScriptTemp.FullName -Value $DiskpartScript -Encoding ASCII
    
    try {
        & diskpart /s $ScriptTemp.FullName
        Write-Host "[*] Copying MVX_BOOT payload to Y:\" -ForegroundColor Green
        Copy-Item -Path "$ResolvedEspDir\*" -Destination "Y:\" -Recurse -Force

        Write-Host "[*] Copying MVX_DATA payload to Z:\" -ForegroundColor Green
        Copy-Item -Path "$ResolvedDataDir\*" -Destination "Z:\" -Recurse -Force
    } finally {
        $DetachScript = @"
select vdisk file="$VhdPath"
detach vdisk
"@
        Set-Content -Path $ScriptTemp.FullName -Value $DetachScript -Encoding ASCII
        & diskpart /s $ScriptTemp.FullName
        Remove-Item $ScriptTemp.FullName -Force
    }
    Write-Host "[*] VHD created successfully: $VhdPath" -ForegroundColor Green
} else {
    # Default: Rootless Pure-Python Dual-Partition GPT Disk Generator
    Write-Host "[*] Building Dual-Partition GPT disk image via make_dual_partition_disk.py..." -ForegroundColor Yellow
    $MakeDiskScript = "$ScriptDir\make_dual_partition_disk.py"
    $MakeArgs = @(
        $MakeDiskScript,
        "--esp-source", $ResolvedEspDir,
        "--data-source", $ResolvedDataDir,
        "--output", $ResolvedDiskPath,
        "--size-mb", $DiskSizeMB.ToString(),
        "--esp-size-mb", $EspSizeMB.ToString()
    )

    & python @MakeArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to build dual-partition virtual disk image."
    }
    Write-Host "[*] Dual-partition virtual disk image created: $ResolvedDiskPath" -ForegroundColor Green
}

# ------------------------------------------------------------------------------
# 7. Validate Disk Image Structure
# ------------------------------------------------------------------------------
Write-Host "[*] Validating dual-partition GPT structure..." -ForegroundColor Yellow
$ValidateScript = @"
from core.storage.disk_validator import DiskValidator
v = DiskValidator(r'$ResolvedDiskPath')
rep = v.validate_all()
print(f'Disk validation passed: {rep[\"disk_size_mb\"]} MB ({len(rep[\"partitions\"])} partitions)')
for p in rep['partitions']:
    print(f'  - Partition {p[\"index\"]}: {p[\"name\"]} ({p[\"size_mb\"]} MB, LBA {p[\"start_lba\"]}..{p[\"end_lba\"]})')
"@
& python -c $ValidateScript
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Disk validation encountered warnings or errors."
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Staged MVX_BOOT directory: $ResolvedEspDir" -ForegroundColor Cyan
Write-Host " Staged MVX_DATA directory: $ResolvedDataDir" -ForegroundColor Cyan
Write-Host " Output dual-partition image: $ResolvedDiskPath" -ForegroundColor Cyan
Write-Host " Next step: Boot with QEMU using:" -ForegroundColor Cyan
Write-Host "     .\scripts\windows\run_qemu.ps1" -ForegroundColor White
Write-Host "==========================================================" -ForegroundColor Cyan
