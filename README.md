# Full-Stack Application

## Setup

### Backend (FastAPI)

1. Navigate to the backend directory:
```bash
cd backend
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Run the server:
```bash
uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`

### Frontend (React)

1. Navigate to the frontend directory:
```bash
cd frontend
```

2. Install dependencies:
```bash
npm install
```

3. Start the development server:
```bash
npm start
```

The app will be available at `http://localhost:3000`

## Docker Setup (Development with Autoreload)

1. Build and start both services:
```bash
docker-compose up --build
```

2. The services will be available at:
   - Backend API: `http://localhost:8000`
   - Frontend: `http://localhost:3000`

3. Both services have autoreload enabled:
   - Backend: Changes to Python files will automatically restart the server
   - Frontend: Changes to React files will hot-reload in the browser

4. To stop the services:
```bash
docker-compose down
```
