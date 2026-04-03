FROM python:3.12-slim

WORKDIR /app

# Copy everything needed to install
COPY pyproject.toml .
COPY src/ src/

# Install runtime dependencies only (no dev/test tools in production image)
RUN pip install --no-cache-dir .

# Don't run as root
USER nobody

CMD ["python", "-m", "kindle_email"]
