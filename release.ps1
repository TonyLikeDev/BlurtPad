# Publish this version of SayNote as a GitHub release: people download its installer, and every installed SayNote offers it as an update.
# 1. Raise VERSION in saynote.py   2. .\build.ps1   3. .\release.ps1 "What changed, in a line or two"
# Uses the GitHub CLI (gh) when it's installed and signed in; otherwise it says what to upload on github.com.
param([string]$Notes = '')
Set-Location $PSScriptRoot
$ErrorActionPreference = 'Stop'
$version = (Select-String saynote.py -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value
$repo = (Select-String saynote.py -Pattern '^REPO = "(.*)"').Matches[0].Groups[1].Value
if (-not $repo) { throw 'Set REPO in saynote.py to your GitHub repo (owner/name) first, then .\build.ps1' }
$setup = "dist\SayNote-Setup-$version.exe"  # the name SayNote looks for
if (-not (Test-Path $setup)) { throw 'Run .\build.ps1 first' }
$changed = Get-ChildItem saynote.py, SayNote.iss, ui\* | Where-Object LastWriteTime -gt (Get-Item $setup).LastWriteTime
if ($changed) { throw "Changed since the last build: $($changed.Name -join ', '). Run .\build.ps1 first" }

if (Get-Command gh -ErrorAction SilentlyContinue) {
    gh release create "v$version" $setup --repo $repo --title "SayNote $version" --notes $Notes
    if ($LASTEXITCODE) { throw 'gh release create failed' }
    "Published https://github.com/$repo/releases/tag/v$version"
} else {
    "Publish it on https://github.com/$repo/releases/new :"
    "  tag: v$version    title: SayNote $version    description: what changed (SayNote shows it)"
    "  attach: $PSScriptRoot\$setup ($([int]((Get-Item $setup).Length / 1MB)) MB), wait for the upload to finish, then Publish release"
}
