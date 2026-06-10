"""
embroidery_mockup.py
====================
Standalone Python equivalent of the n8n AI Embroidery Mockup Generator workflow.

Usage:
    python embroidery_mockup.py --image logo.png --placement "left chest"
    python embroidery_mockup.py --image logo.png --placement "hat front" --output mockup.png
    python embroidery_mockup.py --image logo.png --placement "left chest" --no-simplify

Requirements:
    pip install openai requests pillow python-dotenv

Environment variables (set in .env or shell):
    OPENAI_API_KEY=sk-...
    OUTPUT_DIR=./mockups          # Where to save output files
"""

import os
import sys
import json
import base64
import argparse
import logging
from pathlib import Path

import requests
from PIL import Image
from openai import OpenAI

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s"
)
log = logging.getLogger(__name__)

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
ASSUMED_DPI = 72
MIN_STITCHES = 4500

PLACEMENT_DENSITY = {
    "left chest":    1850,
    "right chest":   1850,
    "full back":     1700,
    "back":          1700,
    "hat front":     2200,
    "hat side":      2200,
    "hat back":      2200,
    "cap front":     2200,
    "front center":  2200,
    "center front":  2200,
    "sleeve":        1800,
    "left sleeve":   1800,
    "right sleeve":  1800,
}
DEFAULT_DENSITY = 1800


# ── STEP 1: RESOLVE IMAGE DIMENSIONS ─────────────────────────────────────────
def resolve_dimensions(image_path: str) -> dict:
    """Read image and compute physical dimensions at assumed DPI."""
    img = Image.open(image_path)
    w_px, h_px = img.size
    fmt = img.format or Path(image_path).suffix.lstrip(".").upper()

    width_in  = round(w_px / ASSUMED_DPI, 2)
    height_in = round(h_px / ASSUMED_DPI, 2)
    aspect    = round(w_px / max(h_px, 1), 4)

    log.info(f"  Image: {w_px}x{h_px}px  {width_in}\"x{height_in}\"  aspect={aspect}  format={fmt}")

    return {
        "width_px":       w_px,
        "height_px":      h_px,
        "width_in":       width_in,
        "height_in":      height_in,
        "aspect_ratio":   aspect,
        "image_format":   fmt.lower(),
        "assumed_dpi":    ASSUMED_DPI,
        "file_name":      Path(image_path).name,
        "file_stem":      Path(image_path).stem,
    }


# ── STEP 2: ESTIMATE STITCH COUNT ─────────────────────────────────────────────
def estimate_stitches(dims: dict, placement: str, color_count: int = 3) -> dict:
    """Rule-based stitch count estimation — matches n8n workflow logic."""
    placement_lower = placement.lower().strip()
    width_in  = dims["width_in"]
    height_in = dims["height_in"]
    total_area = round(width_in * height_in, 2)

    # Logo coverage estimate (% of bounding box actually filled with design)
    logo_pct = 32  # default
    if any(x in placement_lower for x in ["hat", "cap"]):
        logo_pct += 4
    logo_pct = max(18, min(45, logo_pct))
    logo_area = round(total_area * logo_pct / 100, 2)

    # Density by placement
    density = DEFAULT_DENSITY
    for key, val in PLACEMENT_DENSITY.items():
        if key in placement_lower:
            density = val
            break
    density = max(1600, min(2600, density))

    # Base stitch estimate
    color_change_cost = max(color_count - 1, 0) * 200
    estimated = round(logo_area * density) + color_change_cost
    if estimated < MIN_STITCHES and logo_area > 0:
        estimated = MIN_STITCHES
    if total_area <= 0 or logo_area <= 0:
        estimated = 0

    production_estimate = round(estimated * 1.15) if estimated > 0 else 0

    log.info(f"  Stitch estimate: {estimated:,}  production: {production_estimate:,}")
    log.info(f"  Area: {total_area} sq\"  logo: {logo_area} sq\"  density: {density}")

    return {
        **dims,
        "placement":                  placement,
        "color_count":                color_count,
        "logo_percentage":            logo_pct,
        "total_area":                 total_area,
        "logo_area_estimate":         logo_area,
        "stitch_density_used":        density,
        "color_change_cost":          color_change_cost,
        "estimated_stitches":         estimated,
        "production_stitch_estimate": production_estimate,
    }


