"""Standalone edge prototype: skeleton + shoulder angle, no chatbot."""
import argparse
import json
import math
import time
from edge.pipeline import VisionPipeline
from edge.simulation import SimulatedPatient


def wave(elapsed, target):
    return (1 - math.cos(elapsed * math.pi / 3)) * min(145, target + 10) / 2


def main():
    parser = argparse.ArgumentParser(description='RehabAI edge measurement prototype')
    parser.add_argument('--source', choices=['simulation', 'live'], default='simulation')
    parser.add_argument('--exercise', default='shoulder_abduction')
    parser.add_argument('--side', choices=['left', 'right'], default='right')
    parser.add_argument('--target', type=float, default=80)
    parser.add_argument('--goal', type=int, default=5)
    parser.add_argument('--model', help='Local pose model path, required for live mode')
    parser.add_argument('--pose', choices=['mediapipe', 'yolo'], default='mediapipe')
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--seconds', type=float, default=12)
    args = parser.parse_args()
    pipeline = VisionPipeline('PROTO', args.exercise, args.side, args.target, args.goal, args.source)
    if args.source == 'simulation':
        movement = 'abduction' if 'abduction' in args.exercise else 'flexion' if 'flexion' in args.exercise or args.exercise == 'wall_climb' else 'elevation'
        patient = SimulatedPatient(args.side, movement)
        started = time.monotonic()
        while time.monotonic() - started < args.seconds:
            elapsed = time.monotonic() - started
            patient.set_angle(wave(elapsed, args.target))
            snapshot = patient.skeleton(elapsed)
            row = pipeline.process_snapshot(snapshot)
            print(json.dumps({k: row[k] for k in ('timestamp', 'exercise', 'shoulder_angle', 'torso_lean',
                                                   'pose_confidence', 'rep', 'exercise_phase', 'feedback', 'source')}),
                  flush=True)
            time.sleep(0.1)
        return
    if not args.model:
        parser.error('--source live requires --model')
    from edge.live_source import LiveCapture
    capture = LiveCapture(args.model, args.pose, args.device)
    try:
        started = time.monotonic()
        while time.monotonic() - started < args.seconds:
            snapshot = capture.snapshot()
            snapshot['timestamp'] = time.monotonic() - started
            row = pipeline.process_snapshot(snapshot)
            print(json.dumps({k: row[k] for k in ('timestamp', 'exercise', 'shoulder_angle', 'torso_lean',
                                                   'pose_confidence', 'rep', 'exercise_phase', 'feedback', 'source')}),
                  flush=True)
    finally:
        capture.close()


if __name__ == '__main__':
    main()
