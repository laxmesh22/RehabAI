"""In-process edge session. Hardware failure never falls back to silent simulation."""
import json
import math
import threading
import time
from pathlib import Path
from backend.config import (
    IMU_BAUD, IMU_PLACEMENTS, IMU_REQUIRED, IMU_SERIAL, IMU_TRANSPORT,
    IMU_UDP_HOST, IMU_UDP_PORT, MEASUREMENT_SOURCE, PHONE_FRAME_MIN_INTERVAL_S,
    POSE_DEVICE, POSE_KIND, POSE_MODEL, STORAGE_DIR,
)
from edge.imu.device import open_imu, read_imu
from edge.pipeline import VisionPipeline
from edge.simulation import SimulatedPatient


class LiveHub:
    def __init__(self):
        self.lock = threading.RLock()
        self.sessions = {}
        self.abandoned_ids = []

    def start(self, session_id, patient_id, exercise_id, side, target, goal, source=None, consent=False):
        source = source or MEASUREMENT_SOURCE
        if source == 'live' and not POSE_MODEL:
            raise ValueError('Live mode requires REHABAI_POSE_MODEL')
        if source == 'phone':
            from edge.phone_capture import phone_pose_available
            if not phone_pose_available():
                raise ValueError('Phone capture requires REHABAI_POSE_KIND=opencv (or a pose model / inference URL)')
        # Single-station: clear any leftover calibrating/running session (refresh / crash).
        self.stop_active(except_id=session_id)
        with self.lock:
            for item in self.sessions.values():
                if item['status'] in ('calibrating', 'running') and item['session_id'] != session_id:
                    raise ValueError('Another live session is already running on this station')
            imu = open_imu(source, IMU_TRANSPORT, IMU_UDP_HOST, IMU_UDP_PORT, IMU_SERIAL, IMU_BAUD, IMU_PLACEMENTS)
            if IMU_REQUIRED and source == 'live' and imu is None:
                raise ValueError('Live dual-sensor mode requires REHABAI_IMU_TRANSPORT=udp or serial')
            pipeline = VisionPipeline(session_id, exercise_id, side, target, goal, source)
            movement = pipeline.spec['movement']
            actor = None
            capture = None
            phone_estimator = None
            if source == 'simulation':
                actor = SimulatedPatient(side, movement)
            elif source == 'phone':
                from edge.phone_capture import PhonePoseEstimator
                phone_estimator = PhonePoseEstimator()
            else:
                from edge.live_source import LiveCapture
                capture = LiveCapture(POSE_MODEL, POSE_KIND, POSE_DEVICE)
            now = time.monotonic()
            state = {
                'session_id': session_id, 'patient_id': patient_id, 'exercise_id': exercise_id, 'side': side,
                'target': target, 'goal': goal, 'source': source, 'consent': consent, 'status': 'calibrating',
                'pipeline': pipeline, 'actor': actor, 'capture': capture, 'phone_estimator': phone_estimator,
                'imu': imu, 'history': [], 'history_offset': 0, 'fault': 'none',
                'stop': threading.Event(), 'started': now, 'motion_t0': now, 'blocked': False,
                'preview_jpeg': None, 'preview_b64': None,
                'phone_lock': threading.Lock(), 'last_phone_frame_at': None,
            }
            thread = threading.Thread(target=self._run, args=(state,), daemon=True)
            state['thread'] = thread
            self.sessions[session_id] = state
            thread.start()
            return state

    def stop_active(self, except_id=None):
        """Stop calibrating/running sessions so a station can start fresh after a refresh."""
        with self.lock:
            active = [
                sid for sid, item in self.sessions.items()
                if item['status'] in ('calibrating', 'running', 'blocked', 'finishing')
                and sid != except_id
            ]
            self.abandoned_ids = list(active)
        for sid in active:
            try:
                self.finish(sid)
            except Exception:
                with self.lock:
                    state = self.sessions.pop(sid, None)
                if state is not None:
                    try:
                        state['stop'].set()
                    except Exception:
                        pass
                    for key in ('phone_estimator', 'capture', 'imu'):
                        resource = state.get(key)
                        if resource is not None and hasattr(resource, 'close'):
                            try:
                                resource.close()
                            except Exception:
                                pass

    def ingest_phone_frame(self, session_id, jpeg_bytes: bytes):
        if not jpeg_bytes:
            raise ValueError('Empty phone frame')
        from edge.phone_capture import phone_pose_available
        if not phone_pose_available():
            raise ValueError('Phone capture requires REHABAI_POSE_KIND=opencv (or a pose model / inference URL)')
        with self.lock:
            state = self.sessions.get(session_id)
            if state is None:
                raise KeyError(session_id)
            if state['source'] == 'simulation':
                raise ValueError('Simulation sessions cannot ingest phone frames')
            if state['source'] != 'phone':
                raise ValueError('Session does not accept phone frames')
            if state.get('phone_estimator') is None:
                raise ValueError('Phone pose is not available')
            if state['status'] not in ('calibrating', 'running'):
                raise ValueError('Session is not accepting frames')
            estimator = state['phone_estimator']
            started = state['started']
            now = time.monotonic()
            last = state.get('last_phone_frame_at')
            if last is not None and now - last < PHONE_FRAME_MIN_INTERVAL_S:
                raise PhoneFrameRateLimit('Phone frames are arriving too quickly')
            state['last_phone_frame_at'] = now
            phone_lock = state['phone_lock']
        with phone_lock:
            with self.lock:
                if state['status'] not in ('calibrating', 'running'):
                    raise ValueError('Session is not accepting frames')
            elapsed = time.monotonic() - started
            snapshot = estimator.snapshot_from_jpeg(jpeg_bytes, elapsed)
            snapshot['imu_required'] = False
            snapshot['imu'] = None
            return self._append_snapshot(state, snapshot)

    def _append_snapshot(self, state, snapshot):
        row = state['pipeline'].process_snapshot(snapshot)
        row['session_status'] = state['status']
        preview_b64 = row.pop('frame_jpeg', None)
        with self.lock:
            # Phone video stays on the phone. The server retains only landmarks and metrics.
            if preview_b64 and state['source'] != 'phone':
                import base64
                state['preview_b64'] = preview_b64
                state['preview_jpeg'] = base64.b64decode(preview_b64)
            state['history'].append(row)
            if len(state['history']) > 4000:
                del state['history'][:1000]
                state['history_offset'] += 1000
            if row.get('safety', {}).get('level') == 'BLOCK':
                state['blocked'] = True
                state['status'] = 'blocked'
            return row

    def set_fault(self, session_id, fault):
        with self.lock:
            state = self.sessions[session_id]
            if state['source'] != 'simulation':
                raise ValueError('Fault injection is only available in simulation')
            if fault not in ('none', 'lean', 'occlusion', 'lost', 'imu_drop'):
                raise ValueError('Unknown simulation fault')
            state['fault'] = fault
            if state['actor']:
                state['actor'].set_fault('none' if fault == 'imu_drop' else fault)

    def confirm(self, session_id):
        with self.lock:
            state = self.sessions[session_id]
            if state['status'] != 'calibrating':
                raise ValueError('Session is not awaiting tracking confirmation')
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

    def history_since(self, session_id, sequence):
        """Return unseen telemetry without copying the complete session on every poll."""
        with self.lock:
            state = self.sessions[session_id]
            offset = state.get('history_offset', 0)
            start = max(0, int(sequence) - offset)
            items = list(state['history'][start:])
            if items:
                items[-1] = _with_preview(state, items[-1])
            return items, offset + len(state['history'])

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
            state['status'] = 'finishing'
        state['thread'].join(timeout=3)
        phone_lock = state.get('phone_lock')
        if phone_lock:
            phone_lock.acquire()
        try:
            with self.lock:
                state['status'] = 'stopped'
                history = list(state['history'])
        finally:
            if phone_lock:
                phone_lock.release()
        with self.lock:
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
            if state.get('phone_estimator'):
                try:
                    state['phone_estimator'].close()
                except Exception:
                    pass
            if state.get('imu'):
                try:
                    state['imu'].close()
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
            self.sessions.pop(session_id, None)
            return summary, history

    def pause(self, session_id):
        """Stop counting without saving. Debrief/finish can still persist the samples."""
        with self.lock:
            state = self.sessions.get(session_id)
            if state is None:
                raise KeyError(session_id)
            if state['status'] in ('stopped', 'finishing'):
                return
            state['blocked'] = True
            state['stop'].set()

    def take_abandoned(self):
        with self.lock:
            ids = list(getattr(self, 'abandoned_ids', []) or [])
            self.abandoned_ids = []
            return ids

    def _run(self, state):
        while not state['stop'].is_set():
            if state.get('phone_estimator') is not None and state.get('actor') is None and state.get('capture') is None:
                # Phone mode waits for ingest_phone_frame; do not invent frames.
                time.sleep(0.1)
                continue
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
                snapshot['imu_required'] = IMU_REQUIRED
                try:
                    snapshot['imu'] = read_imu(
                        state.get('imu'), elapsed, state['source'],
                        angle=None if state['actor'] is None else state['actor'].angle,
                        side=state['side'], drop=state['fault'] == 'imu_drop',
                    )
                except Exception:
                    from edge.imu.packets import lost_packet
                    snapshot['imu'] = lost_packet(state['source']) if state.get('imu') else None
            except Exception as exc:
                snapshot = {'people': 0, 'points': {}, 'landmarks2d': {}, 'confidence': 0, 'source': state['source'],
                            'model_version': 'unavailable', 'timestamp': elapsed, 'simulation': state['source'] == 'simulation',
                            'camera_ok': False, 'depth_ok': False, 'source_error': 'camera_lost', 'error': str(exc)}
            self._append_snapshot(state, snapshot)
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


class PhoneFrameRateLimit(ValueError):
    pass
