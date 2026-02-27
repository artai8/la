FROM python:3.11-slim

# Install compilation tools (required for tgcrypto) and unzip/curl for v2ray
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev curl unzip \
    && rm -rf /var/lib/apt/lists/*

# Install v2ray
COPY --from=v2fly/v2ray-core:latest /usr/bin/v2ray /usr/bin/v2ray
COPY --from=v2fly/v2ray-core:latest /usr/share/v2ray/geoip.dat /usr/share/v2ray/geoip.dat
COPY --from=v2fly/v2ray-core:latest /usr/share/v2ray/geosite.dat /usr/share/v2ray/geosite.dat

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Ensure directories exist
RUN mkdir -p account data delete downloads gaps static/css static/js templates

# Railway uses PORT environment variable
ENV PORT=8080
ENV V2RAY_LOCATION_ASSET=/usr/share/v2ray/

EXPOSE ${PORT}

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/ || exit 1

CMD ["python", "run.py"]
