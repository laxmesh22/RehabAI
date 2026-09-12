"""Download cp312 / py3 wheels offline (curl -k) for RehabAI pose venv."""
from __future__ import annotations

import json
import ssl
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.offline-wheels'
OUT.mkdir(exist_ok=True)

PKGS = '''
fastapi uvicorn starlette sqlalchemy pydantic pydantic-core PyJWT httpx python-multipart
Pillow anyio sniffio idna certifi h11 httpcore annotated-types typing-extensions
click colorama greenlet httptools python-dotenv pyyaml watchfiles websockets
absl-py attrs flatbuffers protobuf sounddevice matplotlib contourpy cycler
fonttools kiwisolver packaging pyparsing python-dateutil six
'''.split()

ctx = ssl.create_default_context()


def pick_wheel(files: list[dict]) -> dict | None:
    preferred = None
    for item in files:
        name = item.get('filename') or ''
        if not name.endswith('.whl'):
            continue
        if 'win_amd64' not in name and 'none-any' not in name:
            continue
        if 'cp313' in name or 'cp314' in name or 'cp311' in name or 'cp310' in name:
            # allow abi3 / none-any; skip other CPython minors unless abi3
            if 'abi3' not in name and 'none-any' not in name:
                continue
        if 'cp312' in name or 'py3' in name or 'py2.py3' in name or 'abi3' in name:
            preferred = item
            if 'cp312' in name:
                return item
    return preferred


def main() -> None:
    for name in PKGS:
        meta_url = f'https://pypi.org/pypi/{name}/json'
        try:
            with urllib.request.urlopen(meta_url, context=ctx, timeout=45) as response:
                data = json.load(response)
        except Exception as exc:
            print('meta fail', name, exc)
            continue
        version = data['info']['version']
        files = data.get('urls') or []
        choice = pick_wheel(files)
        if choice is None:
            print('no wheel', name, version)
            continue
        dest = OUT / choice['filename']
        if dest.exists() and dest.stat().st_size > 1000:
            print('have', dest.name)
            continue
        print('get', choice['filename'])
        subprocess.check_call(['curl.exe', '-k', '-L', '-o', str(dest), choice['url']])
    print('done', OUT)


if __name__ == '__main__':
    main()
