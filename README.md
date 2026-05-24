## qui-ratio-dashboard

Tiny dashboard displaying per-tracker ratios from QUI aggregated stats, for local
use or embedding behind Homarr authentication.

The dashboard preserves totals from torrents removed from QUI. It records
visible decreases in `/data/state.json` and carries them into later totals, so
new upload and download activity remains visible immediately after a removal.
Persist `/data` between container recreations. A background refresh runs once
at service startup and then once per hour by default, even when nobody opens
the dashboard.

`buffers.yml` can seed historical upload/download already shown by a tracker
site but unavailable in QUI. The result matches activity observed by QUI after
that baseline; activity completed and deleted between two dashboard reads
cannot be reconstructed without a direct tracker API.

Configuration variables:

- `QUI_BASE_URL`
- `QUI_INSTANCE_ID`
- `QUI_API_KEY`
- `HTTP_TIMEOUT`
- `BACKGROUND_REFRESH_ENABLED` (`1` by default)
- `REFRESH_INTERVAL_SECONDS` (`3600` by default)
- `HOMARR_AUTH_ENABLED`
- `HOMARR_BASE_URL`
- `HOMARR_SESSION_ENDPOINT`
- `BUFFERS_PATH`
- `TRACKERS_PATH`
- `STATE_PATH`
- `PORT`

`trackers.yml` maps several tracker domains to one displayed tracker. Set
`visible: false` on a tracker to hide it from the web page while keeping its
history and API data; if omitted, the tracker remains visible.
`buffers.yml` adds an initial upload/download baseline.
