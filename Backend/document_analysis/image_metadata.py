"""Keep known physical image resolution through rotation and cropping."""
import math


def png_resolution(image, orientation=1):
    """Return Pillow save options; never guess DPI for an image without it."""
    value = image.info.get('dpi')
    try:
        if value is None or len(value) != 2:
            return {}
        x, y = map(float, value)
        if not all(math.isfinite(v) and 0 < v <= 0xFFFFFFFF * .0254 for v in (x, y)):
            return {}
    except (TypeError, ValueError, OverflowError):
        return {}
    if orientation in {5, 6, 7, 8}:
        x, y = y, x
    return {'dpi': (x, y)}
