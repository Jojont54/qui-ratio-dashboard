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
        "body{font-family:Inter,system-ui,sans-serif;margin:0;padding:20px;background:#0f1117;color:#e4e6eb}",
        "h2{margin:0 0 20px 0;font-weight:600;font-size:20px}",
        "table{width:100%;border-collapse:collapse;background:#161b22;border-radius:12px;overflow:hidden}",
        "th{text-align:left;padding:14px;background:#1c2128;font-size:13px;color:#9da5b4}",
        "td{padding:14px;font-size:14px}",
        "tr{transition:background 0.2s ease}",
        "tr:hover{background:#20252e}",
        ".bad{color:#ff4d4f;font-weight:600}",
        ".low{color:#ffb020;font-weight:600}",
        ".ok{color:#3ddc97;font-weight:600}",
        ".ratio-bar{height:6px;border-radius:4px;background:#222;margin-top:6px;overflow:hidden}",
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
