import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_env_file():
    path = ROOT / '.env'
    if not path.is_file():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.split('=', 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


def _use_os_certificate_store():
    """Campus SSL inspection fails against certifi alone; use Windows/macOS roots."""
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:
        return


_load_env_file()
_use_os_certificate_store()


def _sarvam_speaker(raw):
    name = (raw or 'shubh').strip().lower()
    return {'subh': 'shubh', 'shub': 'shubh'}.get(name, name) or 'shubh'


DATA_DIR = Path(os.environ.get('REHABAI_DATA', ROOT / 'data'))
STORAGE_DIR = Path(os.environ.get('REHABAI_STORAGE', ROOT / 'storage'))
DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///' + str(DATA_DIR / 'rehabai.db').replace('\\', '/'))
DEFAULT_JWT_SECRET = 'rehabai-local-prototype-secret-key'
JWT_SECRET = os.environ.get('JWT_SECRET', DEFAULT_JWT_SECRET)
JWT_SECRET_IS_DEFAULT = JWT_SECRET == DEFAULT_JWT_SECRET
JWT_HOURS = int(os.environ.get('JWT_HOURS', '12'))
HOST = os.environ.get('REHABAI_HOST', '127.0.0.1')
PORT = int(os.environ.get('REHABAI_PORT', '8000'))
LLM_BASE_URL = os.environ.get('LLM_BASE_URL', '')
LLM_API_KEY = os.environ.get('LLM_API_KEY', '')
LLM_MODEL = os.environ.get('LLM_MODEL', 'local-clinician-llm')
SARVAM_API_KEY = os.environ.get('SARVAM_API_KEY', '')
SARVAM_BASE_URL = os.environ.get('SARVAM_BASE_URL', 'https://api.sarvam.ai').rstrip('/')
SARVAM_STT_MODEL = os.environ.get('SARVAM_STT_MODEL', 'saaras:v4')
SARVAM_TTS_MODEL = os.environ.get('SARVAM_TTS_MODEL', 'bulbul:v3')
SARVAM_TTS_SPEAKER = _sarvam_speaker(os.environ.get('SARVAM_TTS_SPEAKER', 'shubh'))
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
ANTHROPIC_BASE_URL = os.environ.get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com').rstrip('/')
ANTHROPIC_MODEL = os.environ.get('ANTHROPIC_MODEL', 'claude-sonnet-5')
ELEVENLABS_API_KEY = os.environ.get('ELEVENLABS_API_KEY', '')
ELEVENLABS_BASE_URL = os.environ.get('ELEVENLABS_BASE_URL', 'https://api.elevenlabs.io').rstrip('/')
ELEVENLABS_VOICE_ID = os.environ.get('ELEVENLABS_VOICE_ID', '21m00Tcm4TlvDq8ikWAM')
ELEVENLABS_TTS_MODEL = os.environ.get('ELEVENLABS_TTS_MODEL', 'eleven_multilingual_v2')
ELEVENLABS_STT_MODEL = os.environ.get('ELEVENLABS_STT_MODEL', 'scribe_v1')
VOICE_TTS = os.environ.get('REHABAI_TTS', 'sarvam').strip().lower() or 'sarvam'
VOICE_STT = os.environ.get('REHABAI_STT', 'auto').strip().lower() or 'auto'
VOICE_TIMEOUT_S = float(os.environ.get('REHABAI_VOICE_TIMEOUT_S', '20'))
MEASUREMENT_SOURCE = os.environ.get('REHABAI_SOURCE', 'simulation')  # simulation | live
POSE_MODEL = os.environ.get('REHABAI_POSE_MODEL', '')
POSE_KIND = os.environ.get('REHABAI_POSE_KIND', 'mediapipe')
POSE_DEVICE = os.environ.get('REHABAI_POSE_DEVICE', 'cpu')
PHONE_INFERENCE_URL = os.environ.get('REHABAI_PHONE_INFERENCE_URL', '').strip().rstrip('/')
PHONE_INFERENCE_API_KEY = os.environ.get('REHABAI_PHONE_INFERENCE_API_KEY', '')
PHONE_INFERENCE_TIMEOUT_S = float(os.environ.get('REHABAI_PHONE_INFERENCE_TIMEOUT_S', '8'))
PHONE_FRAME_MAX_BYTES = int(os.environ.get('REHABAI_PHONE_FRAME_MAX_BYTES', '1500000'))
PHONE_FRAME_MAX_PIXELS = int(os.environ.get('REHABAI_PHONE_FRAME_MAX_PIXELS', str(1920 * 1080)))
PHONE_FRAME_MIN_INTERVAL_S = float(os.environ.get('REHABAI_PHONE_FRAME_MIN_INTERVAL_S', '0.18'))
DEMO_PASSWORD = os.environ.get('REHABAI_DEMO_PASSWORD', 'rehabai-demo')
IMU_TRANSPORT = os.environ.get('REHABAI_IMU_TRANSPORT', 'auto')  # auto | simulation | udp | serial | off
IMU_UDP_HOST = os.environ.get('REHABAI_IMU_UDP_HOST', '127.0.0.1')
IMU_UDP_PORT = int(os.environ.get('REHABAI_IMU_UDP_PORT', '8766'))
IMU_SERIAL = os.environ.get('REHABAI_IMU_SERIAL', '')
IMU_BAUD = int(os.environ.get('REHABAI_IMU_BAUD', '115200'))
IMU_REQUIRED = os.environ.get('REHABAI_IMU_REQUIRED', '0').lower() in ('1', 'true', 'yes')
IMU_PLACEMENTS = tuple(
    part.strip() for part in os.environ.get('REHABAI_IMU_PLACEMENTS', 'arm').split(',') if part.strip()
) or ('arm',)
