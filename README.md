## qui-ratio-dashboard

Self-hosted tracker ratio dashboard currently powered by QUI, with a compact
Homarr widget and the first management screens for a larger multi-client
application.

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
- `/clients`: add or remove QUI connections and select the qBittorrent
  instance exposed by each QUI server.
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

QUI servers are configured from the Clients torrent screen with the `+`
button: enter the QUI address, port and API key, load its available qBittorrent
instances, then select the instance whose transfer totals should be collected.
Existing connections can be edited without re-entering the API key, or deleted
from their card. Totals from all configured clients are consolidated by
tracker. When a connection is created or deliberately reinitialized, choose
how its first reading is handled: keep stored values as the baseline for a
repaired connection or rebuild, add current values for an additional
instance, or erase stored tracker values and replace them with the client's
current totals. This choice is required for a new connection so a first client
is not mistaken for a repaired connection.

The Options screen can disable the compact iFrame globally. When disabled,
`/iframe` and its compatibility alias `/widget` are unavailable, while saved
per-tracker iFrame visibility is retained for a later reactivation.

Homarr authentication is also configured from Options: enable it, provide
the Homarr address and its session endpoint, then save. Once enabled, a valid
Homarr session is required to access the application.

The Options screen also controls background collection and API timeout. The
automatic refresh is enabled by default and runs hourly; both its activation
and interval can be changed without restarting the container.

Docker only exposes port `8787` and persists `/data`; no `.env` connection
configuration is required anymore.

Unknown tracker domains discovered during collection are added automatically
to the Trackers screen. Each display name expands to show its linked domains:
select domains and use the link button to join them under one name, or the
unlink button to return them to individual trackers. The Dashboard and Homarr
widget show one consolidated row per name. Select all domains of an existing
group and link them to a new name to rename it while keeping its buffers.
The display name, visibility and buffers are edited in the same screen and
saved together with the global save button. Upload and download event menus
can be used for double/triple upload, silverleech and freeleech periods.
Event multipliers apply only to transfer progress collected after the setting
is enabled; existing totals are not recalculated. A remaining-hours field can
set an automatic end for each event. Once that time is reached, the event
returns to normal on the next collection.
