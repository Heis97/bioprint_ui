# Ярлык «Биопринт» на рабочем столе.
#   -Mode exe     — на собранный dist\Bioprint\Bioprint.exe (или путь в -Exe)
#   -Mode python  — на start.py через pythonw.exe (без сборки)
#   -Window       — запускать в окне, а не на весь экран
#   -Demo         — отдельный ярлык «Биопринт — демо» (симулятор без принтера)
param(
    [ValidateSet("exe", "python")] [string]$Mode = "exe",
    [string]$Exe = "",
    [switch]$Window,
    [switch]$Demo
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$icon = Join-Path $here "assets\icon.ico"
$flags = @()
if ($Window) { $flags += "--window" }
if ($Demo) { $flags += "--demo" }

if ($Mode -eq "exe") {
    if (-not $Exe) { $Exe = Join-Path $here "dist\Bioprint\Bioprint.exe" }
    if (-not (Test-Path $Exe)) { Write-Host "Не найден $Exe — сначала соберите: build_exe.bat"; exit 1 }
    $target = (Resolve-Path $Exe).Path
    $arguments = ($flags -join " ")
    $workdir = Split-Path -Parent $target
    $icon = $target
} else {
    $pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if (-not $pyw) { $pyw = (Get-Command pyw.exe -ErrorAction SilentlyContinue).Source }
    if (-not $pyw) { Write-Host "Не найден pythonw.exe — установите Python с python.org"; exit 1 }
    $target = $pyw
    if (-not $Window) { $flags += "--fullscreen" }
    $arguments = ('"' + (Join-Path $here "start.py") + '" ' + ($flags -join " ")).Trim()
    $workdir = Split-Path -Parent $here
}

$name = if ($Demo) { "Биопринт — демо" } else { "Биопринт" }
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop "$name.lnk"
$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = $target
$s.Arguments = $arguments
$s.WorkingDirectory = $workdir
$s.IconLocation = "$icon,0"
$s.Description = "Биопринт 1.0 — пульт управления биопринтером"
$s.Save()
Write-Host "Ярлык создан: $lnk"
