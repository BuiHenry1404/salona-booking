FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Create non-root user
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash appuser

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Change ownership to appuser
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/api/v1/health')"

# Run the application
# IMPORTANT: keep this at a single worker. Do NOT add --workers N (or switch to
# gunicorn -w N). Each worker is a separate process running the full lifespan,
# including the Telegram getUpdates long-polling loop. Telegram permits only one
# active getUpdates connection per bot token, so multiple workers cause a
# continuous "409 Conflict: terminated by other getUpdates request" loop.
# For the same reason, do not scale this service to multiple replicas.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"] 