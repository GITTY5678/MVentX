<#
.SYNOPSIS
    MVentX Native Windows QEMU UEFI Boot Runner (Milestone 1).

.DESCRIPTION
    Boots the prepared virtual disk or staged ESP directory in UEFI mode using
    QEMU and split EDK2/OVMF firmware (read-only code + writable NVRAM variables)
    natively on Windows 11 without WSL2 or Linux.

.PARAMETER DiskPath
    Path to the virtual disk image. Default is 'build\mventx_test.img'.

.PARAMETER EspDir
    Path to the staged ESP directory (for -UseDir mode). Default is 'build\esp'.

.PARAMETER UseDir
    Boot the staged directory directly using QEMU virtual FAT driver (vvfat)
    instead of the raw disk image.

.PARAMETER Empty
    Prepares disk with an empty environment configuration to verify fallback menu.

.PARAMETER NoGraphic
    Runs QEMU in headless/console mode.

.PARAMETER ResetVars
    Resets the UEFI NVRAM variable store by recreating it fresh from the template.

.PARAMETER Memory
    RAM allocated to the VM in MB. Default is 512.

.PARAMETER QemuPath
    Explicit path to qemu-system-x86_64.exe if not in PATH.

.PARAMETER CodePath
    Explicit path to UEFI code firmware image (edk2-x86_64-code.fd).

.PARAMETER VarsTemplatePath
    Explicit path to UEFI variable-store template (edk2-i386-vars.fd).

.PARAMETER VarsPath
    Explicit path for the writable variable-store copy. Default is 'build\ovmf\mventx_vars.fd'.

.PARAMETER OvmfPath
    Alias for -CodePath for backward compatibility.
#>

[CmdletBinding()]
param(
    [string]$DiskPath = "build\mventx_test.img",
    [string]$EspDir = "build\esp",
    [switch]$UseDir,
    [switch]$Empty,
    [switch]$NoGraphic,
    [switch]$ResetVars,
    [int]$Memory = 512,
    [string]$QemuPath,
    [string]$CodePath,
    [string]$VarsTemplatePath,
    [string]$VarsPath,
    [string]$OvmfPath
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path "$ScriptDir\..\..").Path

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " MVentX Native Windows: QEMU UEFI Boot Execution          " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# ------------------------------------------------------------------------------
# 1. Locate QEMU Binary
# ------------------------------------------------------------------------------
$QemuExe = $null

if ($QemuPath -and (Test-Path $QemuPath)) {
    $QemuExe = (Resolve-Path $QemuPath).Path
} else {
    $QemuCmd = Get-Command qemu-system-x86_64.exe -ErrorAction SilentlyContinue
    if ($QemuCmd) {
        $QemuExe = $QemuCmd.Source
    } else {
        # Check standard Windows installation paths
        $StandardPaths = @(
            "C:\Program Files\qemu\qemu-system-x86_64.exe",
            "C:\Program Files (x86)\qemu\qemu-system-x86_64.exe",
            "$env:USERPROFILE\scoop\shims\qemu-system-x86_64.exe",
            "C:\ProgramData\chocolatey\bin\qemu-system-x86_64.exe"
        )
        foreach ($p in $StandardPaths) {
            if (Test-Path $p) {
                $QemuExe = (Resolve-Path $p).Path
                break
            }
        }
    }
}

if (-not $QemuExe) {
    Write-Host ""
    Write-Host "[!] Error: qemu-system-x86_64.exe not found on this system." -ForegroundColor Red
    Write-Host ""
    Write-Host "To install QEMU natively on Windows 11, choose one of these options:" -ForegroundColor Yellow
    Write-Host "  Option 1 (winget - Recommended):" -ForegroundColor Cyan
    Write-Host "      winget install SoftwareFreedomConservancy.QEMU" -ForegroundColor White
    Write-Host "  Option 2 (Chocolatey):" -ForegroundColor Cyan
    Write-Host "      choco install qemu" -ForegroundColor White
    Write-Host "  Option 3 (Official Installer):" -ForegroundColor Cyan
    Write-Host "      Download 64-bit installer from https://www.qemu.org/download/#windows" -ForegroundColor White
    Write-Host ""
    exit 1
}

$QemuDir = Split-Path -Parent $QemuExe

# ------------------------------------------------------------------------------
# 2. Locate UEFI Firmware Code Image (Unit 0, Read-Only)
# ------------------------------------------------------------------------------
$FoundCode = $null
$SpecifiedCode = if ($CodePath) { $CodePath } elseif ($OvmfPath) { $OvmfPath } else { $null }

