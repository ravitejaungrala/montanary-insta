# Local Run Instructions

This guide explains how to set up and run the Pipelyt application on your local machine.

## Prerequisites

- Python 3.10+
- Node.js & npm
- PostgreSQL (running locally)

---

## 1. Backend Setup

Navigate to the backend directory:
```powershell
cd apps/backend
```

### Create and Activate Virtual Environment
```powershell
# Create venv
python -m venv venv

# Activate venv
.\venv\Scripts\activate
```

### Install Dependencies
```powershell
pip install -r requirements.txt
```

### Environment Variables
Ensure your `.env` file in `apps/backend` is correctly configured, especially the `DATABASE_URL`.

### Run the Server
```powershell
uvicorn main:app --reload
```
The backend will be available at `http://localhost:8000`.

---

## 2. Frontend Setup

Navigate to the frontend directory:
```powershell
cd ../frontend
```

### Install Dependencies
```powershell
npm install
```

### Run the Dev Server
```powershell
npm run dev
```
The frontend will be available at `http://localhost:5173`.

---

## 3. Running with Docker (Recommended)

If you have **Docker** installed, you can run both the frontend and backend with a single command. This is the easiest way to ensure everything works together correctly.

### Run anyway:
From the root directory:
```bash
docker-compose up --build
```

- **Backend**: Available at `http://localhost:8000`
- **Frontend**: Available at `http://localhost:5173`

---

## Important Notes

- **Database**: The application is now configured to use your **Amazon RDS** database.
- **Instagram Posting (Local Testing)**: Instagram's API requires a publicly accessible URL for your images. If you are running locally, use a tool like **ngrok** to tunnel your backend port:
  1. Install ngrok and run: `ngrok http 8000`
  2. Copy the public URL (e.g., `https://xxxx-xxxx.ngrok-free.app`).
  3. Add it to your `.env` file: `PUBLIC_URL=https://xxxx-xxxx.ngrok-free.app`
  4. Restart the backend.
- **Image Preview**: If the image preview doesn't show up after a post, you can now re-upload the same or a different image without refreshing the page.
