"""
eval.py — Stitch estimator benchmark.

Runs estimate_stitches against each job in benchmark_jobs.json,
compares to known actual stitch counts, and reports accuracy metrics.

Usage:
    python eval/eval.py
    python eval/eval.py --jobs eval/benchmark_jobs.json
"""

import sys
import json
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))
from embroidery_mockup import estimate_stitches

ASSUMED_DPI = 72


def load_jobs(path: str) -> list[dict]:
    with open(path) as f:
        data = json.load(f)
    return data["jobs"]


def build_dims(job: dict) -> dict:
    w, h = job["width_in"], job["height_in"]
    return {
        "width_px":     int(w * ASSUMED_DPI),
        "height_px":    int(h * ASSUMED_DPI),
        "width_in":     w,
        "height_in":    h,
        "aspect_ratio": round(w / max(h, 0.001), 4),
        "image_format": "png",
        "assumed_dpi":  ASSUMED_DPI,
        "file_name":    job["id"] + ".png",
        "file_stem":    job["id"],
    }


def run_benchmark(jobs: list[dict]) -> list[dict]:
    results = []
    for job in jobs:
        dims = build_dims(job)
        estimate = estimate_stitches(dims, job["placement"], job.get("color_count", 3))
        actual = job["actual_stitches"]
        est = estimate["estimated_stitches"]
        error = abs(est - actual)
        pct_error = error / actual * 100

        results.append({
            **job,
            "estimated_stitches": est,
            "error": error,
            "signed_error": est - actual,
            "pct_error": round(pct_error, 1),
        })
    return results


def group_stats(results: list[dict], key: str) -> dict:
    groups = defaultdict(list)
    for r in results:
        groups[r[key]].append(r)
    stats = {}
    for group, items in sorted(groups.items()):
        errors = [r["error"] for r in items]
        pcts = [r["pct_error"] for r in items]
        stats[group] = {
            "count": len(items),
            "mae": round(sum(errors) / len(errors)),
            "mape": round(sum(pcts) / len(pcts), 1),
        }
    return stats


def print_report(results: list[dict]):
    sep = "=" * 72
    n = len(results)
    errors = [r["error"] for r in results]
    pcts = [r["pct_error"] for r in results]
    mae = round(sum(errors) / n)
    mape = round(sum(pcts) / n, 1)

    # Direction of error
    over = sum(1 for r in results if r["signed_error"] > 0)
    under = sum(1 for r in results if r["signed_error"] < 0)

    print(f"\n{sep}")
    print(f"  STITCH ESTIMATOR BENCHMARK  ({n} jobs)")
    print(sep)
    print(f"  MAE:           {mae:,} stitches")
    print(f"  MAPE:          {mape}%")
    print(f"  Over-estimates: {over} jobs  |  Under-estimates: {under} jobs")
    print()

    print("  BY PLACEMENT")
    by_placement = group_stats(results, "placement")
    for placement, s in by_placement.items():
        print(f"    {placement:<14s}  {s['count']} jobs   MAE: {s['mae']:>6,}   MAPE: {s['mape']:>5.1f}%")

    print()
    print("  BY COMPLEXITY")
    by_complexity = group_stats(results, "complexity")
    for complexity, s in by_complexity.items():
        print(f"    {complexity:<8s}  {s['count']} jobs   MAE: {s['mae']:>6,}   MAPE: {s['mape']:>5.1f}%")

    print()
    print("  JOB-LEVEL DETAIL")
    print(f"  {'ID':<10s}  {'Placement':<14s}  {'Complexity':<8s}  {'Actual':>8s}  {'Est':>8s}  {'Error':>7s}  {'Err%':>6s}")
    print(f"  {'-'*10}  {'-'*14}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*7}  {'-'*6}")
    for r in results:
        direction = "▲" if r["signed_error"] > 0 else "▼"
        print(f"  {r['id']:<10s}  {r['placement']:<14s}  {r['complexity']:<8s}  "
              f"{r['actual_stitches']:>8,}  {r['estimated_stitches']:>8,}  "
              f"{direction}{r['error']:>6,}  {r['pct_error']:>5.1f}%")

    print(sep)

    # Key findings
    worst = max(results, key=lambda r: r["pct_error"])
    best = min(results, key=lambda r: r["pct_error"])
    print("\n  KEY FINDINGS")
    print(f"  Best estimate:  {best['id']} ({best['placement']}, {best['complexity']}) — {best['pct_error']}% error")
    print(f"  Worst estimate: {worst['id']} ({worst['placement']}, {worst['complexity']}) — {worst['pct_error']}% error")

    floor_jobs = [r for r in results if r["estimated_stitches"] == 4500]
    if floor_jobs:
        floor_errors = [r["pct_error"] for r in floor_jobs]
        floor_mape = round(sum(floor_errors) / len(floor_errors), 1)
        non_floor_errors = [r["pct_error"] for r in results if r["estimated_stitches"] != 4500]
        non_floor_mape = round(sum(non_floor_errors) / len(non_floor_errors), 1)
        print(f"\n  Floor-capped jobs ({len(floor_jobs)}):   MAPE {floor_mape}%  "
              f"(MIN_STITCHES floor inflates estimates for small designs)")
        print(f"  Non-floor jobs   ({len(results) - len(floor_jobs)}):   MAPE {non_floor_mape}%")

    print()


def main():
    parser = argparse.ArgumentParser(description="Stitch estimator benchmark")
    parser.add_argument("--jobs", default=str(Path(__file__).parent / "benchmark_jobs.json"),
                        help="Path to benchmark_jobs.json")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    jobs = load_jobs(args.jobs)
    results = run_benchmark(jobs)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_report(results)


if __name__ == "__main__":
    # Suppress the logging noise from estimate_stitches
    import logging
    logging.getLogger().setLevel(logging.WARNING)
    main()
