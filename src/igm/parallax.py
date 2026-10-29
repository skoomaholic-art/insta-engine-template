"""2.5D: из одной картинки — «живой» кадр. Нейросеть MiDaS (бесплатная, работает на CPU) оценивает глубину,
дальше камера плавно наезжает и смещается: близкие объекты движутся сильнее дальних, появляется объём.
"""
import subprocess
import urllib.request
from pathlib import Path

import numpy as np

MODEL_URL = "https://github.com/isl-org/MiDaS/releases/download/v2_1/model-small.onnx"
MODEL_PATH = Path.home() / ".cache" / "igm" / "midas-small.onnx"
W, H, FPS = 1080, 1920, 30

# движения камеры: (наезд, сдвиг x, сдвиг y) за всю сцену; глубина усиливает их для близких объектов
MOVES = [
    (0.16, 0.0, 0.0),     # наезд в кадр
    (0.10, 0.05, 0.0),    # наезд с уходом вправо
    (0.10, -0.05, 0.0),   # наезд с уходом влево
    (0.09, 0.0, -0.045),  # наезд с подъёмом
]


def _session():
    import onnxruntime as ort
    if not MODEL_PATH.exists():
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    return ort.InferenceSession(str(MODEL_PATH), providers=["CPUExecutionProvider"])


_SESSION = None


def depth_map(rgb):
    """rgb: HxWx3 uint8 → глубина 0..1 (1 — ближе к камере), сглаженная."""
    import cv2
    global _SESSION
    _SESSION = _SESSION or _session()
    x = cv2.resize(rgb, (256, 256), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    x = x.transpose(2, 0, 1)[None]
    name = _SESSION.get_inputs()[0].name
    d = _SESSION.run(None, {name: x})[0].reshape(256, 256)
    d = cv2.resize(d, (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_CUBIC)
    lo, hi = np.percentile(d, 2), np.percentile(d, 98)
    d = np.clip((d - lo) / max(1e-6, hi - lo), 0, 1)
    return cv2.GaussianBlur(d, (0, 0), sigmaX=max(3, rgb.shape[1] / 160)).astype(np.float32)


def cover(rgb, w, h):
    """Заполнить кадр w×h без полей (обрезка по центру)."""
    import cv2
    ih, iw = rgb.shape[:2]
    k = max(w / iw, h / ih)
    img = cv2.resize(rgb, (int(round(iw * k)), int(round(ih * k))), interpolation=cv2.INTER_LANCZOS4 if k > 1 else cv2.INTER_AREA)
    y0, x0 = (img.shape[0] - h) // 2, (img.shape[1] - w) // 2
    return img[y0:y0 + h, x0:x0 + w]


def parallax_clip(image_path, out_path, seconds, move=0, dim=0.0, fps=FPS, strength=1.0):
    """Видео-сцена W×H из картинки: объёмное движение камеры. dim — затемнение 0..1 (как в конструкторе)."""
    import cv2
    bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"не открыть {image_path}")
    pad = 1.18  # запас по краям, чтобы при движении не было пустот
    src = cover(bgr, int(W * pad), int(H * pad))
    depth = depth_map(cv2.cvtColor(src, cv2.COLOR_BGR2RGB))
    sh, sw = src.shape[:2]
    gy, gx = np.mgrid[0:H, 0:W].astype(np.float32)
    cx, cy = sw / 2.0, sh / 2.0
    ox, oy = (sw - W) / 2.0, (sh - H) / 2.0
    zoom, mx, my = (v * strength for v in MOVES[move % len(MOVES)])
    frames = max(2, int(round(seconds * fps)))
    proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
                             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(out_path)], stdin=subprocess.PIPE)
    try:
        # глубина в координатах выходного кадра (для малых сдвигов точности хватает)
        d = depth[int(oy):int(oy) + H, int(ox):int(ox) + W]
        weight = 0.35 + 0.65 * d  # дальний план движется на треть, ближний полностью
        for f in range(frames):
            t = f / (frames - 1)
            e = t * t * (3 - 2 * t)  # плавный старт и финиш
            s = 1 + zoom * e * weight
            mapx = (gx + ox - cx) / s + cx - mx * W * e * weight
            mapy = (gy + oy - cy) / s + cy - my * H * e * weight
            frame = cv2.remap(src, mapx, mapy, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
            if dim > 0:
                frame = (frame.astype(np.float32) * (1 - dim)).astype(np.uint8)
            proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
    if proc.returncode:
        raise RuntimeError("ffmpeg не собрал сцену")
    return out_path
