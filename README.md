# ParseFusion (backend + frontend in one folder)

```
ParseFusion/
  backend-app/    Python FastAPI backend   (run on port 8000)
  frontend-app/   React + Vite frontend    (run on port 5173)
```

## 1. Install these first (one time)

| Software | Version | Download |
|---|---|---|
| Python | 3.10 or newer | https://www.python.org/downloads/ (tick "Add Python to PATH") |
| Node.js (includes npm) | 18 or newer | https://nodejs.org (LTS) |
| Tesseract OCR (optional, for scanned pages) | any | https://github.com/UB-Mannheim/tesseract/wiki |

Check in a terminal: `python --version` and `node --version`.

## 2. Run the backend (Terminal 1)

Windows (Command Prompt):
```
cd ParseFusion\backend-app
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
rem Set unique values for PF_ADMIN_PASSWORD, PF_EDITOR_PASSWORD, and PF_VIEWER_PASSWORD in .env
uvicorn backend.main:app --port 8000
```
Mac/Linux:
```
cd ParseFusion/backend-app
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set unique values for PF_ADMIN_PASSWORD, PF_EDITOR_PASSWORD, and PF_VIEWER_PASSWORD in .env
uvicorn backend.main:app --port 8000
```
Check: open http://localhost:8000/docs. Keep this terminal open.

## 3. Run the frontend (Terminal 2, a new window)

```
cd ParseFusion\frontend-app
npm install
copy .env.example .env        (Mac/Linux: cp .env.example .env)
```
Open `.env` and set `VITE_API_BASE_URL=http://localhost:8000`, then:
```
npm run dev
```
Open http://localhost:5173 .

The three accounts are created from the `PF_ADMIN_*`, `PF_EDITOR_*`, and `PF_VIEWER_*` values in `backend-app/.env`.
Editors set viewer-hidden columns on the Access control page. Viewers can request locked columns there. Admins decide
requests and approved grants are signed and expire at the selected time. The API applies the policy to protected previews,
document text, case facts, and page images.

Open **Agent status** -> **Run contract check** to check backend wiring. The frontend contract is documented in
`frontend-app/docs/CONTRACT.md`.
