"""
Tests for the deterministic logic in embroidery_mockup.py.

Covers stitch estimation, placement routing, dimension resolution, and PIL
color simplification. No API keys required — all tests run offline.
"""

import sys
import pytest
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

from embroidery_mockup import (
    resolve_dimensions,
    estimate_stitches,
    simplify_with_pil,
    MIN_STITCHES,
    DEFAULT_DENSITY,
    PLACEMENT_DENSITY,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_png(tmp_path, width: int, height: int, color=(255, 0, 0, 255),
             name: str = "test.png") -> str:
    path = str(tmp_path / name)
    Image.new("RGBA", (width, height), color).save(path, format="PNG")
    return path


def make_dims(width_in: float, height_in: float) -> dict:
    """Minimal dims dict for use with estimate_stitches."""
    dpi = 72
    return {
        "width_px":     int(width_in * dpi),
        "height_px":    int(height_in * dpi),
        "width_in":     width_in,
        "height_in":    height_in,
        "aspect_ratio": round(width_in / max(height_in, 0.001), 4),
        "image_format": "png",
        "assumed_dpi":  dpi,
        "file_name":    "test.png",
        "file_stem":    "test",
    }


# ── resolve_dimensions ────────────────────────────────────────────────────────

class TestResolveDimensions:
    def test_pixel_dimensions_returned(self, tmp_path):
        path = make_png(tmp_path, 144, 72)
        result = resolve_dimensions(path)
        assert result["width_px"] == 144
        assert result["height_px"] == 72

    def test_inch_conversion_at_72dpi(self, tmp_path):
        # 144px / 72dpi = 2.0 in, 72px / 72dpi = 1.0 in
        path = make_png(tmp_path, 144, 72)
        result = resolve_dimensions(path)
        assert result["width_in"] == 2.0
        assert result["height_in"] == 1.0

    def test_aspect_ratio(self, tmp_path):
        path = make_png(tmp_path, 216, 72)  # 3:1
        result = resolve_dimensions(path)
        assert result["aspect_ratio"] == pytest.approx(3.0, rel=0.01)

    def test_square_aspect_ratio(self, tmp_path):
        path = make_png(tmp_path, 72, 72)
        result = resolve_dimensions(path)
        assert result["aspect_ratio"] == pytest.approx(1.0)

    def test_format_detected(self, tmp_path):
        path = make_png(tmp_path, 100, 100)
        result = resolve_dimensions(path)
        assert result["image_format"].lower() == "png"

    def test_file_stem_returned(self, tmp_path):
        path = make_png(tmp_path, 100, 100, name="mylogo.png")
        result = resolve_dimensions(path)
        assert result["file_stem"] == "mylogo"


# ── estimate_stitches: MIN_STITCHES floor ─────────────────────────────────────

class TestMinStitchesFloor:
    def test_tiny_logo_hits_floor(self):
        result = estimate_stitches(make_dims(0.3, 0.3), "left chest", color_count=1)
        assert result["estimated_stitches"] == MIN_STITCHES

    def test_adequate_logo_above_floor(self):
        result = estimate_stitches(make_dims(4.0, 3.0), "left chest", color_count=1)
        assert result["estimated_stitches"] > MIN_STITCHES

    def test_zero_area_returns_zero(self):
        result = estimate_stitches(make_dims(0.0, 0.0), "left chest")
        assert result["estimated_stitches"] == 0
        assert result["production_stitch_estimate"] == 0


# ── estimate_stitches: production estimate ────────────────────────────────────

class TestProductionEstimate:
    def test_production_is_115_percent_of_estimated(self):
        result = estimate_stitches(make_dims(4.0, 3.0), "full back", color_count=1)
        assert result["production_stitch_estimate"] == round(result["estimated_stitches"] * 1.15)

    def test_production_zero_when_estimated_zero(self):
        result = estimate_stitches(make_dims(0.0, 0.0), "left chest")
        assert result["production_stitch_estimate"] == 0


# ── estimate_stitches: color change cost ─────────────────────────────────────

class TestColorChangeCost:
    def test_single_color_has_no_change_cost(self):
        result = estimate_stitches(make_dims(4.0, 3.0), "left chest", color_count=1)
        assert result["color_change_cost"] == 0

    def test_each_extra_color_adds_200_stitches(self):
        one = estimate_stitches(make_dims(4.0, 3.0), "left chest", color_count=1)
        four = estimate_stitches(make_dims(4.0, 3.0), "left chest", color_count=4)
        assert four["estimated_stitches"] - one["estimated_stitches"] == (4 - 1) * 200

    def test_color_change_cost_field_matches_formula(self):
        result = estimate_stitches(make_dims(4.0, 3.0), "left chest", color_count=5)
        assert result["color_change_cost"] == (5 - 1) * 200


# ── estimate_stitches: placement density routing ──────────────────────────────

class TestPlacementDensity:
    def test_left_chest_density(self):
        result = estimate_stitches(make_dims(3.5, 2.5), "left chest")
        assert result["stitch_density_used"] == PLACEMENT_DENSITY["left chest"]

    def test_right_chest_density(self):
        result = estimate_stitches(make_dims(3.5, 2.5), "right chest")
        assert result["stitch_density_used"] == PLACEMENT_DENSITY["right chest"]

    def test_full_back_density(self):
        result = estimate_stitches(make_dims(10.0, 8.0), "full back")
        assert result["stitch_density_used"] == PLACEMENT_DENSITY["full back"]

    def test_hat_front_density(self):
        result = estimate_stitches(make_dims(3.0, 2.0), "hat front")
        assert result["stitch_density_used"] == PLACEMENT_DENSITY["hat front"]

    def test_hat_side_density(self):
        result = estimate_stitches(make_dims(2.5, 2.0), "hat side")
        assert result["stitch_density_used"] == PLACEMENT_DENSITY["hat side"]

    def test_sleeve_density(self):
        result = estimate_stitches(make_dims(3.0, 2.0), "sleeve")
        assert result["stitch_density_used"] == PLACEMENT_DENSITY["sleeve"]

    def test_unknown_placement_uses_default_density(self):
        result = estimate_stitches(make_dims(3.0, 3.0), "unknown placement xyz")
        assert result["stitch_density_used"] == DEFAULT_DENSITY

    def test_placement_matching_is_case_insensitive(self):
        lower = estimate_stitches(make_dims(3.0, 3.0), "left chest")
        upper = estimate_stitches(make_dims(3.0, 3.0), "LEFT CHEST")
        assert lower["stitch_density_used"] == upper["stitch_density_used"]

    def test_partial_placement_string_matches(self):
        # "left chest embroidery" should still route to left chest density
        result = estimate_stitches(make_dims(3.0, 3.0), "left chest embroidery")
        assert result["stitch_density_used"] == PLACEMENT_DENSITY["left chest"]

    def test_hat_placement_yields_higher_density_than_back(self):
        hat = estimate_stitches(make_dims(3.0, 3.0), "hat front", color_count=1)
        back = estimate_stitches(make_dims(3.0, 3.0), "full back", color_count=1)
        assert hat["stitch_density_used"] > back["stitch_density_used"]

    def test_hat_placement_adds_logo_pct_bonus(self):
        hat = estimate_stitches(make_dims(3.0, 3.0), "hat front")
        chest = estimate_stitches(make_dims(3.0, 3.0), "left chest")
        assert hat["logo_percentage"] > chest["logo_percentage"]

    def test_output_passthrough_includes_input_dims(self):
        d = make_dims(3.5, 2.5)
        result = estimate_stitches(d, "left chest")
        assert result["width_in"] == 3.5
        assert result["height_in"] == 2.5
        assert result["placement"] == "left chest"


# ── simplify_with_pil ─────────────────────────────────────────────────────────

class TestSimplifyWithPil:
    def _unique_opaque_colors(self, path: str) -> int:
        img = Image.open(path).convert("RGBA")
        return len({px[:3] for px in img.getdata() if px[3] > 0})

    def test_output_file_created(self, tmp_path):
        path = make_png(tmp_path, 100, 100)
        out = simplify_with_pil(path, max_colors=5)
        assert out is not None
        assert Path(out).exists()

    def test_gradient_reduced_to_max_colors(self, tmp_path):
        path = str(tmp_path / "gradient.png")
        img = Image.new("RGB", (256, 10))
        img.putdata([(i, 0, 0) for i in range(256)] * 10)
        img.save(path)
        out = simplify_with_pil(path, max_colors=4)
        assert self._unique_opaque_colors(out) <= 4

    def test_alpha_channel_preserved(self, tmp_path):
        path = str(tmp_path / "transparent.png")
        img = Image.new("RGBA", (50, 50), (255, 0, 0, 0))  # fully transparent
        img.save(path)
        out = simplify_with_pil(path, max_colors=5)
        result = Image.open(out).convert("RGBA")
        assert all(px[3] == 0 for px in result.getdata())

    def test_output_filename_contains_simplified(self, tmp_path):
        path = make_png(tmp_path, 50, 50, name="mylogo.png")
        out = simplify_with_pil(path)
        assert "mylogo_simplified" in Path(out).name

    def test_returns_none_on_missing_file(self):
        result = simplify_with_pil("/nonexistent/path/image.png")
        assert result is None

    def test_respects_max_colors_parameter(self, tmp_path):
        path = str(tmp_path / "multicolor.png")
        img = Image.new("RGB", (100, 100))
        img.putdata([(i * 20, j * 20, 0) for i in range(10) for j in range(10)] * 10)
        img.save(path)
        out = simplify_with_pil(path, max_colors=3)
        assert self._unique_opaque_colors(out) <= 3
