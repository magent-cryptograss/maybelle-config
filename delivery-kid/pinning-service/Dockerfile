FROM python:3.12-slim

# Build args for version info
ARG GIT_COMMIT=unknown
ARG BUILD_TIME=unknown

ENV GIT_COMMIT=${GIT_COMMIT}
ENV BUILD_TIME=${BUILD_TIME}

# Install ffmpeg for audio transcoding
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Run as non-root user
RUN useradd -m -u 1000 appuser
USER appuser

EXPOSE 3001
EXPOSE 6881

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3001"]
