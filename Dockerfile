FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
ENV PYTHONPATH=/app/src
EXPOSE 8787

CMD ["gunicorn", "-b", "0.0.0.0:8787", "qui_ratio_dashboard.app:app", "--workers", "2", "--threads", "4", "--timeout", "30"]
