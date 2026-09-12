"""Manual check that a phone baseline session really reaches OpenCV/MediaPipe.

Posts a synthetic JPEG at the live API and reports what the pose sidecar returned.
This proves the plumbing only. It is not a measurement-accuracy test.
"""
import io
import json
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import memory as memory_mod
from backend.intake import INTAKE_FIELDS, apply_confirmed_value, empty_intake
from backend.profile import apply_profile_value, empty_profile

BASE = 'http://127.0.0.1:8000'


def blank_jpeg(width=640, height=480):
    out = io.BytesIO()
    Image.new('RGB', (width, height), (40, 60, 50)).save(out, format='JPEG')
    return out.getvalue()


def main() -> None:
    client = httpx.Client(base_url=BASE, timeout=30)
    health = client.get('/api/health').json()
    print('phone_pose_available:', health.get('phone_pose_available'), '| pose_kind:', health.get('pose_kind'))
    if not health.get('phone_pose_available'):
        print('FAIL: API still cannot reach the pose backend')
        return

    boot = client.post('/api/consumer/bootstrap', json={'full_name': 'Phone Pose'}).json()
    headers = {'Authorization': 'Bearer ' + boot['token']}
    patient_id = boot['patient_id']
    intake = empty_intake()
    for i, field in enumerate(INTAKE_FIELDS):
        intake = apply_confirmed_value(intake, field, min(i, 4 if 'difficulty' in field else 10), 'voice')
    profile = apply_profile_value(apply_profile_value(apply_profile_value(
        empty_profile(), 'full_name', 'Ravi', 'Ravi'), 'age', 41, '41'), 'affected_side', 'right', 'right')
    memory_mod.save_memory(patient_id, {'intake': intake, 'profile': profile, 'report_phase': 'skipped'})

    for exercise in ('shoulder_abduction', 'shoulder_flexion'):
        started = client.post('/api/sessions', headers=headers, json={
            'patient_id': patient_id, 'exercise_id': exercise, 'side': 'right',
            'goal': 3, 'kind': 'assessment', 'capture': 'phone',
        })
        if started.status_code != 200:
            print(exercise, 'FAILED to start:', started.status_code, started.text)
            continue
        session = started.json()
        print(exercise, '-> session', session['id'], 'source:', session['source'])
        frame = blank_jpeg()
        posted = client.post(
            '/api/sessions/' + session['id'] + '/phone-frame',
            headers=headers, files={'frame': ('frame.jpg', frame, 'image/jpeg')},
        )
        print('   phone-frame:', posted.status_code, posted.text[:200])
        snap = client.get('/api/sessions/' + session['id'], headers=headers).json()
        telemetry = (snap.get('live') or {}).get('telemetry') or {}
        calibration = telemetry.get('calibration') or {}
        print('   capture_profile:', calibration.get('capture_profile'),
              '| plane:', telemetry.get('measurement_plane'),
              '| model:', telemetry.get('model_version'))
        print('   calibration message:', calibration.get('message'))
        client.post('/api/sessions/' + session['id'] + '/finish', headers=headers,
                    json={'pain_after': 3, 'create_assessment': False})
        time.sleep(0.2)
    print('memory baseline:', json.dumps(memory_mod.load_memory(patient_id)['baseline']))


if __name__ == '__main__':
    main()
