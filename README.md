# Kodi M4 Sport live plugin

Kodi **video source plugin** for watching **M4 Sport live** from Mediaklikk with automatic URL resolution.

## What this plugin is

`plugin.video.m4sport` provides a Kodi source at:

- `plugin://plugin.video.m4sport`

When opened, it exposes a playable item for **M4 Sport (Live)** and resolves the current stream URL dynamically at play time.

## Installation

You must create a .zip file from the `plugin.video.m4sport` directory, then install that in Kodi.

1. Create a zip archive where the top-level folder in the archive is `plugin.video.m4sport/` and it contains `addon.xml`.
2. In Kodi, go to **Settings -> Add-ons -> Install from zip file**.
3. Select your created ZIP file.
4. After installation, open the plugin at **Add-ons -> Video add-ons -> M4 Sport Live Source** or
   **Videos -> Video add-ons -> M4 Sport Live Source**.
5. Start playback by selecting **M4 Sport (Live)**.

## How it works

`plugin.video.m4sport` flow:

1. User opens the plugin in Kodi.
2. Plugin shows one playable item: **M4 Sport (Live)**.
3. On play, it fetches the live page (`https://mediaklikk.hu/elo/mtv4live/`).
4. It extracts `streamId` from the page.
5. It calls the Mediaklikk player endpoint with the required `sourceUrl` + `Referer`.
6. It extracts the final `.m3u8` (or `.mpd`) URL from the player response.
7. It returns that URL to Kodi via `setResolvedUrl`, and Kodi starts playback.

This means no hardcoded permanent stream URL is needed; each playback resolves a fresh URL.

## Key files

- `plugin.video.m4sport/addon.py` – plugin entrypoint and route handling
- `plugin.video.m4sport/resources/lib/m4sport_core.py` – stream URL extraction logic
- `plugin.video.m4sport/resources/settings.xml` – plugin settings (`source_page_url`, `channel_name`)
- `plugin.video.m4sport/addon.xml` – Kodi addon metadata and extension registration
