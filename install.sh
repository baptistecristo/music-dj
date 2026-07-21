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

mkdir -p "$HOME/.music-dj"
config="$HOME/.music-dj/config.json"
python3 - "$service" "$config" <<'EOF'
import json, sys
service, path = sys.argv[1], sys.argv[2]
try:
    cfg = json.load(open(path))
except Exception:
    cfg = {"enabled": True}
cfg["service"] = service
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
echo "  You'll also need Chrome with the 'Claude in Chrome' extension for playback"
echo "  control (macOS + Apple Music works natively without it)."
echo ""
echo "  Last step — open 'claude' and say:  set up my music DJ"
echo "  The agent scans your library, learns your taste, and saves the profile to"
echo "  ~/.music-dj/taste-profile.md (it never leaves your machine)."
echo ""
