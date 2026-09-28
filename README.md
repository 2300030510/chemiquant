# ChemiQuant — real backend version

A Flask + SQLite full-stack app for the Chemiluminometer Concentration
Analyzer project. No login/auth — every endpoint is open, as requested.

## What's real here (vs. the earlier browser-only prototype)
- **Server-side frame decoding**: images via Pillow, video via OpenCV
  (`cv2.VideoCapture`, seeks to the exact timestamp you scrub to), animated
  GIFs via Pillow's frame index (`Image.seek`).
- **A persisted database** (`chemiquant.db`, SQLite): calibration curves and
  every prediction are stored, not just held in memory in the browser tab.
- **A real regression model**: `scikit-learn`'s `LinearRegression`, fit
  server-side and re-fit whenever you save new calibration standards.
- **Real email sending** via `smtplib`/SMTP — not a `mailto:` link. It needs
  your own SMTP credentials (see below); until those are set it returns a
  clear "not configured" error instead of pretending to send.

## Setup
```bash
cd chemiquant
python3 -m venv venv && source venv/bin/activate     # optional but recommended
pip install -r requirements.txt
python app.py
```
Open **http://localhost:5000**. The SQLite database and its four preset
calibration curves (luminol/H₂O₂, pesticide residue, oil rancidity, ALP) are
created automatically on first run.

## Enabling email
The app reads its SMTP settings from a `.env` file next to `app.py` (via
`python-dotenv`), so they don't depend on which terminal tab you happen to
run it from or get lost when you restart.

1. Copy the template: `cp .env.example .env`
2. Open `.env` and fill in real values:
   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=you@example.com
   SMTP_PASS=your_app_password
   FROM_EMAIL=you@example.com
   ```
   For Gmail, `SMTP_PASS` must be a 16-character
   [App Password](https://support.google.com/accounts/answer/185833)
   (Google Account → Security → 2-Step Verification → App passwords) —
   your normal Gmail password will be rejected. Any other provider
   (Outlook, SendGrid, your institution's mail server) works the same way;
   just point `SMTP_HOST`/`SMTP_PORT` at it.
3. **Restart** `python app.py` — `.env` is only read at startup.
4. Before testing "Send result by email" in the UI, check
   `http://localhost:5000/api/email/status` in your browser. It should show
   `"configured": true` with every field `true`. If any field is `false`,
   the `.env` file isn't being found or a line is missing/misspelled — fix
   that first, since sending will keep failing with the same "not
   configured" error until this check passes.

(You can still use real environment variables instead of `.env` if you
prefer — e.g. in a hosting platform's secrets panel — `.env` is just there
so local runs don't depend on the shell session.)

## API summary
| Method | Route | Purpose |
|---|---|---|
| GET | `/api/library` | List all calibration curves (presets + custom) |
| POST | `/api/calibration` | Save/re-fit a curve from `{analyte, units, points}` |
| POST | `/api/upload` | Upload an image/video/GIF, returns an `upload_id` |
| GET | `/api/frame/<id>?t=` or `?frame=` | Decode + analyze one frame |
| POST | `/api/predict` | `{analyte, intensity}` → stored concentration result |
| GET | `/api/results` | Last 20 predictions |
| POST | `/api/email` | `{to, result_id}` → sends the result by real email |

## Moving to production
The built-in server (`app.run`) is for development only. For a real
deployment:
- Serve with `gunicorn app:app` (Linux) or `waitress` (Windows) behind Nginx.
- Switch `SQLALCHEMY_DATABASE_URI` from SQLite to Postgres/MySQL if more than
  one process will write to it concurrently.
- Put the SMTP credentials in your host's secret manager rather than plain
  environment variables in source control.
- If you later *do* want access control, add it at the reverse-proxy layer
  or with Flask-Login — deliberately left out here per the "no login page"
  requirement.

## Project outline this implements
Built against `Mahanya_K_2024A8PS2049H__Project_Outline.pdf` — "Chemiluminometer
Concentration Analyzer (CCA)" / **ChemiQuant** — plus five follow-up
requirements: email delivery of results, a calibration library, video/GIF
frame capture, automatic (not spreadsheet) concentration calculation, and no
login page.
