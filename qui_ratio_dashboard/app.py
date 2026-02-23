import math
import requests

from flask import Flask, jsonify, Response, request, abort

from .qui_client import QuiClient
from .formatters import compute_tracker_rows, fmt_bytes
from .state_store import apply_ledger
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
    rows = apply_ledger(rows)
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
        "body{font-family:Inter,system-ui,sans-serif;margin:0;padding:20px;background:#242424;color:#e4e6eb}",
        "h2{margin:0 0 16px 0;font-weight:600;font-size:16px}",
        "table{width:100%;border-collapse:collapse;background:#2e2e2e;border-radius:12px;overflow:hidden}",
        "th{text-align:left;padding:14px;background:#2e2e2e;font-size:13px;color:#9da5b4}",
        "thead{border-bottom:1px solid #424242}",
        "td{padding:14px;font-size:14px}",
        "tr{transition:background 0.2s ease}",
        "tr:hover{background:#242424}",
        ".bad{color:#ff4d4f;font-weight:600}",
        ".low{color:#ffb020;font-weight:600}",
        ".ok{color:#3ddc97;font-weight:600}",
        ".ratio-bar{height:6px;border-radius:4px;background:#2e2e2e;margin-top:6px;overflow:hidden}",
        ".ratio-fill{height:100%;border-radius:4px}",
        "</style></head><body>",
        "<h2>Tracker Ratios</h2>",
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
