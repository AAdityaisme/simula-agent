"""pixelmatch in numpy: which pixels differ between two opaque RGB images, by YIQ color distance, not counting
anti-aliased pixels. The algorithm is mapbox/pixelmatch (ISC), step for step as pixelmatch-py 0.4.0 (ISC) runs it;
tests/test_pixelmatch.py checks it against that package pixel for pixel. The package's per-pixel Python loop took
two thirds of QA's test time."""

import numpy as np

# pixelmatch visits a pixel's neighbours column by column: x outer, y inner. The order decides which of two equally
# dark (or bright) neighbours the anti-aliasing check looks at.
NEIGHBOURS = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)]
# The largest squared YIQ distance two colors can have; pixelmatch's threshold scales it.
MAX_YIQ_DELTA = 35215


def differing(a: np.ndarray, b: np.ndarray, threshold: float) -> np.ndarray:
    """pixelmatch(a, b, threshold, includeAA=False) for two H x W x 3 uint8 images: True where it counts a pixel as
    different."""
    (y1, i1, q1), (y2, i2, q2) = yiq(a), yiq(b)
    y, i, q = y1 - y2, i1 - i2, q1 - q2
    delta = np.where(same_color(a, b), 0.0, 0.5053 * y * y + 0.299 * i * i + 0.1957 * q * q)
    siblings_a, siblings_b = many_siblings(a), many_siblings(b)
    return ((delta > MAX_YIQ_DELTA * threshold * threshold)
            & ~(antialiased(a, siblings_a, siblings_b) | antialiased(b, siblings_b, siblings_a)))


def yiq(a: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r, g, b = (a[..., c].astype(np.float64) for c in range(3))
    return (r * 0.29889531 + g * 0.58662247 + b * 0.11448223,
            r * 0.59597799 - g * 0.27417610 - b * 0.32180189,
            r * 0.21147017 - g * 0.52261711 + b * 0.31114694)


def same_color(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return (a == b).all(axis=-1)


def shifted(a: np.ndarray, dx: int, dy: int, fill=0) -> np.ndarray:
    """a moved so that out[y, x] = a[y + dy, x + dx], with fill where that falls off the image."""
    h, w = a.shape[:2]
    padded = np.pad(a, ((1, 1), (1, 1)) + ((0, 0),) * (a.ndim - 2), constant_values=fill)
    return padded[1 + dy:1 + dy + h, 1 + dx:1 + dx + w]


def on_edge(h: int, w: int) -> np.ndarray:
    """pixelmatch counts the image's edge as one matching neighbour of every pixel on it."""
    edge = np.zeros((h, w), bool)
    edge[0], edge[-1], edge[:, 0], edge[:, -1] = True, True, True, True
    return edge


def many_siblings(a: np.ndarray) -> np.ndarray:
    """For every pixel: 3 or more of its neighbours have exactly its color."""
    h, w = a.shape[:2]
    inside = np.ones((h, w), bool)
    matching = on_edge(h, w).astype(int)
    for dx, dy in NEIGHBOURS:
        matching += shifted(inside, dx, dy, fill=False) & same_color(shifted(a, dx, dy), a)
    return matching > 2


def antialiased(a: np.ndarray, siblings_a: np.ndarray, siblings_b: np.ndarray) -> np.ndarray:
    """For every pixel of a: likely part of anti-aliasing (V. Vysniauskas, 2009). It has at most 2 neighbours of the
    same brightness, both a darker and a brighter one, and its darkest or its brightest neighbour has many siblings in
    both images."""
    h, w = a.shape[:2]
    inside = np.ones((h, w), bool)
    brightness = yiq(a)[0]
    equal = on_edge(h, w).astype(int)
    darker, brighter = [], []
    for dx, dy in NEIGHBOURS:
        there = shifted(inside, dx, dy, fill=False)
        delta = np.where(same_color(shifted(a, dx, dy), a), 0.0, brightness - shifted(brightness, dx, dy))
        equal += there & (delta == 0)
        darker.append(np.where(there & (delta < 0), delta, 0.0))
        brighter.append(np.where(there & (delta > 0), delta, 0.0))
    darker, brighter = np.stack(darker), np.stack(brighter)
    ys, xs = np.indices((h, w))
    offsets = np.array(NEIGHBOURS)

    def siblings_in_both(k: np.ndarray) -> np.ndarray:
        """At the k-th neighbour of each pixel (argmin/argmax keep pixelmatch's first darkest or brightest)."""
        y, x = np.clip(ys + offsets[k, 1], 0, h - 1), np.clip(xs + offsets[k, 0], 0, w - 1)
        return siblings_a[y, x] & siblings_b[y, x]
    return ((equal <= 2) & (darker.min(axis=0) < 0) & (brighter.max(axis=0) > 0)
            & (siblings_in_both(darker.argmin(axis=0)) | siblings_in_both(brighter.argmax(axis=0))))
