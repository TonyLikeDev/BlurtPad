# Swaps in a new SayNote. SayNote downloads and unpacks the new version, copies this script next to it in %TEMP%, starts it
# and quits. This waits for SayNote to exit, moves the old version aside, moves the new one in and starts it.
# If the swap fails, the old version is put back and started again. Problems go to SayNote's log.
param(
    [Parameter(Mandatory)][string]$From,   # the unpacked new version (the folder with SayNote.exe)
    [Parameter(Mandatory)][string]$To,     # the folder SayNote runs from
    [int]$ProcessId,                       # the SayNote to wait for
    [string]$Version,                      # for the Settings > Apps entry
    [switch]$NoStart                       # tests: don't start SayNote afterwards
)
$ErrorActionPreference = 'Stop'
$log = Join-Path $env:LOCALAPPDATA 'SayNote\saynote.log'
$old = "$To.old"
if ($ProcessId) { Wait-Process -Id $ProcessId -Timeout 600 -ErrorAction SilentlyContinue }
try {
    if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }
    for ($i = 0; ; $i++) {  # a virus scanner may still hold a file of the old version for a moment
        try { Rename-Item -LiteralPath $To -NewName (Split-Path $old -Leaf); break } catch { if ($i -ge 10) { throw }; Start-Sleep 1 }
    }
    try {
        Move-Item -LiteralPath $From -Destination $To
    } catch {
        if (Test-Path -LiteralPath $To) { Remove-Item -LiteralPath $To -Recurse -Force }
        Rename-Item -LiteralPath $old -NewName (Split-Path $To -Leaf)
        throw
    }
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SayNote'
    if ($Version -and (Test-Path $key) -and (Get-ItemProperty $key).InstallLocation -eq $To) {  # only the installed copy, never a test's
        Set-ItemProperty $key DisplayVersion $Version
        Set-ItemProperty $key EstimatedSize ([int]((Get-ChildItem -LiteralPath $To -Recurse -File | Measure-Object Length -Sum).Sum / 1KB))
    }
    Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue
} catch {
    Add-Content $log "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ERROR update.ps1 $($_.Exception.Message)" -ErrorAction SilentlyContinue
}
if (-not $NoStart) { Start-Process -FilePath (Join-Path $To 'SayNote.exe') }
