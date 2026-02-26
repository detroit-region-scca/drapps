# DRSCCA Event Registration

A simple web application for DRSCCA program directors to register and track events.

## Features

*   Create new events with program name, date, description, and creator name.
*   Automatically generates a unique code and sequence ID for each event.
*   Recommends an MSR event name and provides a button to copy it to the clipboard.
*   Events are grouped by program name in a collapsible list.
*   Delete events with a confirmation modal.
*   Dark mode interface.

## Running the Application

1.  Install the required dependencies:
    ```bash
    pip install Flask
    ```
2.  Run the application:
    ```bash
    python app.py
    ```
3.  Open your web browser and navigate to `http://localhost:5858`.