# ── STEP 3: ANALYZE IMAGE WITH GPT-4o-mini ───────────────────────────────────
def analyze_image(client: OpenAI, image_path: str, context: dict) -> dict:
    """Send image to GPT-4o-mini for embroidery suitability analysis."""
    log.info("  Analyzing image with GPT-4o-mini...")

    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    ext = Path(image_path).suffix.lower()
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png",  "webp": "image/webp",
            "gif": "image/gif"}.get(ext.lstrip("."), "image/png")

    system_prompt = """You are an expert embroidery digitizer with 20 years of production experience.
Analyze logo images for machine embroidery suitability with conservative, production-realistic judgment.
Return only valid JSON, no markdown, no prose."""

    user_prompt = f"""Analyze this logo for machine embroidery suitability.

Supporting context:
- Placement: {context.get('placement', 'unknown')}
- Source width: {context.get('width_in', 'unknown')}\"
- Source height: {context.get('height_in', 'unknown')}\"
- Aspect ratio: {context.get('aspect_ratio', 'unknown')}
- Estimated stitches at source size: {context.get('estimated_stitches', 'unknown')}
- Production stitch estimate: {context.get('production_stitch_estimate', 'unknown')}

Return this exact JSON structure with no other text:
{{
  "detected_text": "all visible text",
  "essential_text": "primary brand/name text only",
  "nonessential_text": "taglines, small supporting text",
  "icon_description": "description of any icon/graphic element",
  "layout_type": "horizontal|stacked|circular|badge|other",
  "visual_complexity": "low|medium|high",
  "stroke_thickness": "thin|medium|bold",
  "color_count_estimate": 3,
  "gradient_detected": false,
  "contains_text": true,
  "contains_icon": false,
  "normalized_placement": "left_chest",
  "source_width_in": {context.get('width_in', 0)},
  "source_height_in": {context.get('height_in', 0)},
  "aspect_ratio": {context.get('aspect_ratio', 1.0)},
  "recommended_width_in": 3.5,
  "recommended_height_in": 2.1,
  "sizing_reason": "why this size was chosen",
  "overall_suitability_score": 8,
  "overall_suitability_rating": "good",
  "small_text_risk": false,
  "thin_stroke_risk": false,
  "gradient_risk": false,
  "density_risk": false,
  "primary_risks": [],
  "too_small_text_found": false,
  "failing_text_segments": [],
  "recalculated_estimated_stitches": {context.get('estimated_stitches', 0)},
  "recalculated_production_stitch_estimate": {context.get('production_stitch_estimate', 0)},
  "recommended_changes": [],
  "overall_recommendation": "brief production recommendation"
}}"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{img_b64}",
                            "detail": "high"
                        }
                    },
                    {"type": "text", "text": user_prompt}
                ]
            }
        ],
        max_tokens=2000,
        temperature=0.1,
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        parsed = json.loads(raw)
        log.info(f"  Analysis: score={parsed.get('overall_suitability_score')}/10  "
                 f"complexity={parsed.get('visual_complexity')}  "
                 f"colors={parsed.get('color_count_estimate')}  "
                 f"gradient={parsed.get('gradient_detected')}")
        return {**context, **parsed}
    except json.JSONDecodeError as e:
        log.warning(f"  JSON parse error: {e} — using raw text")
        return {**context, "raw_analysis": raw}


# ── STEP 4a: PIL COLOR SIMPLIFICATION (for complex/gradient logos) ────────────
def simplify_with_pil(image_path: str, max_colors: int = 5) -> str | None:
    """
    Simplify a complex or gradient logo for embroidery digitizing using PIL:
    - Quantizes to max_colors flat colors, eliminating gradients
    - Uses no dithering so color boundaries are hard-edged (as embroidery requires)
    - Preserves the original alpha channel
    Returns path to simplified image, or None on failure.
    """
    log.info(f"  Simplifying logo with PIL color quantization ({max_colors} colors)...")
    try:
        img = Image.open(image_path).convert("RGBA")
        r, g, b, a = img.split()
        rgb = Image.merge("RGB", (r, g, b))

        # Median cut quantization, no dithering — hard flat fills, no blending
        quantized = rgb.quantize(colors=max_colors, method=Image.Quantize.MEDIANCUT,
                                 dither=Image.Dither.NONE)
        quantized_rgb = quantized.convert("RGB")

        # Reattach original alpha so transparency is preserved
        quantized_rgba = Image.merge("RGBA", (*quantized_rgb.split(), a))

        out_path = str(Path(image_path).parent / (Path(image_path).stem + "_simplified.png"))
        quantized_rgba.save(out_path, format="PNG")

        opaque = [px[:3] for px in quantized_rgba.getdata() if px[3] > 0]
        unique = len(set(opaque))
        log.info(f"  Simplified to {unique} colors → {out_path}")
        return out_path

    except Exception as e:
        log.warning(f"  PIL simplification failed: {e} — using original image")
        return None


# ── STEP 4b: GENERATE EMBROIDERY MOCKUP WITH GPT-IMAGE-1 ─────────────────────
def generate_mockup(client: OpenAI, image_path: str, analysis: dict) -> bytes | None:
    """Generate photorealistic embroidery mockup using GPT-Image-1."""
    log.info("  Generating embroidery mockup with GPT-Image-1...")

    rec_w   = analysis.get("recommended_width_in", analysis.get("width_in", 3.5))
    rec_h   = analysis.get("recommended_height_in", analysis.get("height_in", 2.0))
    aspect  = analysis.get("aspect_ratio", round(rec_w / max(rec_h, 0.01), 4))
    colors  = analysis.get("color_count_estimate", 3)
    ess     = analysis.get("essential_text", "")
    icon    = analysis.get("icon_description", "the icon/graphic element")

    prompt = f"""Render the uploaded logo as a macro photograph of a real commercial machine embroidery patch on a fully transparent background (PNG, alpha = 0 everywhere outside the thread).

