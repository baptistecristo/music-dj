# Kept as a shortcut: the registration itself moved to register.py, which
# covers macOS and Linux as well and knows about more browsers than the two
# this used to write. Same job, same lack of admin rights, still safe to
# re-run whenever this folder moves.
param([string]$ExtensionId = "")
$ErrorActionPreference = "Stop"

# Get-Command alone is not proof of Python: with none installed, Windows still
# resolves "python" to the Microsoft Store alias stub. Only trust one that
# actually answers --version (same check as install.ps1).
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

$script = Join-Path $PSScriptRoot "register.py"
if ($ExtensionId) {
    & $python $script --extension-id $ExtensionId
} else {
    & $python $script
}
