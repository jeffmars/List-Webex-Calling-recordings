# List Converged Recordings

This script calls the Webex API to list call recordings (admin or compliance officer view) for the **past 30 days**, prints the total count, and saves all results to a CSV file.

## What you need

1. **Python 3** (3.9 or newer).
2. **`requests`** installed:
   ```bash
   pip install requests
   ```
3. A **Webex access token** with one of these scopes:
   - `spark-admin:recordings_read` (admin), or  
   - `spark-compliance:recordings_read` (compliance officer).

   Create or copy the token from the [Webex for Developers](https://developer.webex.com/) portal (e.g. under your app or “Personal Access Token”).

## How to run

1. Open a terminal and go to the folder that contains `list_converged_recordings.py`.
2. Run:
   ```bash
   python list_converged_recordings.py
   ```
3. When prompted, paste your Webex access token and press Enter.
4. Wait while the script runs. It will print progress to the screen (e.g. “Fetching recordings…”, “Page 1: 100 items (100 total)”).
5. When it finishes, it will:
   - Print the **final count** (number only) to the screen.
   - Create (or overwrite) **`converged_recordings.csv`** in the current directory with one row per recording.

## What you’ll see

- **While running:** Status lines like “Fetching recordings (past 30 days)…”, “Page 1: 100 items (100 total)”, “Writing CSV…”, and “Saved N recordings to converged_recordings.csv”.
- **At the end:** A single number (the total recording count). You can use this in other scripts, e.g. `count=$(python list_converged_recordings.py)`.
- **If rate limited:** The script will say it’s waiting and then retry automatically; you don’t need to do anything.

## Output file

- **File name:** `converged_recordings.csv` (in the directory where you ran the script).
- **Contents:** One row per recording, with columns such as id, topic, createTime, timeRecorded, ownerId, ownerEmail, format, durationSeconds, status, locationId, callSessionId, etc.

That’s it. Run the script, enter your token when asked, and use the printed count and the CSV file as needed.
