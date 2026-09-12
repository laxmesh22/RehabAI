"""Seed a consumer who has finished Talk and is waiting on the camera baseline.

Prints the localStorage bootstrap for a manual browser check of #/app/baseline.
"""
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import memory as memory_mod
from backend.intake import INTAKE_FIELDS, apply_confirmed_value, empty_intake
from backend.profile import apply_profile_value, empty_profile


def main() -> None:
    client = httpx.Client(base_url='http://127.0.0.1:8000', timeout=30)
    boot = client.post('/api/consumer/bootstrap', json={'full_name': 'Camera Check'}).json()
    patient_id = boot['patient_id']
    intake = empty_intake()
    for i, field in enumerate(INTAKE_FIELDS):
        intake = apply_confirmed_value(intake, field, min(i, 4 if 'difficulty' in field else 10), 'voice')
    profile = apply_profile_value(apply_profile_value(apply_profile_value(
        empty_profile(), 'full_name', 'Camera Check', 'Camera Check'), 'age', 38, '38'),
        'affected_side', 'right', 'right')
    memory_mod.save_memory(patient_id, {'intake': intake, 'profile': profile, 'report_phase': 'skipped'})

    me = client.get('/api/consumer/me', headers={'Authorization': 'Bearer ' + boot['token']}).json()
    print('phase:', me.get('phase'), '| phone_pose_available:', me.get('phone_pose_available'))
    print('baseline:', json.dumps(me.get('baseline')))
    print('TOKEN=' + boot['token'])
    print('PATIENT=' + patient_id)


if __name__ == '__main__':
    main()
