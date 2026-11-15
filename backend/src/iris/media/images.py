import cv2
import numpy as np


def grey(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image


def size_within_width(width: int, height: int, max_width: int) -> tuple[int, int]:
    if width <= max_width:
        return width, height
    return max_width, max(1, round(height * max_width / width))


def shrink_to_width(image: np.ndarray, max_width: int) -> np.ndarray:
    height, width = image.shape[:2]
    size = size_within_width(width, height, max_width)
    if size == (width, height):
        return image
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA)
