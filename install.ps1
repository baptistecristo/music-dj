# music-dj installer — Windows PowerShell onboarding
# Run from the repo root:  .\install.ps1
$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "  ___  ___             _        ______  ___ " -ForegroundColor Magenta
Write-Host "  |  \/  |            (_)       |  _  \|_  |" -ForegroundColor Magenta
Write-Host "  | .  . | _   _  ___  _   ___  | | | |  | |" -ForegroundColor Magenta
Write-Host "  | |\/| || | | |/ __|| | / __| | | | |  | |" -ForegroundColor Magenta
Write-Host "  | |  | || |_| |\__ \| || (__  | |/ /\__/ /" -ForegroundColor Magenta
Write-Host "  \_|  |_/ \__,_||___/|_| \___| |___/\____/ " -ForegroundColor Magenta
Write-Host ""
Write-Host "  Your AI DJ: it learns your taste, reads your mood, plays the music." -ForegroundColor Gray
Write-Host ""

# ----------------------------------------------------------- pick a service
$services = @(
    @{ id = "apple-music";   name = "Apple Music";   note = "web player (music.apple.com) - no API key needed" },
    @{ id = "spotify";       name = "Spotify";       note = "web player, or optional Web API mode (Premium required)" },
    @{ id = "soundcloud";    name = "SoundCloud";    note = "web player (soundcloud.com)" },
    @{ id = "youtube-music"; name = "YouTube Music"; note = "web player (music.youtube.com)" },
    @{ id = "deezer";        name = "Deezer";        note = "web player + Flow (deezer.com)" },
    @{ id = "tidal";         name = "Tidal";         note = "web player (listen.tidal.com)" },
    @{ id = "amazon-music";  name = "Amazon Music";  note = "web player (music.amazon.com)" },
    @{ id = "qobuz";         name = "Qobuz";         note = "web player (play.qobuz.com)" },
    @{ id = "bandcamp";      name = "Bandcamp";      note = "streams free, purchases support artists" },
    @{ id = "pandora";       name = "Pandora";       note = "station-based (pandora.com, US only)" }
)

Write-Host "  Which music service do you use?" -ForegroundColor Cyan
for ($i = 0; $i -lt $services.Count; $i++) {
    Write-Host ("    [{0}] {1,-14} {2}" -f ($i + 1), $services[$i].name, $services[$i].note)
}
Write-Host ""
do {
    $choice = Read-Host "  Enter 1-$($services.Count)"
} until ($choice -match '^\d+$' -and [int]$choice -ge 1 -and [int]$choice -le $services.Count)
$service = $services[[int]$choice - 1]
Write-Host ""
Write-Host ("  -> {0} it is." -f $service.name) -ForegroundColor Green

# ----------------------------------------------------------- pick a browser
$browsers = @(
    @{ id = "chrome";   name = "Chrome" },
    @{ id = "edge";     name = "Edge" },
    @{ id = "brave";    name = "Brave" },
    @{ id = "arc";      name = "Arc" },
    @{ id = "opera";    name = "Opera" },
    @{ id = "vivaldi";  name = "Vivaldi" }
)

Write-Host ""
Write-Host "  Which browser do you want the DJ to play in?" -ForegroundColor Cyan
Write-Host "  (It needs the 'Claude in Chrome' extension installed there. All of" -ForegroundColor Gray
Write-Host "   these are Chromium-based, so they all take it from the Chrome Web Store.)" -ForegroundColor Gray
for ($i = 0; $i -lt $browsers.Count; $i++) {
    Write-Host ("    [{0}] {1}" -f ($i + 1), $browsers[$i].name)
}
Write-Host ""
do {
    $bchoice = Read-Host "  Enter 1-$($browsers.Count)"
} until ($bchoice -match '^\d+$' -and [int]$bchoice -ge 1 -and [int]$bchoice -le $browsers.Count)
$browser = $browsers[[int]$bchoice - 1]
Write-Host ""
Write-Host ("  -> DJ-ing in {0}." -f $browser.name) -ForegroundColor Green

