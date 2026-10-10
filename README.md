# PocketSmart AI

Budget-aware recommendation demo based on the supplied project document. Includes Home, Party and Jewelry planners, optional outfit image analysis, login/register, saved history, API endpoints, and Gemini integration with a demo fallback.

## Windows / VS Code setup
1. Extract the ZIP and open this folder in VS Code.
2. Terminal → New Terminal.
3. Run `py -m venv .venv` (if `py` is unavailable, run `python -m venv .venv`).
4. Activate: PowerShell `\.venv\Scripts\Activate.ps1`; Command Prompt `\.venv\Scripts\activate.bat`.
5. Install: `python -m pip install --upgrade pip` then `pip install -r requirements.txt`.
6. Copy `.env.example` to `.env`; optionally add your Google AI Studio key as `GEMINI_API_KEY=...`.
7. Start: `uvicorn app.main:app --reload`
8. Open `http://127.0.0.1:8000`. API docs: `http://127.0.0.1:8000/docs`.

Without a Gemini key the app uses demo recommendations. Demo prices are estimates, not live marketplace inventory. Marketplace links are search links, not official product feeds. For public deployment, configure HTTPS, secure cookies, CSRF protection, rate limits, and production database hosting.
