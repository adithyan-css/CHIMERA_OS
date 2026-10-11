FROM python:3.10-slim

WORKDIR /app

# The operator only needs the control core, not the research/evaluation stack.
# numpy, scipy and kubernetes all ship wheels, so no compiler is installed.
COPY k8s/requirements-operator.txt .
RUN pip install --no-cache-dir -r requirements-operator.txt

# Copy application code
COPY src/ /app/src/
COPY models/ /app/models/
COPY k8s/manager.py k8s/operator_core.py k8s/prom_query.py /app/

ENV PYTHONPATH=/app

CMD ["python", "/app/manager.py"]
