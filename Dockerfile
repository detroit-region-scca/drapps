FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Create a non-root user
RUN adduser --disabled-password --gecos '' appuser

WORKDIR /app

# Change ownership of /app
RUN chown -R appuser:appuser /app

# Install dependencies. pytest is here so the deploy and the weekly update can run the tests inside the image they
# are about to start (deploy/gcp/lib.sh).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn pytest

# Copy application code
COPY . .

# Ensure appuser owns the copied files
RUN chown -R appuser:appuser /app

# Switch to the non-root user
USER appuser

# Expose the default port
EXPOSE 5858

# Which commit this image was built from. Last, so it never spoils the cached layers above. The deploy reads the
# label to prove the running container is the new one.
ARG GIT_SHA=unknown
LABEL org.drscca.commit=$GIT_SHA
ENV DRAPPS_COMMIT=$GIT_SHA

# Default command: the production server, never Flask's development server (docker-compose.debug.yml overrides it).
# 120 s before a stuck request is given up on.
CMD ["gunicorn", "-w", "2", "--timeout", "120", "-b", "0.0.0.0:5858", "app:app"]
