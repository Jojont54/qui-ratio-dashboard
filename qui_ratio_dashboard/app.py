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
        "  --bg: var(--background, #0f1117);",
        "  --fg: var(--foreground, #e4e6eb);",
        "  --card: var(--card, #161b22);",
        "  --card2: var(--card-secondary, #1c2128);",
        "  --border: var(--border, rgba(255,255,255,.08));",
        "  --muted: var(--muted-foreground, #9da5b4);",
        "  --row-hover: rgba(255,255,255,.04);",
        "  --shadow: 0 8px 24px rgba(0,0,0,.35);",
        "",
        "  /* Status colors (fallbacks) */",
        "  --danger: var(--color-danger, #ff4d4f);",
        "  --warning: var(--color-warning, #ffb020);",
        "  --success: var(--color-success, #3ddc97);",
        "}",
        "html,body{height:100%}",
        "body{font-family:Inter,system-ui,sans-serif;margin:0;padding:0px;background:transparent;color:var(--fg)}",
        "h2{margin:0 0 12px 0;font-weight:600;font-size:16px;color:var(--fg);opacity:.95}",
        ".card{background:var(--card);border:1px solid var(--border);border-radius:16px;overflow:hidden;box-shadow:var(--shadow)}",
        "table{width:100%;border-collapse:separate;border-spacing:0}",
        "thead th{position:sticky;top:0;background:var(--card2);text-align:left;padding:12px 14px;font-size:12px;letter-spacing:.02em;color:var(--muted);border-bottom:1px solid var(--border)}",
        "tbody td{padding:12px 14px;font-size:13px;border-bottom:1px solid var(--border)}",
        "tbody tr:hover{background:var(--row-hover)}",
        "tbody tr:last-child td{border-bottom:0}",
        ".mono{font-variant-numeric:tabular-nums}",
        ".bad{color:var(--danger);font-weight:650}",
        ".low{color:var(--warning);font-weight:650}",
        ".ok{color:var(--success);font-weight:650}",
        ".delta{white-space:nowrap}",
        "</style></head><body>",
        "<h2>Ratios trackers</h2>",
        "<div class='card'>",
        "<table><thead><tr>",
        "<th>Tracker</th><th>Upload</th><th>Download</th><th>Ratio</th><th>Delta</th><th>#</th><th>Total</th>",
        "</tr></thead><tbody>",
    ]
    parts += ["</tbody></table></div></body></html>"]

    for r in rows:
        ratio = r["ratio"]
        cls = "ok"
        if ratio != math.inf:
            if ratio < 1.0:
                cls = "bad"
            elif ratio < 1.5:
                cls = "low"

        parts.append(
            f"<tr><td>{r['tracker']}</td>"
            f"<td>{fmt_bytes(r['uploaded'])}</td>"
            f"<td>{fmt_bytes(r['downloaded'])}</td>"
            f"<td class='{cls}'>{fmt_ratio(ratio)}</td>"
            f"<td>{fmt_bytes(r['delta'])}</td>"
            f"<td>{r['count']}</td>"
            f"<td>{fmt_bytes(r['total_size'])}</td></tr>"
        )

    parts += ["</tbody></table></body></html>"]
    return Response("\n".join(parts), mimetype="text/html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
