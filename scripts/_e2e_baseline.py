"""Manual end-to-end check of the Talk -> baseline -> rehab handoff.

Runs against a locally running API. Simulation capture only, so this proves the
wiring and says nothing about camera accuracy on a real shoulder.
"""
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import memory as memory_mod
from backend.intake import INTAKE_FIELDS, apply_confirmed_value, empty_intake
from backend.profile import apply_profile_value, empty_profile

BASE = 'http://127.0.0.1:8000'


def main() -> None:
    client = httpx.Client(base_url=BASE, timeout=30)
    planes = {row['exercise_id']: row.get('phone_plane') for row in client.get('/api/exercises').json()}
    print('planes:', {k: planes[k] for k in ('shoulder_abduction', 'shoulder_flexion')})

    boot = client.post('/api/consumer/bootstrap', json={'full_name': 'Smoke Test'}).json()
    headers = {'Authorization': 'Bearer ' + boot['token']}
    patient_id = boot['patient_id']
    me = client.get('/api/consumer/me', headers=headers).json()
    print('fresh phase:', me['phase'], '| baseline:', json.dumps(me['baseline']))

    intake = empty_intake()
    for i, field in enumerate(INTAKE_FIELDS):
        intake = apply_confirmed_value(intake, field, min(i, 4 if 'difficulty' in field else 10), 'voice')
    profile = apply_profile_value(apply_profile_value(apply_profile_value(
        empty_profile(), 'full_name', 'Ravi', 'Ravi'), 'age', 41, '41'), 'affected_side', 'right', 'right')
    memory_mod.save_memory(patient_id, {'intake': intake, 'profile': profile, 'report_phase': 'skipped'})

    me = client.get('/api/consumer/me', headers=headers).json()
    print('after talk:', me['phase'], '| home_ready:', me['home_ready'],
          '| next:', me['baseline']['next_movement'], me['baseline']['next_exercise_id'])

    for movement, exercise in (('abduction', 'shoulder_abduction'), ('flexion', 'shoulder_flexion')):
        started = client.post('/api/sessions', headers=headers, json={
            'patient_id': patient_id, 'exercise_id': exercise, 'side': 'right',
            'goal': 1, 'kind': 'assessment', 'capture': 'simulation',
        }).json()
        sid = started['id']
        client.post('/api/sessions/' + sid + '/confirm', headers=headers, json={})
        time.sleep(1.0)
        finished = client.post('/api/sessions/' + sid + '/finish', headers=headers, json={
            'pain_after': 4, 'create_assessment': True,
        }).json()
        print('  {}: peak={} source={} next={}'.format(
            movement, finished['session']['peak_angle'], finished['session']['source'],
            finished['baseline']['next_movement'],
        ))

    me = client.get('/api/consumer/me', headers=headers).json()
    print('final phase:', me['phase'], '| home_ready:', me['home_ready'])
    print('baseline:', json.dumps(me['baseline']))


if __name__ == '__main__':
    main()
