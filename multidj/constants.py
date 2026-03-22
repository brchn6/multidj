from __future__ import annotations

import re

# Genres that carry no meaningful classification information.
# These are typically YouTube category names scraped into music metadata.
UNINFORMATIVE_GENRES: frozenset[str] = frozenset({
    "music",
    "people & blogs",
    "entertainment",
    "comedy",
    "sports",
    "travel & events",
    "film & animation",
    "gaming",
    "education",
    "howto & style",
    "news & politics",
    "science & technology",
    "autos & vehicles",
    "pets & animals",
    "nonprofits & activism",
})

# Matches strings composed entirely of emoji / non-word symbols.
EMOJI_OR_SYMBOL_RE = re.compile(r"^[^\w\s]+$", re.UNICODE)

# Crate names that are auto-generated (not hand-curated).
# Matched against the start of the crate name.
AUTO_CRATE_PREFIXES = re.compile(r"^(Genre:\s|BPM:\s|Lang:\s)", re.IGNORECASE)

# Crate names that are special catch-alls (not meaningful collections).
CATCH_ALL_CRATE_NAMES: frozenset[str] = frozenset({"New Crate"})

# Camelot/audio-analysis tags appended by some tagging tools.
# Matches: [11B][E0.87][D0.54][][L - 6.5dB]  or  [10A][E0.81][D0.85][][L - 8.3dB]
CAMELOT_SUFFIX_RE = re.compile(
    r"\s*[-–]?\s*\[[0-9]{1,2}[AB]\](\[E[0-9.]+\])?(\[D[0-9.]+\])?\[\]\[L\s*-\s*[0-9.]+dB\]",
    re.IGNORECASE,
)

# Numeric or marketing junk that prefixes many filenames.
# Matches: "2 - ", "100 x ", "(BUY =) ", "[FREE DOWNLOAD] ", "#DKMIX "
# Note: bare "\d+\s+" is intentionally excluded — it would corrupt artist names
# like "50 Cent" or "2Pac". Only strip numbers when followed by an explicit
# separator (dash or x), a parenthesised tag, a bracketed tag, or a hash prefix.
NOISE_PREFIX_RE = re.compile(
    r"^(?:\d+\s*[-x]\s+|\([^)]*\)\s*|\[[^\]]*\]\s*|#\S+\s*)+",
    re.IGNORECASE,
)

# Duplicate-copy suffix: "Song.1.mp3" → strip ".1" before extension.
DUPLICATE_SUFFIX_RE = re.compile(r"\.\d+$")

# Auto-crate prefixes for rebuild operations (superset of AUTO_CRATE_PREFIXES).
# Matches Genre:, BPM:, Lang: prefixes (case-insensitive).
REBUILD_CRATE_RE = re.compile(r"^(Genre:\s|BPM:\s|Lang:\s)", re.IGNORECASE)
