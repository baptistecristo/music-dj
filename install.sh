#!/usr/bin/env bash
# music-dj installer — macOS / Linux onboarding (see install.ps1 for Windows)
set -e

echo ""
echo "  MUSIC DJ — your AI DJ: it learns your taste, reads your mood, plays the music."
echo ""
echo "  Which music service do you use?"

services=(apple-music spotify soundcloud youtube-music deezer tidal amazon-music qobuz bandcamp pandora)
names=("Apple Music" "Spotify" "SoundCloud" "YouTube Music" "Deezer" "Tidal" "Amazon Music" "Qobuz" "Bandcamp" "Pandora (US only)")
for i in "${!names[@]}"; do printf "    [%d] %s\n" "$((i+1))" "${names[$i]}"; done
echo ""
read -rp "  Enter 1-${#services[@]}: " choice
idx=$((choice-1))
service="${services[$idx]}"
echo "  -> ${names[$idx]} it is."

echo ""
echo "  Which browser do you want the DJ to play in?"
echo "  (It needs the 'Claude in Chrome' extension installed there. All of these"
echo "   are Chromium-based, so they all take it from the Chrome Web Store.)"
browsers=(chrome edge brave arc opera vivaldi)
bnames=("Chrome" "Edge" "Brave" "Arc" "Opera" "Vivaldi")
for i in "${!bnames[@]}"; do printf "    [%d] %s\n" "$((i+1))" "${bnames[$i]}"; done
echo ""
read -rp "  Enter 1-${#browsers[@]}: " bchoice
bidx=$((bchoice-1))
browser="${browsers[$bidx]}"
echo "  -> DJ-ing in ${bnames[$bidx]}."

mkdir -p "$HOME/.music-dj"
config="$HOME/.music-dj/config.json"
python3 - "$service" "$browser" "$config" <<'EOF'
import json, sys
service, browser, path = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    cfg = json.load(open(path))
except Exception:
    cfg = {"enabled": True}
cfg["service"] = service
cfg["browser"] = browser
# Cleared on purpose: the agent binds a real deviceId on first run.
cfg["browser_device_id"] = ""
json.dump(cfg, open(path, "w"), indent=2)
EOF
echo "  Saved your choice to $config"

echo ""
if command -v claude >/dev/null 2>&1; then
    echo "  Installing the plugin into Claude Code..."
    # Install from GitHub, not the local clone, so future updates track the repo.
    marketplace="baptistecristo/music-dj"
    claude plugin marketplace add "$marketplace" 2>/dev/null || true
    claude plugin install music-dj@music-dj 2>/dev/null || {
        echo "  Automatic install didn't work — inside Claude Code run:"
        echo "    /plugin marketplace add $marketplace"
        echo "    /plugin install music-dj@music-dj"
    }
else
    echo "  Claude Code CLI not found. Install it first:"
    echo "    npm install -g @anthropic-ai/claude-code"
fi

echo ""
echo "  You'll also need ${bnames[$bidx]} with the 'Claude in Chrome' extension for"
echo "  playback control (macOS + Apple Music works natively without it)."
if [ "$browser" != "chrome" ]; then
    echo "  Install the extension in ${bnames[$bidx]} itself — having it in Chrome does"
    echo "  NOT make ${bnames[$bidx]} controllable."
fi
echo ""
echo "  Last step — open 'claude' and say:  set up my music DJ"
echo "  The agent scans your library, learns your taste, and saves the profile to"
echo "  ~/.music-dj/taste-profile.md (it never leaves your machine)."
echo ""