if ($SpecifiedCode -and (Test-Path $SpecifiedCode)) {
    $FoundCode = (Resolve-Path $SpecifiedCode).Path
} else {
    $CandidateCodePaths = @(
        "$QemuDir\share\edk2-x86_64-code.fd",
        "$QemuDir\share\qemu\edk2-x86_64-code.fd",
        "$QemuDir\edk2-x86_64-code.fd",
        "C:\Program Files\qemu\share\edk2-x86_64-code.fd",
        "$RepoRoot\build\ovmf\edk2-x86_64-code.fd",
        "$RepoRoot\build\ovmf\OVMF_CODE.fd",
        "$RepoRoot\build\ovmf\OVMF.fd"
    )

    foreach ($cand in $CandidateCodePaths) {
        if (Test-Path $cand) {
            $FoundCode = (Resolve-Path $cand).Path
            break
        }
    }
}

if (-not $FoundCode) {
    Write-Host ""
    Write-Host "[!] Error: UEFI Code image (edk2-x86_64-code.fd) not found." -ForegroundColor Red
    Write-Host "Searched locations:" -ForegroundColor Yellow
    foreach ($cand in $CandidateCodePaths) {
        Write-Host "  - $cand" -ForegroundColor Gray
    }
    Write-Host ""
    Write-Host "Please verify your QEMU installation or provide the path explicitly:" -ForegroundColor Cyan
    Write-Host "    .\scripts\windows\run_qemu.ps1 -CodePath C:\path\to\edk2-x86_64-code.fd" -ForegroundColor White
    exit 1
}

# ------------------------------------------------------------------------------
# 3. Locate UEFI Variable-Store Template (Template for Unit 1)
# ------------------------------------------------------------------------------
$FoundVarsTemplate = $null

if ($VarsTemplatePath -and (Test-Path $VarsTemplatePath)) {
    $FoundVarsTemplate = (Resolve-Path $VarsTemplatePath).Path
} else {
    $CandidateVarsPaths = @(
        "$QemuDir\share\edk2-i386-vars.fd",
        "$QemuDir\share\qemu\edk2-i386-vars.fd",
        "$QemuDir\edk2-i386-vars.fd",
        "C:\Program Files\qemu\share\edk2-i386-vars.fd",
        "$QemuDir\share\edk2-arm-vars.fd",
        "$RepoRoot\build\ovmf\edk2-i386-vars.fd",
        "$RepoRoot\build\ovmf\OVMF_VARS.fd"
    )

    foreach ($cand in $CandidateVarsPaths) {
        if (Test-Path $cand) {
            $FoundVarsTemplate = (Resolve-Path $cand).Path
            break
        }
    }
}

if (-not $FoundVarsTemplate) {
    Write-Host ""
    Write-Host "[!] Error: UEFI variable-store template (edk2-i386-vars.fd) not found." -ForegroundColor Red
    Write-Host "Searched locations:" -ForegroundColor Yellow
    foreach ($cand in $CandidateVarsPaths) {
        Write-Host "  - $cand" -ForegroundColor Gray
    }
    Write-Host ""
    Write-Host "Note: edk2-i386-vars.fd is compatible with x86_64 EDK2 firmware." -ForegroundColor Gray
    Write-Host "Provide the template path explicitly using:" -ForegroundColor Cyan
    Write-Host "    .\scripts\windows\run_qemu.ps1 -VarsTemplatePath C:\path\to\edk2-i386-vars.fd" -ForegroundColor White
    exit 1
}

# ------------------------------------------------------------------------------
# 4. Prepare Writable NVRAM Variable-Store Copy
# ------------------------------------------------------------------------------
# Rule: Never modify the original template! Always use a writable copy in build/ovmf/
$OvmfBuildDir = Join-Path $RepoRoot "build\ovmf"
if (-not (Test-Path $OvmfBuildDir)) {
    New-Item -ItemType Directory -Path $OvmfBuildDir -Force | Out-Null
}

$WritableVars = if ($VarsPath) {
    $VarsPath
} else {
    Join-Path $OvmfBuildDir "mventx_vars.fd"
}

$TemplateItem = Get-Item $FoundVarsTemplate
$OriginalTemplateLastWrite = $TemplateItem.LastWriteTimeUtc
$OriginalTemplateLength = $TemplateItem.Length

