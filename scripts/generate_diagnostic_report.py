#!/usr/bin/env python3
"""CLI utility to generate a comprehensive diagnostic report for an MVentX USB or virtual mount.

Usage:
    python scripts/generate_diagnostic_report.py --data E:\ --boot F:\
    python scripts/generate_diagnostic_report.py --data build/data --boot build/esp --format json
"""

import argparse
from pathlib import Path
import sys

# Ensure repository root in path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.diagnostics.diagnostic_report import DiagnosticReportGenerator


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate MVentX Diagnostic & Troubleshooting Report.")
    parser.add_argument("--data", "-d", required=True, help="Path to MVX_DATA mount or directory")
    parser.add_argument("--boot", "-b", default=None, help="Path to MVX_BOOT mount or directory (optional)")
    parser.add_argument("--output", "-o", default=None, help="Output file path (default: print to stdout)")
    parser.add_argument("--format", "-f", choices=["markdown", "json"], default="markdown", help="Output format")

    args = parser.parse_args()

    data_p = Path(args.data)
    boot_p = Path(args.boot) if args.boot else None

    if not data_p.is_dir():
        print(f"Error: MVX_DATA path '{data_p}' does not exist or is not a directory.", file=sys.stderr)
        return 1

    generator = DiagnosticReportGenerator(data_mount=data_p, boot_mount=boot_p)
    report = generator.generate_report()

    if args.format == "json":
        output_text = report.to_json(indent=2)
    else:
        output_text = report.to_markdown()

    if args.output:
        out_p = Path(args.output)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(output_text, encoding="utf-8")
        print(f"Diagnostic report exported to {out_p}")
    else:
        print(output_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())

