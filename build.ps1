# Build SayNote.exe and install it for this user: %LOCALAPPDATA%\Programs\SayNote, plus a Start Menu shortcut.
# Run from anywhere: .\build.ps1    Quit SayNote from its tray icon first. Notes in Documents\SayNote are never touched.
Set-Location $PSScriptRoot
if (Get-Process SayNote -ErrorAction SilentlyContinue) { throw 'SayNote is running. Quit it from its tray icon first.' }

# Before ErrorActionPreference=Stop: PyInstaller logs to stderr, which PowerShell 5.1 would treat as a failure when redirected
.\.venv\Scripts\pyinstaller.exe --noconfirm --clean --windowed --onedir --name SayNote --log-level WARN `
    --icon "$PSScriptRoot\saynote.ico" --specpath build `
    --add-data "$PSScriptRoot\ui;ui" --add-data "$PSScriptRoot\saynote.ico;." --add-data "$PSScriptRoot\update.ps1;." `
    --collect-all faster_whisper saynote.py
if ($LASTEXITCODE) { throw 'PyInstaller failed' }
$ErrorActionPreference = 'Stop'

$dest = Join-Path $env:LOCALAPPDATA 'Programs\SayNote'
Copy-Item uninstall.ps1 dist\SayNote  # in the release zip too, so an update keeps it
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
Copy-Item dist\SayNote $dest -Recurse
$exe = Join-Path $dest 'SayNote.exe'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Programs')) 'SayNote.lnk'))
$shortcut.TargetPath = $exe
$shortcut.WorkingDirectory = $dest
$shortcut.Description = 'Voice notes: press a shortcut, talk, press it again'
$shortcut.Save()

# The entry in Settings > Apps, so SayNote can be removed like any other app
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SayNote'
$uninstall = "`"$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe`" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$dest\uninstall.ps1`""
New-Item $key -Force | Out-Null
$values = @{
    DisplayName = 'SayNote'; DisplayIcon = $exe; InstallLocation = $dest; Publisher = 'SayNote'
    DisplayVersion = (Select-String saynote.py -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value
    UninstallString = $uninstall; QuietUninstallString = "$uninstall -Quiet"
}
foreach ($v in $values.GetEnumerator()) { New-ItemProperty $key -Name $v.Key -Value $v.Value -PropertyType String -Force | Out-Null }
$kb = [int]((Get-ChildItem $dest -Recurse -File | Measure-Object Length -Sum).Sum / 1KB)
foreach ($v in @{ NoModify = 1; NoRepair = 1; EstimatedSize = $kb }.GetEnumerator()) { New-ItemProperty $key -Name $v.Key -Value $v.Value -PropertyType DWord -Force | Out-Null }
"Installed $dest, a Start Menu shortcut and an entry in Settings > Apps. Open SayNote from Start."
