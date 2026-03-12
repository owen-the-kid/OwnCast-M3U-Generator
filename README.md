# Owncast M3U Playlist

An automated M3U playlist generator for Owncast streams. This repository fetches every live and offline stream from the official Owncast directory, resolves channel logos so they display correctly in VLC, and commits an up-to-date playlist once a day.

## How to Add This Playlist to Your Media Player

### Direct Link

```
https://raw.githubusercontent.com/owen-the-kid/OwnCast-M3U-Generator/main/owncast.m3u
```

### VLC

1. Open VLC
2. Click **Media → Open Network Stream** (or press `Ctrl+N`)
3. Paste the URL above and click **Play**

### Kodi

1. Install the **PVR IPTV Simple Client** add-on
2. Go to **Settings → PVR & Live TV → PVR IPTV Simple Client → Configure**
3. Set **M3U playlist URL** to the URL above

### Jellyfin

1. Go to **Dashboard → Live TV**
2. Click **Add Tuner Device**
3. Select **M3U Tuner** and paste the URL above

### mpv

```bash
mpv https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/owncast.m3u
```

## Playlist Updates

This playlist is automatically updated once every day using GitHub Actions. The workflow fetches the latest streams from the Owncast directory and commits the updated `owncast.m3u` to this repository.

## Technical Details

- **Update Frequency**: Daily at midnight (UTC)
- **Content Source**: [Owncast Directory](https://directory.owncast.online)
- **Format**: Generic M3U with resolved logo URLs

## Setup (for your own copy)

1. Fork or create a new repository
2. Add the two files:
   - `.github/workflows/generate-m3u.yml`
   - `scripts/generate_m3u.py`
3. Go to **Settings → Actions → General → Workflow permissions** and enable **Read and write permissions**
4. Go to the **Actions** tab and run the workflow manually to generate the first playlist

## Troubleshooting

If the playlist isn't updating:

- Check the **Actions** tab to see if the workflow ran successfully
- Make sure **Read and write permissions** is enabled under Settings → Actions → General

## License

This project is not licensed and i will not license it because it is made by Claude (The AI by Anthropic)