TARGET OUTPUT
A professionally digitized left-chest embroidery patch. Think: Madeira 40-weight polyester thread, photographed on a lightbox with a single soft directional light, shallow depth of field, macro lens at 1:1 scale.

PHYSICAL EMBROIDERY SIZE
Stitch this at {rec_w}" × {rec_h}" — NOT the source art dimensions.
Aspect ratio: {aspect}

THREAD + STITCH BEHAVIOR
- 40-weight polyester thread, ~0.5mm strand diameter
- Satin columns: 2–15mm wide, individually visible parallel strands with cylindrical highlights and micro-shadows
- Fill (tatami): used only for large solid shapes, 0.4–0.5mm row spacing, row-by-row rhythm visible
- Run stitch: only where physically necessary
- Stitch direction follows form; direction changes between adjacent regions are visible
- Slight physical elevation above the base plane, subtle thread tension, realistic corner softening
- Every region is one flat color only — no shading, no gradients, no tonal variation within any shape

COLORS
Use {colors} thread colors maximum — fewer if possible. Merge similar hues aggressively. Hard edges between all color regions, no blending.

DIGITIZING LOGIC
Interpret this like an experienced digitizer optimizing for minimum thread usage:
- Preserve: {ess or 'the main text'}, {icon}, major shape boundaries
- Simplify: fine internal linework, small accents, micro-detail that won't survive at this size
- Remove: anything nonessential that requires disproportionate thread to stitch
- Use open negative space instead of background-colored fill wherever possible
- No automatic outer border unless it clearly exists in the source art

WHAT TO AVOID
No background, surface, fabric, shadow, glow, halo, or fringe outside the thread. No gradients or color transitions. No tonal shading within any stitched region. No printed/vector/painterly appearance.

FINAL CHECK
The result must look like real machine-sewn thread — tactile, physical, with individually visible satin strands, packed fill rows, and realistic stitch compression — not illustrated, painted, or printed."""

    with open(image_path, "rb") as f:
        response = client.images.edit(
            model="gpt-image-1",
            image=f,
            prompt=prompt,
            size="1024x1024",
            background="transparent",
            output_format="png",
            quality="high",
        )

    if response.data and response.data[0].b64_json:
        return base64.b64decode(response.data[0].b64_json)

    log.error("  No image data returned from GPT-Image-1")
    return None


# ── STEP 5: SAVE OUTPUT ───────────────────────────────────────────────────────
def save_output(img_bytes: bytes, stem: str, output_dir: str = "./mockups") -> str:
    """Save mockup PNG to disk."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    out_path = str(Path(output_dir) / f"{stem}-mockup.png")
    with open(out_path, "wb") as f:
        f.write(img_bytes)
    log.info(f"  Saved mockup: {out_path}")
    return out_path


