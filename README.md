# outlook_auto_reply

LLM-powered Outlook assistant with preset-group replies and manual libraries for the Outlook add-in taskpane.

## Run the service
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Start the Flask server (defaults to port 8000). Set `LLM_MAILER_SSL_CERT` and `LLM_MAILER_SSL_KEY` to enable HTTPS, or `LLM_MAILER_SSL_ADHOC=1` for a temporary self-signed cert:
   ```bash
   python -m llm_mailer.web_app
   ```
3. Open `https://localhost:8000/outlook` (or your tunnel URL) to load the add-in taskpane UI.

## Outlook add-in
1. Ensure the server is reachable at `https://localhost:8000` (use a dev certificate or tunnel for HTTPS if Outlook requires it).
2. Sideload `docs/outlook-addin-manifest.xml` in Outlook (File → Manage Add-ins → Upload My Add-in) and confirm the taskpane button appears on read/compose surfaces.
3. Open the taskpane to access the Outlook-focused UI:
   - Choose preset groups, optionally add a custom request, and click **Generate reply**.
   - The add-in will prefill the subject/body from the active message when Office.js is available.
4. Use the **Open settings pop-out** link to adjust API keys, model/base URL, and randomness controls without leaving Outlook.

## Notes
- Preset groups and manuals are stored under `data/` for the Outlook experience; update the JSON files on the server to change them.
- The manifest references `/static/icon-32.svg` for icons; the repository ships a lightweight placeholder SVG for development.
