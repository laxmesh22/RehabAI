"""Hospital platform API. Simulation and live sources stay explicitly labelled."""
import argparse
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from backend.config import ALLOWED_ORIGINS, HOST, PORT
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


app = FastAPI(title='PhysioBuDDY', version='0.2.0',
              description='AI-assisted shoulder rehabilitation platform (prototype)',
              lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=list(ALLOWED_ORIGINS),
                   allow_origin_regex=(
                       r'capacitor://localhost|'
                       r'http://localhost|'
                       r'https://localhost|'
                       r'http://(127\.0\.0\.1|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+|192\.168\.\d+\.\d+)(:\d+)?'
                   ),
                   allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
app.include_router(router, prefix='/api')


@app.get('/station')
def patient_station():
    index = DIST / 'index.html'
    if index.is_file():
        return FileResponse(index)
    return HTMLResponse('<h1>PhysioBuDDY station bundle is not built.</h1><p>Open / for the studio UI.</p>', status_code=404)


if DIST.exists():
    app.mount('/station-static', StaticFiles(directory=DIST), name='station-static')


@app.get('/')
def spa_index():
    index = WEB / 'index.html'
    if index.exists():
        return FileResponse(index)
    return HTMLResponse('<h1>PhysioBuDDY API</h1><p>Open /docs or start the frontend.</p>')


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
