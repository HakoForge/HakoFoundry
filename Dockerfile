FROM python:3.11-alpine

RUN apk add --no-cache smartmontools

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create non-root user before copying files
RUN adduser -u 5678 --disabled-password --gecos "" appuser

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app files with correct ownership in one step
COPY --chown=appuser:appuser . /app

USER appuser

CMD ["python3", "main.py"]
