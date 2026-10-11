FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir kubernetes prometheus-client

# Copy application code
COPY src/ /app/src/
COPY models/ /app/models/
COPY k8s/manager.py /app/manager.py

ENV PYTHONPATH=/app

CMD ["python", "/app/manager.py"]
