FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY qui_ratio_dashboard ./qui_ratio_dashboard
ENV PYTHONPATH=/app
EXPOSE 8787

CMD ["gunicorn", "-b", "0.0.0.0:8787", "qui_ratio_dashboard.app:app", "--workers", "2", "--threads", "4", "--timeout", "30"]
