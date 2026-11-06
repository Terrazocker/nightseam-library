"""Synthetic star fields for the session-quality tests."""
import io

import numpy as np
from astropy.io import fits


def star_field(seed=1, shape=(600, 900), n=150, sigma=1.6, stretch=1.0, angle=0.4,
               sky=1000.0, noise=12.0, bayer=False, clouds=1.0):
    """Gaussian stars on a flat sky. `stretch` > 1 elongates every star along
    `angle` (tracking trail); `clouds` < 1 dims the stars (thin cloud)."""
    rng = np.random.default_rng(seed)
    h, w = shape
    img = np.full(shape, sky, dtype=np.float64)
    yy, xx = np.mgrid[0:h, 0:w]
    sx, sy = sigma * stretch, sigma
    c, s = np.cos(angle), np.sin(angle)
    half = max(15, int(np.ceil(5 * max(sx, sy))))      # draw each star out to 5 sigma
    margin = half + 5
    for _ in range(n):
        y0, x0 = rng.uniform(margin, h - margin), rng.uniform(margin, w - margin)
        # A star's total light is fixed: seeing or trailing spreads it, it
        # does not add any. Thin cloud dims it.
        amp = rng.uniform(300, 3000) * clouds * (1.6 * 1.6) / (sx * sy)
        y1, x1 = int(y0) - half, int(x0) - half
        box = slice(y1, y1 + 2 * half + 1), slice(x1, x1 + 2 * half + 1)
        dy, dx = yy[box] - y0, xx[box] - x0
        u, v = c * dx + s * dy, -s * dx + c * dy
        img[box] += amp * np.exp(-0.5 * ((u / sx) ** 2 + (v / sy) ** 2))
    img += rng.normal(0, noise, shape)
    if bayer:
        img[0::2, 1::2] *= 0.6
        img[1::2, 0::2] *= 0.8
    return np.clip(img, 0, 65535).astype(np.uint16)


def fits_bytes(data, **header):
    hdu = fits.PrimaryHDU(data)
    for key, value in header.items():
        hdu.header[key] = value
    buffer = io.BytesIO()
    hdu.writeto(buffer)
    return buffer.getvalue()
