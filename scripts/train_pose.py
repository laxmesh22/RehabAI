"""Optional GPU-server entry point; requires a real, separately annotated dataset."""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True, help='Existing local COCO-17 pretrained pose weights')
    parser.add_argument('--data', required=True, help='Ultralytics pose dataset YAML with subject-disjoint splits')
    parser.add_argument('--epochs', type=int, default=30)
    parser.add_argument('--device', default='0')
    parser.add_argument('--output', default='training-runs')
    args = parser.parse_args()
    for path in (args.model, args.data):
        if not Path(path).is_file():
            parser.error('Missing local file: '+path)
    from ultralytics import YOLO
    model = YOLO(args.model, task='pose')
    model.train(data=args.data, epochs=args.epochs, device=args.device, project=args.output,
                name='shoulder-pose', seed=42)
    # Held-out subject evaluation and deployment approval remain explicit separate gates.


if __name__ == '__main__':
    main()
