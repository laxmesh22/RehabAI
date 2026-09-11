"""Local-only prototype server. Standard-library runtime, no cloud dependency."""
import argparse
import json
import math
import mimetypes
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from backend.engine import SessionEngine

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data' / 'sessions'
LOCK = threading.RLock()
ACTIVE = {}
PROVIDER = None


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, payload):
        raw = json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/api/health':
            return self.respond(200, dict(status='ok', source='live' if PROVIDER else 'simulation', live_available=PROVIDER is not None))
        if path == '/api/active':
            with LOCK:
                active = next(iter(ACTIVE.values()), None)
                return self.respond(200, active['metadata'] if active else None)
        if path == '/api/sessions':
            with LOCK:
                records = [json.loads(p.read_text()) for p in sorted(DATA.glob('*.json'), reverse=True)]
            return self.respond(200, records)
        filename = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}.get(path)
        if not filename:
            return self.respond(404, {'error': 'Not found'})
        raw = (ROOT / 'dist' / filename).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(filename)[0] or 'text/plain')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        # Browser writes must originate from this loopback application.
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            return self.respond(403, {'error': 'Cross-origin writes rejected'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size < 0 or size > 16384:
                raise ValueError('Request too large')
            body = json.loads(self.rfile.read(size) or b'{}')
            if not isinstance(body, dict):
                raise ValueError('Expected a JSON object')
            with LOCK:
                result = self.handle_post(body)
            self.respond(200, result)
        except (ValueError, KeyError, TypeError) as exc:
            self.respond(400, {'error': str(exc)})

    def handle_post(self, body):
        if self.path == '/api/sessions/start':
            if ACTIVE:
                raise ValueError('A session is already active. Reload the page to recover it or restart the server.')
            exercise = body.get('exercise')
            side = body.get('side')
            target, goal, pain = float(body.get('target', 80)), float(body.get('goal', 5)), float(body.get('pain', 0))
            if exercise not in ('abduction', 'flexion') or side not in ('left', 'right'):
                raise ValueError('Invalid protocol')
            if not (40 <= target <= 120 and 1 <= goal <= 20 and goal.is_integer() and 0 <= pain <= 10 and pain.is_integer()):
                raise ValueError('Configuration outside supported demonstration range')
            goal, pain = int(goal), int(pain)
            sid = uuid.uuid4().hex
            ACTIVE[sid] = dict(engine=SessionEngine(target, goal), start=time.monotonic(), tick=0,
                               metadata=dict(id=sid, exercise=exercise, side=side, target=target, goal=goal,
                                             pain_before=pain, source='live' if PROVIDER else 'simulation', model_version=PROVIDER.version if PROVIDER else 'synthetic-v1',
                                             created_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())))
            return dict(id=sid)
        sid = body['id']
        session = ACTIVE[sid]
        if self.path == '/api/sessions/sample':
            session['tick'] += 1
            t = time.monotonic()-session['start']
            if PROVIDER:
                sample = PROVIDER.sample(session['metadata']['side'])
                # A cached frame is never counted twice. Polling faster than inference is OK.
                captured = sample.get('captured')
                if captured and captured == session.get('last_capture'):
                    return session['last_result']
                session['last_capture'] = captured
                result = session['engine'].update(t, sample['angle'], sample['lean'], sample['confidence'])
                if sample['message']:
                    result['feedback'] = sample['message']+'. Lower your arm to reset.'
                session['last_result'] = result
                return result
            angle = (1-math.cos(t*math.pi/3))*min(145, session['engine'].target+14)/2
            fault = body.get('fault', 'none')
            return session['engine'].update(t, angle, 24 if fault == 'lean' else 3,
                                             .2 if fault == 'occlusion' else .96)
        if self.path == '/api/sessions/finish':
            pain = float(body.get('pain', 0))
            if not (0 <= pain <= 10 and pain.is_integer()):
                raise ValueError('Pain must be 0–10')
            pain = int(pain)
            engine = session['engine']
            report = dict(**session['metadata'], pain_after=pain, repetitions=engine.reps,
                          peak=round(engine.peak, 1), coverage=round(100*engine.valid/max(1, engine.total)),
                          duration=round(time.monotonic()-session['start']), samples=engine.history,
                          reason=body.get('reason', 'user_stop'))
            DATA.mkdir(parents=True, exist_ok=True)
            destination = DATA / (session['metadata']['created_at'].replace(':', '-')+'_'+sid+'.json')
            temp = destination.with_suffix('.tmp')
            temp.write_text(json.dumps(report, indent=2, allow_nan=False))
            temp.replace(destination)
            del ACTIVE[sid]
            return report
        raise ValueError('Unknown operation')


def main():
    global PROVIDER
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--source', choices=['simulation', 'live'], default='simulation')
    parser.add_argument('--model', help='Existing local COCO-17 pose weights, required for live mode')
    parser.add_argument('--device', default='cpu', help='Ultralytics inference device, e.g. cpu or 0')
    args = parser.parse_args()
    if args.source == 'live':
        if not args.model:
            parser.error('--source live requires --model')
        from backend.live import RealSensePose
        PROVIDER = RealSensePose(args.model, args.device)
    print(f'RehabAI running at http://127.0.0.1:{args.port}', flush=True)
    try:
        ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
    finally:
        if PROVIDER:
            PROVIDER.close()


if __name__ == '__main__':
    main()
