# Removes SayNote for this user: the app, its Start Menu shortcut, Start with Windows, its settings and log, and its entry in
# Settings > Apps, which is what runs this. Notes in Documents\SayNote are kept, and so are recordings kept as .wav files.
# -Quiet skips the closing message.
param([switch]$Quiet)
# Always the installed copy, never the folder this script sits in: the repo has a copy too, and that one must not delete the repo
$app = Join-Path $env:LOCALAPPDATA 'Programs\SayNote'
$exe = Join-Path $app 'SayNote.exe'

if (Get-Process SayNote -ErrorAction SilentlyContinue) {
    if (Test-Path $exe) { & $exe --quit }  # quits like the tray's Quit: a recording in progress is written down first
    Wait-Process SayNote -Timeout 300 -ErrorAction SilentlyContinue
    if (Get-Process SayNote -ErrorAction SilentlyContinue) {  # still writing a long note: never kill it, that would lose the note
        if (-not $Quiet) { (New-Object -ComObject WScript.Shell).Popup('SayNote is still writing down a note. Try again in a few minutes, or quit SayNote from its tray icon first.', 0, 'SayNote', 48) | Out-Null }
        exit 1
    }
    Start-Sleep -Milliseconds 500  # let Windows release the files
}

$run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$cmd = (Get-ItemProperty $run -Name SayNote -ErrorAction SilentlyContinue).SayNote
if ($cmd -and $cmd.Contains($exe)) { Remove-ItemProperty $run -Name SayNote }
Remove-Item (Join-Path ([Environment]::GetFolderPath('Programs')) 'SayNote.lnk') -ErrorAction SilentlyContinue
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SayNote' -Recurse -ErrorAction SilentlyContinue

$data = Join-Path $env:LOCALAPPDATA 'SayNote'  # also where a recording goes when the notes folder can't be written: only known files go
foreach ($f in 'settings.json', 'settings.json.tmp', 'saynote.log') { Remove-Item (Join-Path $data $f) -ErrorAction SilentlyContinue }
if (-not (Get-ChildItem $data -Force -ErrorAction SilentlyContinue)) { Remove-Item $data -ErrorAction SilentlyContinue }

Set-Location $env:TEMP
Remove-Item $app -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "$app.old", (Join-Path $env:TEMP 'SayNote-update') -Recurse -Force -ErrorAction SilentlyContinue  # left by an update
if (-not $Quiet) {
    $notes = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'SayNote'
    (New-Object -ComObject WScript.Shell).Popup("SayNote was removed. Your notes are still in $notes.", 0, 'SayNote', 64) | Out-Null
}
