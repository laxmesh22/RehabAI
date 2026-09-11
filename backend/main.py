"""Hospital platform API. Simulation and live sources stay explicitly labelled."""
import argparse
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from backend.config import HOST, PORT
from backend.database.session import init_db
from backend.seed import seed_if_empty
from backend.routers import router

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'
DIST = ROOT / 'dist'


@asynccontextmanager
async def lifespan(app: FastAPI):
    from backend.database.session import SessionLocal, init_db
    init_db()
    db = SessionLocal()
    try:
        seed_if_empty(db)
        db.commit()
    finally:
        db.close()
    yield


app = FastAPI(title='RehabAI', version='0.2.0',
              description='AI-assisted shoulder rehabilitation platform (prototype)',
              lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[
    'http://127.0.0.1:3000', 'http://localhost:3000',
    'http://127.0.0.1:8000', 'http://localhost:8000',
    'http://10.0.2.2:8000',
    'capacitor://localhost', 'https://localhost', 'http://localhost',
],
                   allow_origin_regex=r'https://.*\.up\.railway\.app|https://.*\.railway\.app|capacitor://localhost|http://localhost',
                   allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
app.include_router(router, prefix='/api')


@app.get('/station')
def patient_station():
    return FileResponse(DIST / 'index.html')


if DIST.exists():
    app.mount('/station-static', StaticFiles(directory=DIST), name='station-static')


@app.get('/')
def spa_index():
    index = WEB / 'index.html'
    if index.exists():
        return FileResponse(index)
    return HTMLResponse('<h1>RehabAI API</h1><p>Open /docs or start the frontend.</p>')


if WEB.exists():
    app.mount('/ui', StaticFiles(directory=WEB), name='web')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default=HOST)
    parser.add_argument('--port', type=int, default=PORT)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run('backend.main:app', host=args.host, port=args.port, reload=False)


if __name__ == '__main__':
    main()
