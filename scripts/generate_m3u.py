"""
generate_m3u.py
Downloads the Owncast directory IPTV playlist, merges in any custom channels
from custom_channels.json, resolves logo URLs so they display correctly in
VLC, and writes:

  owncast.m3u              - full playlist (every channel, grouped by category)
  categories/<tag>.m3u     - one playlist per tag/category (min. 2 channels)

Custom channels are defined in custom_channels.json at the repo root:

  [
    {
      "name": "My Channel",
      "url":  "https://example.com/stream.m3u8",
      "logo": "https://example.com/logo.png",
      "tags": ["news", "english"]
    }
  ]

All fields except "name" and "url" are optional.

Requirements:
    pip install requests

Usage:
    python generate_m3u.py
"""

import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests

DIRECTORY_IPTV_URL   = "https://directory.owncast.online/api/iptv"
OUTPUT_FILE          = Path("owncast.m3u")
CATEGORIES_DIR       = Path("categories")
CUSTOM_CHANNELS_FILE = Path("custom_channels.json")
IMAGE_EXTENSIONS     = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
TVG_LOGO_RE          = re.compile(r'tvg-logo=["\']([^"\']*)["\']', re.IGNORECASE)
TVG_TAGS_RE          = re.compile(r'tvg-tags=["\']([^"\']*)["\']', re.IGNORECASE)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_playlist() -> str:
    log.info("Fetching playlist from %s", DIRECTORY_IPTV_URL)
    response = requests.get(DIRECTORY_IPTV_URL, timeout=15)
    response.raise_for_status()
    return response.text


# ---------------------------------------------------------------------------
# Parse Owncast M3U
# ---------------------------------------------------------------------------

def parse_entries(raw: str) -> list:
    """
    Parse the API's non-standard 3-line M3U into a list of dicts:
        {"name": str, "logo": str, "tags": [str, ...], "url": str}
    """
    lines   = raw.splitlines()
    entries = []
    i       = 0

    while i < len(lines):
        line = lines[i].strip()

        if not line.startswith("#EXTINF"):
            i += 1
            continue

        # Format A: bare "#EXTINF:-1," with attrs on the next line
        if re.match(r"^#EXTINF:-1\s*,\s*$", line) and i + 1 < len(lines):
            attr_line  = lines[i + 1].strip()
            url_line   = lines[i + 2].strip() if i + 2 < len(lines) else ""
            last_comma = attr_line.rfind(",")
            name       = attr_line[last_comma + 1:].strip() if last_comma != -1 else attr_line
            logo_match = TVG_LOGO_RE.search(attr_line)
            logo       = logo_match.group(1).strip() if logo_match else ""
            tags_match = TVG_TAGS_RE.search(attr_line)
            tags       = [t.strip() for t in tags_match.group(1).split(",")] if tags_match else []
            tags       = [t for t in tags if t]
            if url_line.startswith("http"):
                entries.append({"name": name, "logo": logo, "tags": tags, "url": url_line})
            i += 3
            continue

        # Format B: attrs already on the #EXTINF line
        last_comma = line.rfind(",")
        if last_comma != -1:
            name       = line[last_comma + 1:].strip()
            logo_match = TVG_LOGO_RE.search(line)
            logo       = logo_match.group(1).strip() if logo_match else ""
            tags_match = TVG_TAGS_RE.search(line)
            tags       = [t.strip() for t in tags_match.group(1).split(",")] if tags_match else []
            tags       = [t for t in tags if t]
            url_line   = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if url_line.startswith("http"):
                entries.append({"name": name, "logo": logo, "tags": tags, "url": url_line})
            i += 2
            continue

        i += 1

    return entries


# ---------------------------------------------------------------------------
# Load custom channels
# ---------------------------------------------------------------------------