# ----------------------------------------------------------- write config
$configDir = Join-Path $env:USERPROFILE ".music-dj"
New-Item -ItemType Directory -Force -Path $configDir | Out-Null
$configPath = Join-Path $configDir "config.json"
if (Test-Path $configPath) {
    $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
} else {
    $cfg = [pscustomobject]@{ enabled = $true }
}
$cfg | Add-Member -NotePropertyName service -NotePropertyValue $service.id -Force
$cfg | Add-Member -NotePropertyName browser -NotePropertyValue $browser.id -Force
# Cleared on purpose: the agent binds a real deviceId on first run.
$cfg | Add-Member -NotePropertyName browser_device_id -NotePropertyValue "" -Force
# WriteAllText with UTF8Encoding($false) = UTF-8 with NO BOM. Set-Content
# -Encoding UTF8 emits a BOM on Windows PowerShell 5.1, which breaks the
# plugin's Python-side json.load.
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($configPath, ($cfg | ConvertTo-Json -Depth 5), $utf8NoBom)
Write-Host ("  Saved your choice to {0}" -f $configPath) -ForegroundColor Gray

# ----------------------------------------------------------- prerequisites
Write-Host ""
Write-Host "  Checking prerequisites..." -ForegroundColor Cyan
$claudeOk = [bool](Get-Command claude -ErrorAction SilentlyContinue)
if ($claudeOk) {
    Write-Host "    [ok] Claude Code CLI found"
} else {
    Write-Host "    [!!] Claude Code CLI not found." -ForegroundColor Yellow
    Write-Host "         Install it first:  npm install -g @anthropic-ai/claude-code"
    Write-Host "         (needs Node.js: https://nodejs.org)"
}
# The hooks/MCP server run "python3", with a fallback to "python". On Windows,
# "python3" is often the Microsoft Store alias stub (python.org installs only
# provide python.exe), which fails silently — so verify a real Python answers.
$py3Ok = $false
try { $v = & python3 --version 2>$null; $py3Ok = ($LASTEXITCODE -eq 0 -and "$v" -match "^Python 3") } catch {}
$pyOk = $false
if (-not $py3Ok) {
    try { $v = & python --version 2>$null; $pyOk = ($LASTEXITCODE -eq 0 -and "$v" -match "^Python 3") } catch {}
}
if ($py3Ok) {
    Write-Host "    [ok] Python 3 found (python3)"
} elseif ($pyOk) {
    Write-Host "    [ok] Python 3 found (python) - the DJ's hooks will use it automatically"
} else {
    Write-Host "    [!!] Python 3 not found (or 'python3' is the Microsoft Store stub)." -ForegroundColor Yellow
    Write-Host "         The mood-detection hooks need it. Install Python 3 from"
    Write-Host "         https://www.python.org/downloads/ (check 'Add python.exe to PATH')"
    Write-Host "         or from the Microsoft Store, then re-run this installer."
}
Write-Host ("    [--] {0} + the 'Claude in Chrome' extension are needed for playback control." -f $browser.name)
Write-Host "         Get it from the Chrome Web Store, sign in with your Claude account."
if ($browser.id -ne "chrome") {
    Write-Host ("         Install it in {0} specifically - an extension in Chrome does NOT" -f $browser.name) -ForegroundColor Yellow
    Write-Host ("         make {0} controllable. Open the Chrome Web Store in {0} itself." -f $browser.name) -ForegroundColor Yellow
}

# ----------------------------------------------------------- install plugin
$marketplace = "baptistecristo/music-dj"
if ($claudeOk) {
    Write-Host ""
    Write-Host "  Installing the plugin into Claude Code..." -ForegroundColor Cyan
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    # Install from GitHub, not $repoRoot, so future updates track the repo.
    claude plugin marketplace add $marketplace *> $null
    $marketplaceOk = ($LASTEXITCODE -eq 0)
    claude plugin install music-dj@music-dj *> $null
    $installOk = $marketplaceOk -and ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEAP
    if ($installOk) {
        Write-Host "    [ok] Plugin installed." -ForegroundColor Green
    } else {
        Write-Host "    [!!] Automatic install didn't work - do it inside Claude Code instead:" -ForegroundColor Yellow
        Write-Host ("         /plugin marketplace add {0}" -f $marketplace)
        Write-Host "         /plugin install music-dj@music-dj"
    }
} else {
    Write-Host ""
    Write-Host "  After installing Claude Code, run inside it:" -ForegroundColor Yellow
    Write-Host ("    /plugin marketplace add {0}" -f $marketplace)
    Write-Host "    /plugin install music-dj@music-dj"
}

