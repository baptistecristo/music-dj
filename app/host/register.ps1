# One-time setup: registers the native messaging host that lets the
# extension's toolbar icon start the DJ. Safe to re-run; needs no admin
# (everything lands under HKCU). Re-run it if this folder ever moves.
param([string]$ExtensionId = "leecnmkgmigdljlikhekgionlokgobna")
$ErrorActionPreference = "Stop"

$hostDir = $PSScriptRoot
# Get-Command alone is not proof of Python: with none installed, Windows
# still resolves "python" to the Microsoft Store alias stub. Only trust an
# interpreter that actually answers --version (same check as install.ps1).
$python = $null
foreach ($name in "python", "python3") {
    try {
        $v = & $name --version 2>$null
        if ($LASTEXITCODE -eq 0 -and "$v" -match "^Python 3") {
            $python = (Get-Command $name).Source
            break
        }
    } catch {}
}
if (-not $python) {
    throw "Python 3 not found on PATH (or only the Store stub). Install it, then re-run."
}

$bat = Join-Path $hostDir "launcher.bat"
"@echo off`r`n`"$python`" `"$hostDir\launcher.py`"" |
    Out-File $bat -Encoding ascii

# Chrome refuses a BOM in the host manifest, and Out-File utf8 writes one
# on Windows PowerShell 5.1 -- ASCII sidesteps it (paths with non-ASCII
# characters would need [IO.File]::WriteAllText with a BOM-less UTF8).
$manifestPath = Join-Path $hostDir "com.music_dj.launcher.json"
@{
    name            = "com.music_dj.launcher"
    description     = "Starts the music-dj daemon and overlay"
    path            = $bat
    type            = "stdio"
    allowed_origins = @("chrome-extension://$ExtensionId/")
} | ConvertTo-Json | Out-File $manifestPath -Encoding ascii

foreach ($root in "Software\Microsoft\Edge", "Software\Google\Chrome") {
    $key = "HKCU:\$root\NativeMessagingHosts\com.music_dj.launcher"
    New-Item -Path $key -Force | Out-Null
    Set-ItemProperty -Path $key -Name "(default)" -Value $manifestPath
}
Write-Host "Registered com.music_dj.launcher (extension $ExtensionId)"
Write-Host "Manifest: $manifestPath"
