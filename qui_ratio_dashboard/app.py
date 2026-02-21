import math
import requests

from flask import Flask, jsonify, Response, request, abort

from .qui_client import QuiClient
from .formatters import compute_tracker_rows, fmt_bytes
from .config import (
    PORT,
    HOMARR_AUTH_ENABLED,
    HOMARR_BASE_URL,
    HOMARR_SESSION_ENDPOINT,
    HTTP_TIMEOUT,
)

app = Flask(__name__)
client = QuiClient()


def homarr_session_ok() -> bool:
    """
    Vérifie si l'utilisateur est authentifié sur Homarr,
    en appelant Homarr côté serveur avec le cookie du client.
    """
    cookie = request.headers.get("Cookie", "")
    if not cookie:
        return False

    try:
        r = requests.get(
            HOMARR_BASE_URL + HOMARR_SESSION_ENDPOINT,
            headers={"Cookie": cookie},
            timeout=HTTP_TIMEOUT,
            allow_redirects=False,
        )
        return r.status_code == 200
    except requests.RequestException:
        # fail-closed
        return False


@app.before_request
def require_homarr_auth():
    if not HOMARR_AUTH_ENABLED:
        return

    # Autorise un healthcheck (pratique pour debug / monitoring)
    if request.path == "/health":
        return

    if not homarr_session_ok():
        abort(401)


@app.after_request
def add_headers(resp):
    # Autoriser l’embed dans ton domaine (ajuste si besoin)
    resp.headers["Content-Security-Policy"] = "frame-ancestors 'self' https://jojont.fr"
    return resp


@app.get("/health")
def health():
    return "ok"


@app.get("/api/ratios")
def api_ratios():
    payload = client.fetch_torrents_summary()
    rows = compute_tracker_rows(payload)
    out = []
    for r in rows:
        rr = dict(r)
        if rr["ratio"] == math.inf:
            rr["ratio"] = None
        out.append(rr)
    return jsonify({"trackers": out})


@app.get("/")
def html():
    payload = client.fetch_torrents_summary()
    rows = compute_tracker_rows(payload)

    def fmt_ratio(x):
        return "∞" if x == math.inf else f"{x:.2f}"

    parts = [
        "<!doctype html><html><head><meta charset='utf-8'/>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'/>",
        "<title>Tracker Ratios</title>",
        "<style>",
        ":root{",
        "  /* Homarr v2 (shadcn-ish tokens) + fallbacks */",
        "  --bg: var(--background, #0f1117);",
        "  --fg: var(--foreground, #e4e6eb);",
        "  --card: var(--card, #161b22);",
        "  --card-fg: var(--card-foreground, #e4e6eb);",
        "  --muted: var(--muted, #111827);",
        "  --muted-fg: var(--muted-foreground, #9da5b4);",
        "  --border: var(--border, rgba(255,255,255,.08));",
        "  --ring: var(--ring, rgba(255,255,255,.12));",
        "  --radius: var(--radius, 16px);",
        "",
        "  --danger: var(--destructive, #ff4d4f);",
        "  --warning: var(--warning, #ffb020);",
        "  --success: var(--success, #3ddc97);",
        "}",
        "html,body{margin:0;padding:0}",
        "body{font-family:Inter,system-ui,sans-serif;background:var(--bg);color:var(--fg);padding:16px}",
        "h2{margin:0 0 12px 0;font-weight:600;font-size:16px;letter-spacing:-.01em;color:var(--fg)}",
        ".card{background:var(--card);color:var(--card-fg);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}",
        "table{width:100%;border-collapse:collapse}",
        "thead th{background:color-mix(in oklab, var(--card) 88%, black);color:var(--muted-fg);text-align:left;padding:12px 14px;font-size:12px;font-weight:600;letter-spacing:.02em;border-bottom:1px solid var(--border)}",
        "tbody td{padding:12px 14px;font-size:13px;border-bottom:1px solid var(--border)}",
        "tbody tr:last-child td{border-bottom:0}",
        "tbody tr:hover td{background:color-mix(in oklab, var(--card) 92%, white)}",
        ".mono{font-variant-numeric:tabular-nums;white-space:nowrap}",
        ".tracker{font-weight:500}",
        ".badge{display:inline-flex;align-items:center;justify-content:center;padding:2px 8px;border-radius:999px;border:1px solid var(--border);font-weight:650;font-size:12px;line-height:18px}",
        ".bad{color:var(--danger)}",
        ".low{color:var(--warning)}",
        ".ok{color:var(--success)}",
        ".delta{opacity:.95}",
        "@media (max-width: 900px){",
        "  body{padding:12px}",
        "  th,td{padding:10px 10px}",
        "  h2{font-size:15px}",
        "}",
        "</style></head><body>",
        "<h2>Ratios trackers</h2>",
        "<div class='card'>",
        "<table><thead><tr>",
        "<th>Tracker</th><th>Upload</th><th>Download</th><th>Ratio</th><th>Delta</th><th>#</th><th>Total</th>",
        "</tr></thead><tbody>",
    ]

    for r in rows:
        ratio = r["ratio"]
        cls = "ok"
        if ratio != math.inf:
            if ratio < 1.0:
                cls = "bad"
            elif ratio < 1.5:
                cls = "low"

        parts.append(
            f"<tr><td class='tracker'>{r['tracker']}</td>"
            f"<td class='mono'>{fmt_bytes(r['uploaded'])}</td>"
            f"<td class='mono'>{fmt_bytes(r['downloaded'])}</td>"
            f"<td class='mono'><span class='badge {cls}'>{fmt_ratio(ratio)}</span></td>"
            f"<td class='mono delta'>{fmt_bytes(r['delta'])}</td>"
            f"<td class='mono'>{r['count']}</td>"
            f"<td class='mono'>{fmt_bytes(r['total_size'])}</td></tr>"
        )

    parts += ["</tbody></table></div></body></html>"]
    return Response("\n".join(parts), mimetype="text/html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
