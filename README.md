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

This application is configured via a `.env` file.  Create a `.env` file in the root of the project with the following variables:

```
SCCA_REGION_ACRONYM="YOUR_REGION_ACRONYM"
SCCA_REGION_NAME="Your Region SCCA Name"
EVENT_CODE_PREFIX="YR"
PROGRAM_DIRECTORS_TEXT="For use by YOUR_REGION_ACRONYM program directors"
FLASK_SECRET_KEY="a_super_secret_key"
APP_PASSWORD="your_password"
PORT=5858
HOST=0.0.0.0
DEBUG=False
DATA_FILE=events.ndjson
DATA_LOCK_FILE=events.ndjson.lock
PROGRAMS_FILE=data/programs.json
```

## Program Definitions

The available event programs (e.g., Autocross, Road Racing) are defined in the `data/programs.json` file. You can customize the programs by editing this file. The format is a simple JSON object where the key is the program code (e.g., "AX") and the value is the full name (e.g., "Autocross").

## Running the Application

1.  Install the required dependencies:
    ```bash
    pip install Flask python-dotenv
    ```
2.  Run the application:
    ```bash
    python app.py
    ```
3.  Open your web browser and navigate to `http://localhost:<PORT>` (default 5858).
