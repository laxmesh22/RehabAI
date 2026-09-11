"""Skeleton overlay. Prefers OpenCV; Pillow JPEG is used when cv2 is unavailable."""
import base64
import io

CONNECTIONS = (
    ('left_shoulder', 'right_shoulder'),
    ('left_shoulder', 'left_elbow'),
    ('left_elbow', 'left_wrist'),
    ('right_shoulder', 'right_elbow'),
    ('right_elbow', 'right_wrist'),
    ('left_shoulder', 'left_hip'),
    ('right_shoulder', 'right_hip'),
    ('left_hip', 'right_hip'),
    ('left_shoulder', 'nose'),
    ('right_shoulder', 'nose'),
)

LIME_BGR = (106, 245, 215)
BONE_BGR = (234, 240, 231)
PINE_BGR = (36, 54, 22)


def opencv_available():
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


def overlay_spec(landmarks2d, highlight_side='right'):
    points = {name: (landmark.x, landmark.y, landmark.confidence) for name, landmark in landmarks2d.items()}
    bones = [{'a': a, 'b': b, 'active': highlight_side in (a, b)} for a, b in CONNECTIONS if a in points and b in points]
    return {'points': points, 'bones': bones, 'highlight_side': highlight_side}


def render_rgb(landmarks2d, highlight_side='right', source='simulation', angle=None, feedback='', width=640, height=480):
    """BGR uint8 frame. Simulation is labelled on the pixels themselves."""
    spec = overlay_spec(landmarks2d, highlight_side)
    if opencv_available():
        return _render_cv2(spec, source, angle, feedback, width, height)
    return _render_pillow(spec, source, angle, feedback, width, height)


def _render_cv2(spec, source, angle, feedback, width, height):
    import numpy as np
    import cv2
    frame = np.zeros((height, width, 3), np.uint8)
    frame[:] = PINE_BGR
    cv2.rectangle(frame, (0, height - 70), (width, height), (28, 42, 18), -1)
    cv2.line(frame, (40, height - 80), (width - 40, height - 80), (80, 110, 90), 2)
    banner = 'SYNTHETIC RGB · NOT A CAMERA' if source == 'simulation' else 'LIVE RGB · POSE OVERLAY'
    cv2.putText(frame, banner, (16, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, LIME_BGR, 1, cv2.LINE_AA)
    _draw_skeleton_cv2(cv2, frame, spec, width, height)
    if angle is not None:
        cv2.putText(frame, f'{angle:.0f} deg', (width - 170, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.9, LIME_BGR, 2, cv2.LINE_AA)
    if feedback:
        cv2.putText(frame, feedback[:48], (16, height - 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, BONE_BGR, 1, cv2.LINE_AA)
    return frame


def _draw_skeleton_cv2(cv2, frame, spec, width, height):
    for bone in spec['bones']:
        a, b = spec['points'][bone['a']], spec['points'][bone['b']]
        color = LIME_BGR if bone['active'] else (144, 155, 127)
        p1 = (int(a[0] * width), int(a[1] * height))
        p2 = (int(b[0] * width), int(b[1] * height))
        cv2.line(frame, p1, p2, color, 6, cv2.LINE_AA)
    for name, pt in spec['points'].items():
        color = LIME_BGR if spec['highlight_side'] in name else BONE_BGR
        cv2.circle(frame, (int(pt[0] * width), int(pt[1] * height)), 8, color, -1, cv2.LINE_AA)


def _render_pillow(spec, source, angle, feedback, width, height):
    import numpy as np
    from PIL import Image, ImageDraw
    img = Image.new('RGB', (width, height), (22, 54, 36))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, height - 70, width, height], fill=(18, 42, 28))
    draw.line([(40, height - 80), (width - 40, height - 80)], fill=(90, 110, 80), width=2)
    banner = 'SYNTHETIC RGB · NOT A CAMERA' if source == 'simulation' else 'LIVE RGB · POSE OVERLAY'
    draw.text((16, 14), banner, fill=(215, 245, 106))
    for bone in spec['bones']:
        a, b = spec['points'][bone['a']], spec['points'][bone['b']]
        color = (215, 245, 106) if bone['active'] else (127, 155, 144)
        p1 = (int(a[0] * width), int(a[1] * height))
        p2 = (int(b[0] * width), int(b[1] * height))
        draw.line([p1, p2], fill=color, width=6)
    for name, pt in spec['points'].items():
        color = (215, 245, 106) if spec['highlight_side'] in name else (231, 240, 234)
        x, y = int(pt[0] * width), int(pt[1] * height)
        draw.ellipse([x - 8, y - 8, x + 8, y + 8], fill=color)
    if angle is not None:
        draw.text((width - 170, 48), f'{angle:.0f} deg', fill=(215, 245, 106))
    if feedback:
        draw.text((16, height - 36), feedback[:48], fill=(231, 240, 234))
    rgb = np.array(img)
    return rgb[:, :, ::-1].copy()


def encode_jpeg(frame, quality=72):
    if frame is None:
        return None
    if opencv_available():
        import cv2
        ok, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return buf.tobytes() if ok else None
    from PIL import Image
    rgb = frame[:, :, ::-1]
    out = io.BytesIO()
    Image.fromarray(rgb).save(out, format='JPEG', quality=quality)
    return out.getvalue()


def jpeg_b64(frame, quality=72):
    raw = encode_jpeg(frame, quality)
    if not raw:
        return None
    return base64.b64encode(raw).decode('ascii')
