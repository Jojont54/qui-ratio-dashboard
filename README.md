## qui-ratio-dashboard

Tiny dashboard to display per-tracker ratios based on QUI aggregated stats.

### Run (dev)
Create a `.env` file (NOT committed):
QUI_API_KEY=...

Then:
docker compose up --build

Open:
- http://localhost:8787/
- http://localhost:8787/api/ratios

Variable : 
QUI_BASE_URL 
QUI_INSTANCE_ID 
QUI_API_KEY 
HTTP_TIMEOUT 


Port:
PORT 

Path:
/data