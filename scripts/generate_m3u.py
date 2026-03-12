"""
generate_m3u.py
Downloads the Owncast directory IPTV playlist, resolves logo URLs so they
display correctly in VLC, and writes:

  owncast.m3u              - full playlist (every channel)
  categories/<tag>.m3u     - one playlist per tag/category

Requirements:
    pip install requests

Usage:
    python generate_m3u.py
"""

import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests

DIRECTORY_IPTV_URL = "https://directory.owncast.online/api/iptv"
OUTPUT_FILE        = Path("owncast.m3u")
CATEGORIES_DIR     = Path("categories")
IMAGE_EXTENSIONS   = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
TVG_LOGO_RE        = re.compile(r'tvg-logo=["\']([^"\']*)["\']', re.IGNORECASE)
TVG_TAGS_RE        = re.compile(r'tvg-tags=["\']([^"\']*)["\']', re.IGNORECASE)

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
# Parse
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
            tags       = [t for t in tags if t]  # drop empty strings
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

def build_m3u(entries: list, logo_map: dict) -> str:
    """Build a clean M3U string from a list of entries."""
    lines = ["#EXTM3U", ""]
    for entry in entries:
        logo = logo_map.get(entry["logo"], entry["logo"])
        if logo:
            lines.append(f'#EXTINF:-1 tvg-logo="{logo}",{entry["name"]}')
        else:
            lines.append(f'#EXTINF:-1,{entry["name"]}')
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
    """Convert a tag string into a safe filename."""
    name = tag.lower().strip()
    name = re.sub(r"[^\w\s-]", "", name)   # remove special chars
    name = re.sub(r"[\s]+", "-", name)      # spaces to hyphens
    return name or "uncategorized"


def group_by_tag(entries: list) -> dict:
    """
    Return a dict mapping tag → [entries].
    Entries with no tags go into 'uncategorized'.
    An entry can appear in multiple categories if it has multiple tags.
    """
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
    log.info("Parsed %d channels", len(entries))

    logo_map = resolve_all_logos(entries)

    # --- Main playlist ---
    save(build_m3u(entries, logo_map), OUTPUT_FILE)

    # --- Per-category playlists ---
    groups = group_by_tag(entries)
    skipped = [tag for tag, tag_entries in groups.items() if len(tag_entries) < 2]
    groups  = {tag: tag_entries for tag, tag_entries in groups.items() if len(tag_entries) >= 2}
    log.info("Generating %d category playlists (%d skipped, only 1 channel)...", len(groups), len(skipped))
    for tag, tag_entries in sorted(groups.items()):
        filename = safe_filename(tag) + ".m3u"
        save(build_m3u(tag_entries, logo_map), CATEGORIES_DIR / filename)

    log.info("Done. %d categories written to %s/", len(groups), CATEGORIES_DIR)


if __name__ == "__main__":
    main()
