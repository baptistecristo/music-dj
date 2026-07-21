"""Moods, and the seed artists behind them.

Two vocabularies exist and neither is going away:

- The plugin hook classifies activity into coding / writing / debugging /
  building / research and writes that to state.json.
- taste-profile.md organises seed artists by feel: energized, tense, focus,
  mellow, loose.

The daemon speaks the second, because that is what the picks come from. LANE_OF
is the bridge. Keeping both means the hook keeps working untouched and the
profile stays the single source of musical truth.
"""

import re

PLUGIN_MOODS = ["coding", "writing", "debugging", "building", "research"]
LANES = ["energized", "tense", "focus", "mellow", "loose"]

LANE_OF = {
    "building": "energized",   # shipping something
    "debugging": "tense",      # warm slow soul, never aggressive
    "coding": "focus",         # low-vocal, instrumental-leaning
    "research": "focus",       # reading docs wants the same register
    "writing": "mellow",       # chanson and soft songwriting
}

# The profile labels a lane "locked in / deep focus"; we call it focus.
_LABEL_ALIASES = {"locked in": "focus", "locked-in": "focus"}

DEFAULT_LANE = "focus"


def lane_for(mood):
    """Plugin mood (or a lane name straight through) -> lane."""
    if not mood:
        return DEFAULT_LANE
    mood = str(mood).strip().lower()
    if mood in LANES:
        return mood
    return LANE_OF.get(mood, DEFAULT_LANE)


# --------------------------------------------------------------- profile seeds

_PARENS = re.compile(r"\([^)]*\)")
_EMPHASIS = re.compile(r"[*_`]")


def _clean_clause(clause):
    """Strip the prose lead-in from a clause, leaving just the artist list.

    'warm slow soul and blues, never aggressive: Bill Withers, Al Green'
        -> 'Bill Withers, Al Green'
    'or upbeat soul - Earth, Wind & Fire, Black Pumas'
        -> 'Earth, Wind & Fire, Black Pumas'
    """
    clause = _PARENS.sub("", clause)
    clause = clause.strip()
    if clause.lower().startswith("or "):
        clause = clause[3:]
    if ":" in clause:
        clause = clause.split(":", 1)[1]
    for dash in ("—", "–", " - "):
        if dash in clause:
            clause = clause.split(dash, 1)[1]
    return clause.strip()


# Bands whose own name contains a comma. There is no way to tell
# "Earth, Wind & Fire" (one band) from "Dabeull, Polo & Pan" (two artists) by
# shape alone -- both are [one word], [X & Y] -- so the ambiguous ones are
# listed rather than guessed at.
COMMA_NAMES = [
    "Earth, Wind & Fire",
    "Blood, Sweat & Tears",
    "Crosby, Stills, Nash & Young",
    "Crosby, Stills & Nash",
    "Emerson, Lake & Palmer",
    "Peter, Paul and Mary",
]

_PLACEHOLDER = "\x00%d\x00"


def _split_artists(text):
    """Comma-split into artist names, protecting names that contain a comma."""
    for i, name in enumerate(COMMA_NAMES):
        if name.lower() in text.lower():
            # Case-insensitive replace, preserving the canonical spelling.
            text = re.sub(re.escape(name), _PLACEHOLDER % i, text,
                          flags=re.IGNORECASE)

    parts = [p.strip(" .;…") for p in text.split(",")]
    parts = [_EMPHASIS.sub("", p).strip() for p in parts]

    restored = []
    for part in parts:
        for i, name in enumerate(COMMA_NAMES):
            part = part.replace(_PLACEHOLDER % i, name)
        if part:
            restored.append(part)

    out = []
    for name in restored:
        # Drop leftovers that are prose, not something worth searching.
        if len(name) < 3 or name.lower() in ("etc", "and", "or"):
            continue
        if name not in out:
            out.append(name)
    return out


def parse_seeds(profile_text):
    """taste-profile.md -> {lane: [artist, ...]}.

    Reads the 'Mood -> seed directions' bullets. Returns {} if the section is
    missing or unrecognisable, which the caller treats as "no seeds" rather
    than crashing -- a hand-edited profile must not take the music down.
    """
    seeds = {}
    if not profile_text:
        return seeds

    # Join each bullet into one string before parsing. The bullets wrap across
    # lines, and a lead-in like "warm slow soul, never aggressive:" can straddle
    # the break -- parsing line by line leaks that prose in as an artist.
    bullets = []
    in_section = False
    for raw in profile_text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            in_section = "seed direction" in line.lower()
            continue
        if not in_section:
            continue
        if line.lstrip().startswith("- "):
            bullets.append(line.strip()[2:])
        elif line.startswith((" ", "\t")) and line.strip() and bullets:
            bullets[-1] += " " + line.strip()

    for bullet in bullets:
        m = re.match(r"\*\*(.+?)\*\*\s*(?:→|->)\s*(.*)", bullet.strip())
        if not m:
            continue
        label = m.group(1).split("/")[0].strip().lower()
        lane = _LABEL_ALIASES.get(label, label)
        found = []
        for clause in m.group(2).split(";"):
            for artist in _split_artists(_clean_clause(clause)):
                if artist not in found:
                    found.append(artist)
        if found:
            seeds[lane] = found

    return seeds
