# SCCA Event Registration

A simple web application for SCCA program directors to register and track events.

## Features

*   Create new events with program name, date, description, and creator name.
*   Automatically generates a unique code and sequence ID for each event.
*   Recommends an MSR event name and provides a button to copy it to the clipboard.
*   Events are grouped by program name in a collapsible list.
*   Delete events with a confirmation modal.
*   Dark mode interface.

## Configuration

This application is configured via a `.env` file. Create a `.env` file in the root of the project with the following variables:

```env
SCCA_REGION_ACRONYM="YOUR_REGION_ACRONYM"
SCCA_REGION_NAME="Your Region SCCA Name"
EVENT_CODE_PREFIX="YR"
PROGRAM_DIRECTORS_TEXT="For use by YOUR_REGION_ACRONYM program directors"
FLASK_SECRET_KEY="a_super_secret_key"
APP_PASSWORD="your_password"
PORT=5858
HOST=0.0.0.0
DEBUG=False
PROGRAMS_FILE=data/programs.json
```

**Note on Data Storage:** The application stores its events, audit logs, and program definitions in files inside the `data/` directory. When running via Docker, this directory is mounted as a volume so that your data persists across container restarts and rebuilds.

## Program Definitions

The available event programs (e.g., Autocross, Road Racing) are defined in the `data/programs.json` file. You can customize the programs by editing this file. The format is a simple JSON object where the key is the program code (e.g., "AX") and the value is the full name (e.g., "Autocross").

## Running the Application

### Using Docker (Recommended)

The easiest way to run the application is using Docker Compose. The provided `docker-compose.prod.yml` sets up a production-ready container that maps the `data/` directory to your host to persist events securely.

1. Ensure you have Docker and Docker Compose installed.
2. Build and start the container in detached mode:
   ```bash
   docker compose -f docker-compose.prod.yml up -d --build
   ```
3. Open your web browser and navigate to `http://localhost:5959` (or the mapped port configured in the compose file).

*Note: The project uses a `.dockerignore` file to ensure local files like `.env`, the Python virtual environment (`venv/`), and the local `data/` folder are not accidentally baked into the built Docker image. This guarantees that your environment configuration and persistent data correctly rely on Docker Compose's volume mapping and variable injection.*

### Running Locally (Without Docker)

1. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
2. Run the application:
   ```bash
   python app.py
   ```
3. Open your web browser and navigate to `http://localhost:<PORT>` (default 5858).