# ----------------------------------------------------------- per-service guide
Write-Host ""
Write-Host ("  ---- Getting {0} ready ----" -f $service.name) -ForegroundColor Cyan
switch ($service.id) {
    "apple-music" {
        Write-Host ("  1. Open https://music.apple.com in {0} and sign in with the Apple ID" -f $browser.name)
        Write-Host "     that has your Apple Music subscription."
        Write-Host "  2. Click play on any song once (unlocks browser audio + confirms the"
        Write-Host "     player is not in PREVIEW mode)."
        Write-Host "  3. No API keys needed. The DJ uses the web player directly."
    }
    "spotify" {
        Write-Host ("  1. Open https://open.spotify.com in {0} and sign in (Premium needed" -f $browser.name)
        Write-Host "     for on-demand playback). Click play on any song once."
        Write-Host "  2. That's enough for browser mode. OPTIONAL - API mode (controls the"
        Write-Host "     desktop app too, no browser tab needed):"
        Write-Host "       - Create an app at https://developer.spotify.com/dashboard"
        Write-Host "       - Redirect URI: http://127.0.0.1:8888/callback"
        Write-Host "       - Put client_id/client_secret in ~\.music-dj\spotify.json"
        Write-Host "       - The DJ will walk you through the one-time authorization."
    }
    "soundcloud" {
        Write-Host ("  1. Open https://soundcloud.com in {0} and sign in." -f $browser.name)
        Write-Host "  2. Click play on any track once. Like some tracks / follow artists if"
        Write-Host "     your library is empty - that's what the DJ learns from."
        Write-Host "  3. No API keys needed."
    }
    "youtube-music" {
        Write-Host ("  1. Open https://music.youtube.com in {0} and sign in with Google." -f $browser.name)
        Write-Host "  2. Click play on any song once. (Free tier works; Premium removes ads.)"
        Write-Host "  3. No API keys needed."
    }
    default {
        $urls = @{ "deezer" = "https://www.deezer.com"; "tidal" = "https://listen.tidal.com";
                   "amazon-music" = "https://music.amazon.com"; "qobuz" = "https://play.qobuz.com";
                   "bandcamp" = "https://bandcamp.com"; "pandora" = "https://www.pandora.com" }
        Write-Host ("  1. Open {0} in {1} and sign in." -f $urls[$service.id], $browser.name)
        Write-Host "  2. Click play on anything once (unlocks browser audio)."
        Write-Host "  3. No API keys needed - the DJ drives the web player directly."
        if ($service.id -eq "pandora") { Write-Host "     Note: Pandora is US-only." }
        if ($service.id -eq "bandcamp") { Write-Host "     Note: the DJ will queue albums; buying tracks supports the artists." }
    }
}

# ----------------------------------------------------------- next steps
Write-Host ""
Write-Host "  ---- Last step: let the DJ learn your taste ----" -ForegroundColor Cyan
Write-Host "  Open a terminal, run 'claude', and say:"
Write-Host ""
Write-Host "      set up my music DJ" -ForegroundColor Magenta
Write-Host ""
Write-Host "  The agent will open your service in a tab named 'DJ', scan your whole"
Write-Host "  library (artists, genres, recent plays), learn your taste, and save the"
Write-Host "  profile to ~\.music-dj\taste-profile.md - it never leaves your machine."
Write-Host ""
Write-Host "  From then on, in any Claude session: 'play something', 'calmer please',"
Write-Host "  'skip', 'what's playing?' - or just work, and let the DJ read the room."
Write-Host ""
