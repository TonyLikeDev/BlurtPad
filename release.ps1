# Publish this version of SayNote as a GitHub release: every installed SayNote then offers it as an update.
# 1. Raise VERSION in saynote.py   2. .\build.ps1   3. .\release.ps1 "What changed, in a line or two"
# Uses the GitHub CLI (gh) when it's installed and signed in; otherwise it says what to upload on github.com.
param([string]$Notes = '')
Set-Location $PSScriptRoot
$ErrorActionPreference = 'Stop'
$version = (Select-String saynote.py -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value
$repo = (Select-String saynote.py -Pattern '^REPO = "(.*)"').Matches[0].Groups[1].Value
if (-not $repo) { throw 'Set REPO in saynote.py to your GitHub repo (owner/name) first, then .\build.ps1' }
if (-not (Test-Path dist\SayNote\SayNote.exe)) { throw 'Run .\build.ps1 first' }
$changed = Get-ChildItem saynote.py, update.ps1, uninstall.ps1, ui\* | Where-Object LastWriteTime -gt (Get-Item dist\SayNote\SayNote.exe).LastWriteTime
if ($changed) { throw "Changed since the last build: $($changed.Name -join ', '). Run .\build.ps1 first" }

$zip = "SayNote-$version.zip"  # the name SayNote looks for
Remove-Item $zip -ErrorAction SilentlyContinue
& .\.venv\Scripts\python.exe -m zipfile -c $zip dist\SayNote  # one folder, SayNote\, inside
if ($LASTEXITCODE) { throw 'Zipping failed' }
"Made $zip ($([int]((Get-Item $zip).Length / 1MB)) MB)"

if (Get-Command gh -ErrorAction SilentlyContinue) {
    gh release create "v$version" $zip --repo $repo --title "SayNote $version" --notes $Notes
    if ($LASTEXITCODE) { throw 'gh release create failed' }
    "Published https://github.com/$repo/releases/tag/v$version"
} else {
    "Now publish it on https://github.com/$repo/releases/new :"
    "  tag: v$version    title: SayNote $version    description: what changed (SayNote shows it)    attach: $zip    then Publish release"
}
