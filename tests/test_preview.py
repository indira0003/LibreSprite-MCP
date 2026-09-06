import base64
import io

from PIL import Image as PILImage

from libresprite_mcp.mcp_server import _png_bytes_from_result


def data_uri(color, size=(2, 2)):
    image = PILImage.new("RGBA", size, color)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def test_png_layer_composition():
    result = {
        "canvas_width": 4,
        "canvas_height": 4,
        "layers": [
            {"x": 0, "y": 0, "png_data_uri": data_uri((255, 0, 0, 255))},
            {"x": 1, "y": 1, "png_data_uri": data_uri((0, 255, 0, 255))},
        ],
    }
    raw = _png_bytes_from_result(result)
    image = PILImage.open(io.BytesIO(raw)).convert("RGBA")
    assert image.size == (4, 4)
    assert image.getpixel((0, 0)) == (255, 0, 0, 255)
    assert image.getpixel((1, 1)) == (0, 255, 0, 255)


def test_resolved_rgba_layer_composition():
    result = {
        "canvas_width": 3,
        "canvas_height": 2,
        "layers": [
            {
                "x": 1,
                "y": 0,
                "width": 2,
                "height": 2,
                "rgba": [
                    255, 0, 0, 255,
                    0, 255, 0, 255,
                    0, 0, 255, 255,
                    255, 255, 255, 255,
                ],
            }
        ],
    }
    raw = _png_bytes_from_result(result)
    image = PILImage.open(io.BytesIO(raw)).convert("RGBA")
    assert image.getpixel((1, 0)) == (255, 0, 0, 255)
    assert image.getpixel((2, 1)) == (255, 255, 255, 255)
