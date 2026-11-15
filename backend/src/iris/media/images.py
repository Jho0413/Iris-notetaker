def size_within_width(width: int, height: int, max_width: int) -> tuple[int, int]:
    if width <= max_width:
        return width, height
    return max_width, max(1, round(height * max_width / width))
