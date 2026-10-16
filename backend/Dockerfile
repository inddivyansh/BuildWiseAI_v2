FROM python:3.11-slim

# System libraries for OpenCV, shapely, and image processing
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        libgl1 \
        libglib2.0-0 \
        libxcb1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies via requirements.txt with uv
COPY requirements.txt pyproject.toml README.md ./
RUN uv pip install --system --no-cache -r requirements.txt

# Copy both application domains: Plan Compliance (planlint) & Blueprint Studio (app)
COPY planlint ./planlint
COPY app ./app

# Create storage directory for uploads and generated assets
RUN mkdir -p /app/storage/uploads /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "planlint.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
