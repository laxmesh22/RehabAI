"""E2E: profile voice → clinical → dashboard unlock on local API."""
from __future__ import annotations

import json
import urllib.request
from urllib.parse import urlencode

from backend.memory import default_memory, save_memory

BASE = 'http://127.0.0.1:8000'


def req(method, path, data=None, headers=None):
    h = dict(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h['Content-Type'] = 'application/json'
    r = urllib.request.Request(BASE + path, data=body, headers=h, method=method)
    with urllib.request.urlopen(r, timeout=60) as res:
        return json.loads(res.read().decode())


def main():
    boot = req('POST', '/api/consumer/bootstrap', {'full_name': 'Medha Demo'})
    tok = boot['token']
    pid = boot['patient_id']
    auth = {'Authorization': 'Bearer ' + tok}

    def voice(text='', field=None):
        ctx = {'scene': 'consumer'}
        if field:
            ctx['intake_field'] = field
        form = {
            'language': 'en-IN',
            'speak': '0',
            'patient_id': pid,
            'context': json.dumps(ctx),
            'text': text,
        }
        body = urlencode(form).encode()
        r = urllib.request.Request(
            BASE + '/api/voice/agent',
            data=body,
            headers={**auth, 'Content-Type': 'application/x-www-form-urlencoded'},
            method='POST',
        )
        with urllib.request.urlopen(r, timeout=60) as res:
            return json.loads(res.read().decode())

    save_memory(pid, default_memory())

    a = voice('')
    print('1', a.get('phase'), a.get('intake_field'), (a.get('spoken') or '')[:70])
    assert a['phase'] == 'profile' and a['intake_field'] == 'full_name'

    b = voice('My name is Medha Demo', 'full_name')
    print('2', b.get('phase'), b.get('intake_field'), (b.get('spoken') or '')[:70])
    assert b['intake_field'] == 'age'

    c = voice('32', 'age')
    print('3', c.get('phase'), c.get('intake_field'), (c.get('spoken') or '')[:70])
    assert c['intake_field'] == 'affected_side'

    d = voice('left', 'affected_side')
    print('4', d.get('phase'), d.get('intake_field'), (d.get('spoken') or '')[:70])
    assert d['phase'] == 'questionnaire' and d['intake_field'] == 'pain_rest'

    field = 'pain_rest'
    for val in ('3', '5', '2', '1', '3', '2'):
        row = voice(val, field)
        field = row.get('intake_field')
        print(' clinical', row.get('phase'), row.get('intake_field'), row.get('action'))

    skip = voice('skip')
    print('5', skip.get('action'), skip.get('phase'), (skip.get('spoken') or '')[:80])
    assert skip['action'] == 'open_home'

    r = urllib.request.Request(BASE + '/api/consumer/me', headers=auth)
    with urllib.request.urlopen(r, timeout=30) as res:
        me = json.loads(res.read().decode())
    print(
        '6', me.get('display_name'), me.get('age'), me.get('affected_side'),
        me.get('phase'), me.get('intake_complete'), me.get('report_complete'),
        'recent', len(me.get('recent_sessions') or []),
    )
    assert me['display_name'] == 'Medha'
    assert me['age'] == 32
    assert me['affected_side'] == 'left'
    assert me['intake_complete'] and me['report_complete']
    assert me['phase'] == 'dashboard'
    assert not me.get('is_demo')

    hist = voice('show history')
    print('7', hist.get('action'), (hist.get('spoken') or '')[:80])
    assert hist['action'] == 'open_history'

    start = voice('start session')
    print('8', start.get('action'), (start.get('spoken') or '')[:80])
    assert start['action'] == 'start_session'

    print('E2E_OK')


if __name__ == '__main__':
    main()
