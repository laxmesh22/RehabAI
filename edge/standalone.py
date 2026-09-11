"""Run live camera + pose + 3D movement overlay. Never substitutes mock data."""
import argparse
import json
import logging
import os
from contextlib import ExitStack
from pathlib import Path
from edge.camera.realsense_camera import RealSenseCamera, CameraError
from edge.pose.pose_estimator import MediaPipePoseEstimator
from edge.pipeline import EdgePipeline
from edge.feedback.overlay import draw_overlay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=os.getenv('REHABAI_POSE_MODEL'))
    parser.add_argument('--serial', default=os.getenv('REHABAI_CAMERA_SERIAL'))
    parser.add_argument('--side', choices=['left','right'], default='right')
    parser.add_argument('--exercise', choices=['abduction','flexion'], default='abduction')
    parser.add_argument('--target', type=float, default=80)
    parser.add_argument('--reps', type=int, default=5)
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--max-frames', type=int, default=0)
    parser.add_argument('--telemetry', help='Optional JSONL output; no video is recorded')
    args = parser.parse_args()
    if not args.model or not Path(args.model).is_file():
        parser.error('Supply an existing MediaPipe .task file with --model or REHABAI_POSE_MODEL')
    if not 40 <= args.target <= 120 or not 1 <= args.reps <= 20:
        parser.error('Demo target must be 40–120 degrees and repetitions 1–20')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        with ExitStack() as stack:
            estimator = MediaPipePoseEstimator(args.model)
            stack.callback(estimator.close)
            camera = stack.enter_context(RealSenseCamera(serial=args.serial))
            pipeline = EdgePipeline(estimator, camera.rs.rs2_deproject_pixel_to_point, args.side, args.exercise, args.target,args.reps)
            output = stack.enter_context(open(args.telemetry,'x',encoding='utf-8')) if args.telemetry else None
            if not args.headless:
                import cv2
                stack.callback(cv2.destroyAllWindows)
            count = 0
            logging.info('LIVE only. No video is stored. Stand upright, arm lowered; Q stops.')
            while True:
                frame = camera.read()
                pose, telemetry = pipeline.process(frame)
                if output:
                    output.write(json.dumps(telemetry,allow_nan=False)+'\n')
                    output.flush()
                if not args.headless:
                    cv2.imshow('RehabAI - live assessment',draw_overlay(frame.rgb,pose,telemetry))
                    key = cv2.waitKey(1) & 0xff
                    if key == ord('q'):
                        break
                    if key == ord('c'):
                        pipeline.calibration.reset()
                        pipeline.filter.reset()
                        pipeline.engine.phase = 'reacquire'
                elif count % 30 == 0:
                    logging.info('%s',json.dumps(telemetry,allow_nan=False))
                count += 1
                if telemetry.get('complete') or (args.max_frames and count >= args.max_frames):
                    break
        return 0
    except (CameraError, ImportError, RuntimeError, ValueError, OSError) as exc:
        logging.error('PAUSE: %s. No further measurements will be emitted.',exc)
        return 1
    except KeyboardInterrupt:
        logging.info('Stopped by operator')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
