# Build SayNote, make its installer (dist\SayNote-Setup-<version>.exe) and install it for this user.
# Run from anywhere: .\build.ps1    Needs Inno Setup 6 (winget install JRSoftware.InnoSetup). Notes in Documents\SayNote are never touched.
# A running SayNote is asked to quit first, the way its tray's Quit does. One started from dist\ has to be quit by hand.
# No $ErrorActionPreference = 'Stop': PowerShell 5.1 would treat PyInstaller's log on stderr as a failure. Exit codes are checked instead
Set-Location $PSScriptRoot
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw 'Inno Setup 6 makes the installer. Install it with: winget install JRSoftware.InnoSetup' }
if (Get-Process SayNote -ErrorAction SilentlyContinue | Where-Object Path -like "$PSScriptRoot\dist\*") { throw 'SayNote is running from dist\. Quit it from its tray icon first.' }
$version = (Select-String saynote.py -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value

.\.venv\Scripts\pyinstaller.exe --noconfirm --clean --windowed --onedir --name SayNote --log-level WARN `
    --icon "$PSScriptRoot\saynote.ico" --specpath build `
    --add-data "$PSScriptRoot\ui;ui" --add-data "$PSScriptRoot\saynote.ico;." `
    --collect-all faster_whisper saynote.py
if ($LASTEXITCODE) { throw 'PyInstaller failed' }

& $iscc /Q "/DVersion=$version" SayNote.iss
if ($LASTEXITCODE) { throw 'Inno Setup failed' }
$setup = "dist\SayNote-Setup-$version.exe"
$run = Start-Process $setup -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
if ($run.ExitCode) { throw "The installer failed (exit code $($run.ExitCode))" }
"Installed SayNote $version. Open it from Start. $setup is the file to publish: .\release.ps1 ""What changed"""
