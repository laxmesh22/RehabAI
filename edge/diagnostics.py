"""Read-only environment diagnostics; does not install packages or start the camera."""
import importlib.metadata
import json
import platform
from pathlib import Path


def main():
    packages = {}
    for name in ('numpy','opencv-python','mediapipe','pyrealsense2','torch'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    l4t = Path('/etc/nv_tegra_release')
    print(json.dumps(dict(platform=platform.platform(),machine=platform.machine(),python=platform.python_version(),
                         l4t=l4t.read_text().strip() if l4t.exists() else None,packages=packages),indent=2))


if __name__ == '__main__':
    main()
