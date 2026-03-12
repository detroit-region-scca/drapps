FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Create a non-root user
RUN adduser --disabled-password --gecos '' appuser

WORKDIR /app

# Change ownership of /app
RUN chown -R appuser:appuser /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# Copy application code
COPY . .

# Ensure appuser owns the copied files
RUN chown -R appuser:appuser /app

# Switch to the non-root user
USER appuser

# Expose the default port
EXPOSE 5858

# Default command (can be overridden by docker-compose)
CMD ["python", "app.py"]