if ($ResetVars) {
    Write-Host "[*] -ResetVars specified: Recreating variable store from template..." -ForegroundColor Yellow
    if (Test-Path $WritableVars) {
        Remove-Item -Path $WritableVars -Force
    }
    Copy-Item -Path $FoundVarsTemplate -Destination $WritableVars -Force
    Write-Host "    Created fresh variable store: $WritableVars" -ForegroundColor Green
} elseif (-not (Test-Path $WritableVars)) {
    Write-Host "[*] Creating initial writable variable store from template..." -ForegroundColor Yellow
    Copy-Item -Path $FoundVarsTemplate -Destination $WritableVars -Force
    Write-Host "    Created writable variable store: $WritableVars" -ForegroundColor Green
} else {
    Write-Host "[*] Preserving existing variable store: $WritableVars" -ForegroundColor Green
}

# Validate writable copy
$WritableItem = Get-Item $WritableVars
if ($WritableItem.Length -ne $OriginalTemplateLength) {
    Write-Warning "Writable variable store size ($($WritableItem.Length)) differs from template ($OriginalTemplateLength). Recreating..."
    Copy-Item -Path $FoundVarsTemplate -Destination $WritableVars -Force
}

# Confirm original template was not modified
$CurrentTemplateItem = Get-Item $FoundVarsTemplate
if ($CurrentTemplateItem.LastWriteTimeUtc -ne $OriginalTemplateLastWrite -or $CurrentTemplateItem.Length -ne $OriginalTemplateLength) {
    Write-Error "Safety violation: Original firmware template was modified!"
}

# ------------------------------------------------------------------------------
# 5. Ensure Disk Image or ESP Directory Exists
# ------------------------------------------------------------------------------
$ResolvedDisk = Join-Path $RepoRoot $DiskPath
$ResolvedEsp = Join-Path $RepoRoot $EspDir

if ($Empty -or (-not (Test-Path $ResolvedDisk) -and -not (Test-Path $ResolvedEsp))) {
    Write-Host "[*] Disk assets not found or -Empty specified. Running create_test_disk.ps1..." -ForegroundColor Yellow
    $CreateArgs = @()
    if ($Empty) { $CreateArgs += "-Empty" }
    & "$ScriptDir\create_test_disk.ps1" @CreateArgs
}

# ------------------------------------------------------------------------------
# 6. Configure QEMU Arguments
# ------------------------------------------------------------------------------
$QemuArgs = @(
    "-m", "${Memory}M",
    "-drive", "if=pflash,format=raw,unit=0,readonly=on,file=$FoundCode",
    "-drive", "if=pflash,format=raw,unit=1,file=$WritableVars",
    "-net", "none"
)

# Drive configuration
if ($UseDir) {
    $QemuArgs += @("-drive", "file=fat:rw:$ResolvedEsp,format=raw")
} else {
    $QemuArgs += @("-drive", "file=$ResolvedDisk,format=raw,index=0,media=disk")
}

# Acceleration: Check for Windows Hypervisor Platform (WHPX)
$HyperV = Get-Service -Name "vmcompute" -ErrorAction SilentlyContinue
if ($HyperV -and $HyperV.Status -eq "Running") {
    $QemuArgs += @("-accel", "whpx", "-accel", "tcg")
} else {
    $QemuArgs += @("-accel", "tcg")
}

if ($NoGraphic) {
    $QemuArgs += "-nographic"
}

# ------------------------------------------------------------------------------
# 7. Print Diagnostics and Launch
# ------------------------------------------------------------------------------
Write-Host ""
Write-Host "-------------------- QEMU Configuration --------------------" -ForegroundColor DarkCyan
Write-Host "  QEMU Executable:      $QemuExe" -ForegroundColor White
Write-Host "  UEFI Code (Unit 0):   $FoundCode (Read-Only)" -ForegroundColor White
Write-Host "  Vars Template:        $FoundVarsTemplate" -ForegroundColor White
Write-Host "  Writable Vars (Unit 1):$WritableVars (Read-Write, $($WritableItem.Length) bytes)" -ForegroundColor White
Write-Host "  Boot Target:          $(if ($UseDir) { "Virtual FAT Directory ($ResolvedEsp)" } else { "GPT Raw Disk ($ResolvedDisk)" })" -ForegroundColor White
Write-Host "  Allocated Memory:     ${Memory} MB" -ForegroundColor White
Write-Host "  Command Line:         $QemuExe $($QemuArgs -join ' ')" -ForegroundColor Gray
Write-Host "------------------------------------------------------------" -ForegroundColor DarkCyan
Write-Host ""

Write-Host "Starting QEMU UEFI Virtual Machine..." -ForegroundColor Cyan
Write-Host "Press Ctrl+Alt+G to release mouse cursor." -ForegroundColor Gray
Write-Host "==========================================================" -ForegroundColor Cyan

& $QemuExe @QemuArgs
