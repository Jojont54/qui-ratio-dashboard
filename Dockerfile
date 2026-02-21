FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY qui_ratio_dashboard ./qui_ratio_dashboard

# copy example config files into image
COPY buffers.yml.example /app/buffers.yml.example
COPY trackers.yml.example /app/trackers.yml.example

# entrypoint for auto-init
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV PORT=8787
EXPOSE 8787

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "-b", "0.0.0.0:8787", "qui_ratio_dashboard.app:app", "--workers", "2", "--threads", "4", "--timeout", "30"]
