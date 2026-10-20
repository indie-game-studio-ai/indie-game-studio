#!/usr/bin/env python3
"""Read and write PNG with the standard library only. Pixels are RGBA, 8 bits per channel."""

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PngError(Exception):
    pass


def _chunk(kind, data):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def write_png(path, width, height, rgba):
    rgba = bytes(rgba)
    row_size = width * 4
    if len(rgba) != row_size * height:
        raise PngError("buffer size %d does not match %dx%dx4" % (len(rgba), width, height))
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += rgba[y * row_size:(y + 1) * row_size]
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    with open(path, "wb") as png_file:
        png_file.write(PNG_SIGNATURE)
        png_file.write(_chunk(b"IHDR", header))
        png_file.write(_chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        png_file.write(_chunk(b"IEND", b""))


def _paeth(left, up, up_left):
    estimate = left + up - up_left
    distance_left = abs(estimate - left)
    distance_up = abs(estimate - up)
    distance_up_left = abs(estimate - up_left)
    if distance_left <= distance_up and distance_left <= distance_up_left:
        return left
    if distance_up <= distance_up_left:
        return up
    return up_left


def _unfilter(data, width, height, bytes_per_pixel):
    stride = width * bytes_per_pixel
    result = bytearray(stride * height)
    previous = bytearray(stride)
    position = 0
    for y in range(height):
        filter_type = data[position]
        position += 1
        line = bytearray(data[position:position + stride])
        position += stride
        if filter_type == 1:
            for i in range(bytes_per_pixel, stride):
                line[i] = (line[i] + line[i - bytes_per_pixel]) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                left = line[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                up_left = previous[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
                line[i] = (line[i] + _paeth(left, previous[i], up_left)) & 0xFF
        elif filter_type != 0:
            raise PngError("unknown row filter %d" % filter_type)
        result[y * stride:(y + 1) * stride] = line
        previous = line
    return result


def read_png(path):
    with open(path, "rb") as png_file:
        data = png_file.read()
    if not data.startswith(PNG_SIGNATURE):
        raise PngError("%s: not a PNG file" % path)
    position = len(PNG_SIGNATURE)
    header = None
    palette = b""
    transparency = b""
    compressed = bytearray()
    while position < len(data):
        (length,) = struct.unpack(">I", data[position:position + 4])
        kind = data[position + 4:position + 8]
        body = data[position + 8:position + 8 + length]
        position += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"tRNS":
            transparency = body
        elif kind == b"IDAT":
            compressed += body
        elif kind == b"IEND":
            break
    if header is None:
        raise PngError("%s: missing IHDR" % path)
    width, height, bit_depth, color_type, _, _, interlace = header
    if interlace:
        raise PngError("%s: interlaced PNG is not supported, re-save it without interlacing" % path)
    if bit_depth != 8:
        raise PngError("%s: only 8-bit depth is supported, got %d" % (path, bit_depth))
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise PngError("%s: unknown color type %d" % (path, color_type))
    pixels = _unfilter(zlib.decompress(bytes(compressed)), width, height, channels)
    rgba = bytearray(width * height * 4)
    for i in range(width * height):
        source = i * channels
        target = i * 4
        if color_type == 6:
            rgba[target:target + 4] = pixels[source:source + 4]
        elif color_type == 2:
            rgba[target:target + 3] = pixels[source:source + 3]
            rgba[target + 3] = 255
            if len(transparency) == 6 and pixels[source:source + 3] == bytes(transparency[1::2]):
                rgba[target + 3] = 0
        elif color_type == 0:
            gray = pixels[source]
            rgba[target:target + 4] = bytes((gray, gray, gray, 255))
            if len(transparency) == 2 and gray == transparency[1]:
                rgba[target + 3] = 0
        elif color_type == 4:
            gray = pixels[source]
            rgba[target:target + 4] = bytes((gray, gray, gray, pixels[source + 1]))
        else:
            index = pixels[source]
            rgba[target:target + 3] = palette[index * 3:index * 3 + 3]
            rgba[target + 3] = transparency[index] if index < len(transparency) else 255
    return width, height, rgba


def parse_color(value):
    """None and "transparent" mean a fully transparent pixel."""
    if value is None or value == "transparent":
        return (0, 0, 0, 0)
    text = str(value).strip()
    if not text.startswith("#"):
        raise ValueError("color must start with #: %r" % value)
    digits = text[1:]
    if len(digits) in (3, 4):
        digits = "".join(digit * 2 for digit in digits)
    if len(digits) not in (6, 8):
        raise ValueError("bad color length: %r" % value)
    try:
        channels = [int(digits[i:i + 2], 16) for i in range(0, len(digits), 2)]
    except ValueError:
        raise ValueError("bad hex in color: %r" % value)
    if len(channels) == 3:
        channels.append(255)
    return tuple(channels)


def new_canvas(width, height, color=(0, 0, 0, 0)):
    return bytearray(bytes(color) * (width * height))


def scale_nearest(width, height, rgba, new_width, new_height):
    result = bytearray(new_width * new_height * 4)
    for y in range(new_height):
        source_y = y * height // new_height
        for x in range(new_width):
            source = (source_y * width + x * width // new_width) * 4
            target = (y * new_width + x) * 4
            result[target:target + 4] = rgba[source:source + 4]
    return result


def blit(canvas, canvas_width, canvas_height, image, image_width, image_height, left, top):
    """Composite image over canvas using its alpha (source-over)."""
    for y in range(image_height):
        target_y = top + y
        if target_y < 0 or target_y >= canvas_height:
            continue
        for x in range(image_width):
            target_x = left + x
            if target_x < 0 or target_x >= canvas_width:
                continue
            source = (y * image_width + x) * 4
            alpha = image[source + 3]
            if alpha == 0:
                continue
            target = (target_y * canvas_width + target_x) * 4
            if alpha == 255:
                canvas[target:target + 4] = image[source:source + 4]
                continue
            base_alpha = canvas[target + 3]
            out_alpha = alpha + base_alpha * (255 - alpha) // 255
            for channel in range(3):
                mixed = (image[source + channel] * alpha
                         + canvas[target + channel] * base_alpha * (255 - alpha) // 255)
                canvas[target + channel] = mixed // out_alpha if out_alpha else 0
            canvas[target + 3] = out_alpha


def fill_rect(canvas, canvas_width, canvas_height, left, top, width, height, color):
    pixel = bytes(color)
    for y in range(max(0, top), min(canvas_height, top + height)):
        start = (y * canvas_width + max(0, left)) * 4
        end = (y * canvas_width + min(canvas_width, left + width)) * 4
        if end > start:
            canvas[start:end] = pixel * ((end - start) // 4)


def checkerboard(width, height, cell=8, light=(204, 204, 204, 255), dark=(153, 153, 153, 255)):
    canvas = bytearray(width * height * 4)
    light_pixel = bytes(light)
    dark_pixel = bytes(dark)
    for y in range(height):
        for x in range(width):
            pixel = light_pixel if ((x // cell) + (y // cell)) % 2 == 0 else dark_pixel
            canvas[(y * width + x) * 4:(y * width + x) * 4 + 4] = pixel
    return canvas