def load_custom_channels() -> list:
    """
    Load channels from custom_channels.json if it exists.

    Expected format:
        [
          {
            "name": "My Channel",           (required)
            "url":  "https://...",           (required)
            "logo": "https://...",           (optional)
            "tags": ["news", "english"]      (optional)
          }
        ]
    """
    if not CUSTOM_CHANNELS_FILE.exists():
        log.info("No %s found, skipping custom channels", CUSTOM_CHANNELS_FILE)
        return []

    try:
        data = json.loads(CUSTOM_CHANNELS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read %s: %s — skipping custom channels", CUSTOM_CHANNELS_FILE, exc)
        return []

    channels = []
    for item in data:
        name = item.get("name", "").strip()
        url  = item.get("url",  "").strip()
        if not name or not url:
            log.warning("Skipping custom channel missing name or url: %s", item)
            continue
        channels.append({
            "name": name,
            "url":  url,
            "logo": item.get("logo", "").strip(),
            "tags": [t.strip() for t in item.get("tags", []) if str(t).strip()],
        })

    log.info("Loaded %d custom channel(s) from %s", len(channels), CUSTOM_CHANNELS_FILE)
    return channels


# ---------------------------------------------------------------------------
# Logo resolution
# ---------------------------------------------------------------------------

def resolve_logo(logo_url: str) -> str:
    """Follow redirects on a logo URL to get the final image URL."""
    if not logo_url:
        return ""
    if any(urlparse(logo_url).path.lower().endswith(ext) for ext in IMAGE_EXTENSIONS):
        return logo_url
    try:
        response = requests.head(logo_url, timeout=10, allow_redirects=True)
        return response.url
    except Exception:
        return logo_url


def resolve_all_logos(entries: list) -> dict:
    """Resolve all logo URLs concurrently. Returns {original: resolved}."""
    unique = list(set(e["logo"] for e in entries if e["logo"]))
    log.info("Resolving %d logo URLs...", len(unique))
    results = {}
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(resolve_logo, url): url for url in unique}
        for i, future in enumerate(as_completed(futures), 1):
            results[futures[future]] = future.result()
            if i % 10 == 0 or i == len(unique):
                log.info("  %d / %d done", i, len(unique))
    return results


# ---------------------------------------------------------------------------
# M3U builder
# ---------------------------------------------------------------------------

def build_m3u(entries: list, logo_map: dict, include_groups: bool = False) -> str:
    """Build a clean M3U string from a list of entries."""
    lines = ["#EXTM3U", ""]
    for entry in entries:
        logo  = logo_map.get(entry["logo"], entry["logo"])
        group = entry["tags"][0] if entry["tags"] else "Uncategorized"
        attrs = ""
        if logo:
            attrs += f' tvg-logo="{logo}"'
        if include_groups:
            attrs += f' group-title="{group}"'
        lines.append(f'#EXTINF:-1{attrs},{entry["name"]}')
        lines.append(entry["url"])
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save(content: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    log.info("Saved %s (%d channels)", path, content.count("#EXTINF"))


# ---------------------------------------------------------------------------
# Category helpers
# ---------------------------------------------------------------------------

def safe_filename(tag: str) -> str:
    name = tag.lower().strip()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s]+", "-", name)
    return name or "uncategorized"


def group_by_tag(entries: list) -> dict:
    groups = {}
    for entry in entries:
        tags = entry["tags"] if entry["tags"] else ["uncategorized"]
        for tag in tags:
            groups.setdefault(tag, []).append(entry)
    return groups


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    try:
        raw = fetch_playlist()
    except requests.RequestException as exc:
        log.error("Failed to fetch playlist: %s", exc)
        sys.exit(1)

    if not raw.strip():
        log.error("Empty playlist received – aborting.")
        sys.exit(1)

    entries = parse_entries(raw)
    log.info("Parsed %d Owncast channels", len(entries))

    custom = load_custom_channels()
    entries = entries + custom
    if custom:
        log.info("Total channels after merge: %d", len(entries))

    logo_map = resolve_all_logos(entries)

    # --- Main playlist (sorted by category, with group-title) ---
    grouped_entries = sorted(
        entries,
        key=lambda e: (e["tags"][0].lower() if e["tags"] else "zzz", e["name"].lower())
    )
    save(build_m3u(grouped_entries, logo_map, include_groups=True), OUTPUT_FILE)

    # --- Per-category playlists ---
    groups  = group_by_tag(entries)
    skipped = [tag for tag, es in groups.items() if len(es) < 2]
    groups  = {tag: es for tag, es in groups.items() if len(es) >= 2}
    log.info("Generating %d category playlists (%d skipped, only 1 channel)...", len(groups), len(skipped))
    for tag, tag_entries in sorted(groups.items()):
        save(build_m3u(tag_entries, logo_map), CATEGORIES_DIR / (safe_filename(tag) + ".m3u"))

    log.info("Done. %d categories written to %s/", len(groups), CATEGORIES_DIR)


if __name__ == "__main__":
    main()
