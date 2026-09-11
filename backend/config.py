import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get('REHABAI_DATA', ROOT / 'data'))
STORAGE_DIR = Path(os.environ.get('REHABAI_STORAGE', ROOT / 'storage'))
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///' + str(DATA_DIR / 'rehabai.db').replace('\\', '/'))
JWT_SECRET = os.environ.get('JWT_SECRET', 'rehabai-local-prototype-secret-key')
JWT_HOURS = int(os.environ.get('JWT_HOURS', '12'))
HOST = os.environ.get('REHABAI_HOST', '127.0.0.1')
PORT = int(os.environ.get('REHABAI_PORT', '8000'))
LLM_BASE_URL = os.environ.get('LLM_BASE_URL', '')
LLM_API_KEY = os.environ.get('LLM_API_KEY', '')
LLM_MODEL = os.environ.get('LLM_MODEL', 'local-clinician-llm')
MEASUREMENT_SOURCE = os.environ.get('REHABAI_SOURCE', 'simulation')  # simulation | live
POSE_MODEL = os.environ.get('REHABAI_POSE_MODEL', '')
POSE_KIND = os.environ.get('REHABAI_POSE_KIND', 'mediapipe')
POSE_DEVICE = os.environ.get('REHABAI_POSE_DEVICE', 'cpu')
DEMO_PASSWORD = os.environ.get('REHABAI_DEMO_PASSWORD', 'rehabai-demo')
