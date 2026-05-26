## qui-ratio-dashboard

Self-hosted tracker ratio dashboard powered by QUI or direct torrent client
connections, with a compact Homarr widget and multi-client management screens.

The dashboard preserves totals from torrents removed from QUI. It records
visible decreases in `/data/state.json` and carries them into later totals, so
new upload and download activity remains visible immediately after a removal.
Persist `/data` between container recreations. A background refresh runs once
at service startup and then once per hour by default, even when nobody opens
the dashboard.

Manual buffers entered from the Trackers screen can seed historical
upload/download already shown by a tracker site but unavailable in QUI. The
result matches activity observed by QUI after that baseline; activity
completed and deleted between two dashboard reads cannot be reconstructed
without a direct tracker API.

## Interface

- `/`: full dashboard interface.
- `/trackers`: select detected domains, link them to a common display
  name, and manage visibility and buffers.
- `/clients`: add, test, edit or remove QUI, qBittorrent, Transmission,
  Deluge and rTorrent connections.
- `/options`: enable or disable the Homarr iFrame endpoint.
- `/iframe` or `/widget`: compact read-only iFrame, compatible with Homarr.

The former `/app/` and `/app/trackers` paths remain available for compatibility.

The SQLite database at `/data/ratio_dashboard.db` is the active configuration
source. Names, linked domains, buffers, clients and options are edited through
the interface.

When upgrading an older installation, the application detects existing
`trackers.yml` and `buffers.yml` files in `/data` or `/data/old` on startup.
If no tracker configuration is already present in SQLite, it imports the
legacy names, linked domains, visibility and buffers once, then continues
using only the database and removes the YAML files that were successfully
converted. Existing database settings are never overwritten, and YAML files
are not deleted when a database already contains tracker configuration.
If an old Docker Compose file still mounts these YAML files read-only, the
import succeeds but removal waits until that obsolete mount is removed.

Clients are configured from the Clients torrent screen with the `+` button.
QUI connections load the available qBittorrent instances as before. Direct
qBittorrent, Transmission, Deluge and rTorrent connections read torrent
counters from their APIs, aggregate them by tracker and feed the same
dashboard calculation previously supplied by QUI. Transmission and rTorrent
accept their RPC path; Deluge uses its Web API and can optionally connect a
configured daemon id.
Existing connections can be edited without re-entering the API key, or deleted
from their card. Totals from all configured clients are consolidated by
tracker. When a connection is created or deliberately reinitialized, choose
how its first reading is handled: keep stored values as the baseline for a
repaired connection or rebuild, add current values for an additional
instance, or erase stored tracker values and replace them with the client's
current totals. This choice is required for a new connection so a first client
is not mistaken for a repaired connection.

The direct-client collector operates per torrent before grouping totals by
tracker. Optional Prowlarr settings in Options inspect newly detected torrent
hashes in Prowlarr history and retain freeleech, silverleech or double-upload
factors before aggregation. For qBittorrent, Transmission and Deluge, only
torrents added during the configured collection interval are checked, so an
existing library is not searched on a rebuild. rTorrent has no standard,
reliable added-at value in this integration and is checked on first detection.
This cannot work with QUI connections because QUI only supplies totals already
grouped by tracker, without torrent hashes.

The Options screen can disable the compact iFrame globally. When disabled,
`/iframe` and its compatibility alias `/widget` are unavailable, while saved
per-tracker iFrame visibility is retained for a later reactivation.

Homarr authentication is also configured from Options: enable it, provide
the Homarr address and its session endpoint, then save. Once enabled, a valid
Homarr session is required only to display `/iframe` or `/widget`; the main
application remains accessible directly.

The Dashboard and iFrame render the last collected snapshot immediately rather
than waiting for torrent clients on every page load. The Options screen also
controls collection frequency and API timeout. The
automatic refresh is always active and runs every 60 minutes by default; its
interval in minutes can be changed without restarting the container. When one
client is temporarily unavailable, responsive clients continue to refresh and
the unavailable client's last values are preserved until it returns.

Docker only exposes port `8787` and persists `/data`; no `.env` connection
configuration is required anymore.

## Security

The main application is not protected by its own login and stores torrent
client and Prowlarr credentials in its SQLite database under `/data`. Keep it
on a trusted local network or place it behind authenticated access before
exposing it outside your network. The optional Homarr session check protects
only the compact `/iframe` and `/widget` views.

Unknown tracker domains discovered during collection are added automatically
to the Trackers screen. Each display name expands to show its linked domains:
select domains and use the link button to join them under one name, or the
unlink button to return them to individual trackers. The Dashboard and Homarr
widget show one consolidated row per name. Select all domains of an existing
group and link them to a new name to rename it while keeping its buffers.
When both a tracker domain and one of its subdomains are detected, such as
`abn.lol` and `tracker.abn.lol`, they are automatically linked on discovery;
an explicit unlink remains separate afterwards.
The display name, visibility and buffers are edited in the same screen and
saved together with the global save button. Upload and download event menus
can be used for double/triple upload, silverleech and freeleech periods.
Event multipliers apply only to transfer progress collected after the setting
is enabled; existing totals are not recalculated. Enabling an event first
requires a successful reference collection from every configured client, so
uncollected earlier traffic is not mistakenly multiplied. A remaining-hours
field can set an automatic end for each event. Once that time is reached, the
event returns to normal on the next collection.
