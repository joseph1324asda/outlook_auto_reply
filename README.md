# outlook_auto_reply

LLM-powered Outlook assistant with preset-group replies, manual libraries, and a web UI that doubles as an Outlook add-in taskpane.

## Run the service
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Start the Flask server (defaults to port 7999):
   ```bash
   python -m llm_mailer.web_app
   ```
   - Set `LLM_MAILER_SSL_CERT` and `LLM_MAILER_SSL_KEY` to run with your own certificate, or set `LLM_MAILER_SSL=adhoc` to let Flask
     generate a temporary self-signed certificate for quick local testing.

### One-click local start scripts
- **Windows (PowerShell):**
  ```powershell
  ./start.ps1
  ```
  Add `--no-install` to skip dependency installation if you already ran `pip install -r requirements.txt`.
- **Windows (Command Prompt):**
  ```bat
  start.bat
  ```
  Add `--no-install` to skip dependency installation if you already ran `pip install -r requirements.txt`.
- **macOS/Linux (bash):**
  ```bash
  ./start.sh
  ```
  Add `--no-install` to skip dependency installation if you already ran `pip install -r requirements.txt`.

Both scripts set `LLM_MAILER_SSL=adhoc` and `PORT=7999` before launching the Flask server for Outlook-compatible HTTPS.

## Outlook add-in
1. Ensure the server is reachable at `https://localhost:7999` (use a dev certificate or tunnel for HTTPS if Outlook requires it).
2. Sideload `docs/outlook-addin-manifest.xml` in Outlook (File → Manage Add-ins → Upload My Add-in) and confirm the taskpane button appears on read/compose surfaces.
3. Open the taskpane to access the Outlook-focused UI:
   - Choose preset groups and click **Generate reply** (manual and API details stay in a separate pop-out).
   - The add-in will prefill the subject/body from the active message when Office.js is available.
4. Use the **Open settings pop-out** link to adjust API keys, model/base URL, and randomness controls without leaving Outlook. Full preset/manual editing remains in the main workspace at `/`.

## Notes
- Preset groups and manuals are stored under `data/` and synced across the main UI and Outlook taskpane.
- The manifest references `/static/icon-32.svg` for icons; the repository ships a lightweight placeholder SVG for development.
