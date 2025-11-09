FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY config.py .
COPY dependencies.py .
COPY schemas.py .
COPY kafka_producer.py .
COPY main.py .

# Set Python path
ENV PYTHONPATH=/app

# Run application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]
