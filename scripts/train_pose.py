"""Train/evaluate a phone pose model from a real, separately annotated dataset."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True, help='Existing local COCO-17 pretrained pose weights')
    parser.add_argument('--data', required=True, help='Ultralytics pose dataset YAML with subject-disjoint splits')
    parser.add_argument('--epochs', type=int, default=30)
    parser.add_argument('--device', default='0')
    parser.add_argument('--output', default='training-runs')
    parser.add_argument('--imgsz', type=int, default=640)
    parser.add_argument('--batch', type=int, default=16)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--patience', type=int, default=15)
    parser.add_argument('--export', choices=('none', 'onnx'), default='onnx')
    args = parser.parse_args()
    for path in (args.model, args.data):
        if not Path(path).is_file():
            parser.error('Missing local file: '+path)
    from ultralytics import YOLO
    model = YOLO(args.model, task='pose')
    run = model.train(
        data=args.data, epochs=args.epochs, device=args.device, project=args.output,
        name='shoulder-pose', seed=42, deterministic=True, imgsz=args.imgsz,
        batch=args.batch, workers=args.workers, patience=args.patience,
    )
    best = Path(run.save_dir) / 'weights' / 'best.pt'
    evaluated = YOLO(str(best), task='pose')
    metrics = evaluated.val(data=args.data, split='test', device=args.device, imgsz=args.imgsz)
    exported = None if args.export == 'none' else str(evaluated.export(format=args.export, imgsz=args.imgsz))
    manifest = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'dataset_yaml': str(Path(args.data).resolve()),
        'split_requirement': 'subject-disjoint train/val/test; verify outside this script',
        'best_weights': str(best.resolve()),
        'exported_model': exported,
        'pose_map_50': getattr(getattr(metrics, 'pose', None), 'map50', None),
        'pose_map_50_95': getattr(getattr(metrics, 'pose', None), 'map', None),
        'hardware_validated': False,
        'clinical_validated': False,
    }
    Path(run.save_dir, 'deployment-manifest.json').write_text(json.dumps(manifest, indent=2, default=float))
    print(json.dumps(manifest, indent=2, default=float))


if __name__ == '__main__':
    main()
