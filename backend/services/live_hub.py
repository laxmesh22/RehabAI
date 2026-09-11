"""In-process edge session. Hardware failure never falls back to silent simulation."""
import json
import math
import threading
import time
from pathlib import Path
from backend.config import MEASUREMENT_SOURCE, POSE_DEVICE, POSE_KIND, POSE_MODEL, STORAGE_DIR
from edge.pipeline import VisionPipeline
from edge.simulation import SimulatedPatient


class LiveHub:
    def __init__(self):
        self.lock = threading.RLock()
        self.sessions = {}

    def start(self, session_id, patient_id, exercise_id, side, target, goal, source=None, consent=False):
        source = source or MEASUREMENT_SOURCE
        if source == 'live' and not POSE_MODEL:
            raise ValueError('Live mode requires REHABAI_POSE_MODEL')
        with self.lock:
            for item in self.sessions.values():
                if item['status'] == 'running':
                    raise ValueError('Another live session is already running on this station')
            pipeline = VisionPipeline(session_id, exercise_id, side, target, goal, source)
            movement = pipeline.spec['movement']
            actor = None
            capture = None
            if source == 'simulation':
                actor = SimulatedPatient(side, movement)
            else:
                from edge.live_source import LiveCapture
                capture = LiveCapture(POSE_MODEL, POSE_KIND, POSE_DEVICE)
            now = time.monotonic()
            state = {
                'session_id': session_id, 'patient_id': patient_id, 'exercise_id': exercise_id, 'side': side,
                'target': target, 'goal': goal, 'source': source, 'consent': consent, 'status': 'calibrating',
                'pipeline': pipeline, 'actor': actor, 'capture': capture, 'history': [], 'fault': 'none',
                'stop': threading.Event(), 'started': now, 'motion_t0': now, 'blocked': False,
                'preview_jpeg': None, 'preview_b64': None,
            }
            thread = threading.Thread(target=self._run, args=(state,), daemon=True)
            state['thread'] = thread
            self.sessions[session_id] = state
            thread.start()
            return state

    def set_fault(self, session_id, fault):
        with self.lock:
            state = self.sessions[session_id]
            if state['source'] != 'simulation':
                raise ValueError('Fault injection is only available in simulation')
            state['fault'] = fault
            if state['actor']:
                state['actor'].set_fault(fault)

    def confirm(self, session_id):
        with self.lock:
            state = self.sessions[session_id]
            latest = state['history'][-1] if state['history'] else None
            if not latest or not latest.get('calibration', {}).get('ready'):
                raise ValueError('Tracking quality is not acceptable yet')
            state['status'] = 'running'
            state['motion_t0'] = time.monotonic()

    def latest(self, session_id):
        with self.lock:
            state = self.sessions.get(session_id)
            if state is None:
                return None
            return {
                'status': state['status'],
                'source': state['source'],
                'blocked': state['blocked'],
                'telemetry': _with_preview(state, state['history'][-1] if state['history'] else None),
                'samples': len(state['history']),
                'has_preview': bool(state.get('preview_jpeg')),
            }

    def history(self, session_id):
        with self.lock:
            items = list(self.sessions[session_id]['history'])
            if items:
                items[-1] = _with_preview(self.sessions[session_id], items[-1])
            return items

    def preview_jpeg(self, session_id):
        with self.lock:
            state = self.sessions.get(session_id)
            return None if state is None else state.get('preview_jpeg')

    def finish(self, session_id):
        with self.lock:
            state = self.sessions.get(session_id)
            if state is None:
                raise KeyError(session_id)
            state['stop'].set()
        state['thread'].join(timeout=3)
        with self.lock:
            state['status'] = 'stopped'
            history = list(state['history'])
            pipeline = state['pipeline']
            path = None
            if state['consent']:
                folder = STORAGE_DIR / 'patients' / state['patient_id'] / 'sessions' / session_id
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / 'telemetry.json'
                slim = [{k: v for k, v in row.items() if k != 'frame_jpeg'} for row in history]
                path.write_text(json.dumps({
                    'session_id': session_id, 'source': state['source'],
                    'model_version': history[-1].get('model_version') if history else None,
                    'samples': slim,
                }, indent=2, default=str))
                if state.get('preview_jpeg'):
                    skeleton = folder / 'skeleton.jpg'
                    skeleton.write_bytes(state['preview_jpeg'])
                    skeleton_path = str(skeleton)
                else:
                    skeleton_path = None
            else:
                skeleton_path = None
            if state['capture']:
                try:
                    state['capture'].close()
                except Exception:
                    pass
            summary = {
                'reps': pipeline.machine.reps,
                'invalid_reps': pipeline.machine.invalid_reps,
                'peak': pipeline.machine.peak,
                'coverage': round(100 * pipeline.machine.valid / max(1, pipeline.machine.total)),
                'events': list(pipeline.events),
                'safety': pipeline.last_safety.as_dict(),
                'source': state['source'],
                'model_version': history[-1].get('model_version') if history else 'unknown',
                'telemetry_path': None if path is None else str(path),
                'skeleton_path': skeleton_path,
                'duration': round(time.monotonic() - state['started'], 1),
                'samples': len(history),
            }
            return summary, history

    def _run(self, state):
        while not state['stop'].is_set():
            elapsed = time.monotonic() - state['started']
            try:
                if state['actor']:
                    if state['status'] == 'running':
                        state['actor'].set_angle(_wave(time.monotonic() - state['motion_t0'], state['target']))
                    else:
                        state['actor'].set_angle(8)
                    snapshot = state['actor'].skeleton(elapsed)
                else:
                    snapshot = state['capture'].snapshot()
                    snapshot['timestamp'] = elapsed
            except Exception as exc:
                snapshot = {'people': 0, 'points': {}, 'landmarks2d': {}, 'confidence': 0, 'source': state['source'],
                            'model_version': 'unavailable', 'timestamp': elapsed, 'simulation': state['source'] == 'simulation',
                            'camera_ok': False, 'depth_ok': False, 'source_error': 'camera_lost', 'error': str(exc)}
            row = state['pipeline'].process_snapshot(snapshot)
            row['session_status'] = state['status']
            preview_b64 = row.pop('frame_jpeg', None)
            with self.lock:
                if preview_b64:
                    import base64
                    state['preview_b64'] = preview_b64
                    state['preview_jpeg'] = base64.b64decode(preview_b64)
                state['history'].append(row)
                if len(state['history']) > 4000:
                    del state['history'][:1000]
                if row.get('safety', {}).get('level') == 'BLOCK':
                    state['blocked'] = True
                    state['status'] = 'blocked'
            if state['blocked']:
                break
            time.sleep(0.1)


def _with_preview(state, telemetry):
    if telemetry is None:
        return None
    if not state.get('preview_b64'):
        return telemetry
    payload = dict(telemetry)
    payload['frame_jpeg'] = state['preview_b64']
    return payload


def _wave(elapsed, target):
    return (1 - math.cos(elapsed * math.pi / 3)) * min(145, target + 12) / 2


HUB = LiveHub()