# ── PRINT ANALYSIS REPORT ─────────────────────────────────────────────────────
def print_report(analysis: dict):
    """Print a human-readable analysis report."""
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  EMBROIDERY ANALYSIS REPORT")
    print(f"  {analysis.get('file_name', 'unknown')}")
    print(sep)
    print(f"  Source size:     {analysis.get('width_in',0)}\" x {analysis.get('height_in',0)}\"")
    print(f"  Recommended:     {analysis.get('recommended_width_in',0)}\" x {analysis.get('recommended_height_in',0)}\"")
    print(f"  Placement:       {analysis.get('placement','')}")
    print(f"  Stitches:        {analysis.get('recalculated_estimated_stitches', analysis.get('estimated_stitches',0)):,}")
    print(f"  Production est:  {analysis.get('recalculated_production_stitch_estimate', analysis.get('production_stitch_estimate',0)):,}")
    print(f"  Colors:          {analysis.get('color_count_estimate','unknown')}")
    print(f"  Layout:          {analysis.get('layout_type','unknown')}")
    print(f"  Complexity:      {analysis.get('visual_complexity','unknown')}")
    print(f"  Gradient:        {analysis.get('gradient_detected', False)}")
    print()

    score = analysis.get('overall_suitability_score', 0)
    rating = analysis.get('overall_suitability_rating', '')
    indicator = "✅" if score >= 7 else "⚠️" if score >= 5 else "❌"
    print(f"  SUITABILITY:     {indicator}  {score}/10  ({rating})")
    print()

    risks = []
    if analysis.get("small_text_risk"):    risks.append("⚠️  Small text risk")
    if analysis.get("thin_stroke_risk"):   risks.append("⚠️  Thin stroke risk")
    if analysis.get("gradient_risk"):      risks.append("⚠️  Gradient detected")
    if analysis.get("density_risk"):       risks.append("⚠️  High density")
    if analysis.get("color_complexity_risk"): risks.append("⚠️  Too many colors")
    if analysis.get("fine_detail_risk"):   risks.append("⚠️  Fine detail may be lost")
    if risks:
        print("  RISKS:")
        for r in risks:
            print(f"    {r}")
        print()

    failing = analysis.get("failing_text_segments", [])
    if failing:
        print("  SMALL TEXT WARNINGS:")
        for seg in failing:
            if isinstance(seg, dict):
                print(f"    '{seg.get('exact_text','')}' → {seg.get('recommended_action','')}")
        print()

    recommendation = analysis.get("overall_recommendation", "")
    if recommendation:
        print(f"  RECOMMENDATION:")
        print(f"    {recommendation}")
        print()

    changes = analysis.get("recommended_changes", [])
    if changes:
        print("  SUGGESTED CHANGES:")
        for c in (changes if isinstance(changes, list) else [changes]):
            print(f"    • {c}")

    print(sep)


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="AI Embroidery Mockup Generator — standalone Python")
    parser.add_argument("--image",      required=True, help="Path to logo image")
    parser.add_argument("--placement",  default="left chest",
                        help="Embroidery placement (e.g. 'left chest', 'hat front')")
    parser.add_argument("--output",     default=None,  help="Output mockup path")
    parser.add_argument("--output_dir", default="./mockups",
                        help="Output directory for mockups")
    parser.add_argument("--no-simplify", action="store_true",
                        help="Skip PIL color simplification even for complex/gradient logos")
    parser.add_argument("--analyze-only", action="store_true",
                        help="Run analysis only, skip mockup generation")
    parser.add_argument("--save-analysis", default=None,
                        help="Save analysis JSON to this path")
    args = parser.parse_args()

    # Validate inputs
    if not os.path.exists(args.image):
        print(f"ERROR: Image not found: {args.image}")
        sys.exit(1)

    openai_key = os.environ.get("OPENAI_API_KEY")
    if not openai_key:
        print("ERROR: OPENAI_API_KEY not set")
        sys.exit(1)

    client = OpenAI(api_key=openai_key)

    print(f"\nProcessing: {args.image}")
    print(f"Placement:  {args.placement}")

    # Step 1: Dimensions
    log.info("Step 1: Resolving dimensions...")
    dims = resolve_dimensions(args.image)

    # Step 2: Stitch estimate
    log.info("Step 2: Estimating stitches...")
    context = estimate_stitches(dims, args.placement)

    # Step 3: GPT-4o-mini analysis
    log.info("Step 3: Analyzing with GPT-4o-mini...")
    analysis = analyze_image(client, args.image, context)

    # Print report
    print_report(analysis)

    # Save analysis JSON if requested
    if args.save_analysis:
        with open(args.save_analysis, "w") as f:
            # Remove binary data before saving
            clean = {k: v for k, v in analysis.items()
                     if not isinstance(v, (bytes, bytearray))}
            json.dump(clean, f, indent=2)
        log.info(f"Analysis saved: {args.save_analysis}")

    if args.analyze_only:
        return

    # Step 4: Determine which image to use for mockup generation
    mockup_source = args.image

    is_complex   = analysis.get("visual_complexity") == "high"
    has_gradient = analysis.get("gradient_detected", False)

    if (is_complex or has_gradient) and not args.no_simplify:
        log.info("Step 4a: Complex/gradient logo — running PIL color simplification...")
        simplified = simplify_with_pil(args.image)
        if simplified:
            mockup_source = simplified
    else:
        log.info("Step 4: Simple logo — skipping color simplification")

    # Step 5: Generate mockup
    log.info("Step 5: Generating mockup with GPT-Image-1...")
    img_bytes = generate_mockup(client, mockup_source, analysis)

    if not img_bytes:
        print("ERROR: Mockup generation failed")
        sys.exit(1)

    # Step 6: Save
    stem = Path(args.image).stem
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "wb") as f:
            f.write(img_bytes)
        out_path = args.output
        log.info(f"Mockup saved: {out_path}")
    else:
        out_path = save_output(img_bytes, stem, args.output_dir)

    print(f"\n✅  Mockup saved: {out_path}")
    print(f"   Size: {len(img_bytes)/1024:.1f} KB")


if __name__ == "__main__":
    main()
