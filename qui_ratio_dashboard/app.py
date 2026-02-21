import math
from flask import Flask, jsonify, Response
from .qui_client import QuiClient
from .formatters import compute_tracker_rows, fmt_bytes
from .config import PORT

app = Flask(__name__)
client = QuiClient()

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
        "body{font-family:system-ui;margin:16px;background:#111;color:#eee}",
        "table{width:100%;border-collapse:collapse}",
        "th,td{padding:10px;border-bottom:1px solid #333}",
        "th{background:#161616;position:sticky;top:0}",
        ".bad{color:#ff6b6b;font-weight:700}",
        ".low{color:#ffcc66;font-weight:700}",
        ".ok{color:#7cff7c;font-weight:700}",
        "</style></head><body>",
        "<h2 style='margin:0 0 12px 0'>Ratios trackers (via QUI)</h2>",
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
