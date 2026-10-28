"""Clean IkanSight snapshots, build price bands and publish them to Supabase (thin CLI wrapper).

Usage:
    D:\\hargaikan-venv\\Scripts\\python.exe scripts/ingest.py --raw data/raw --target development
    D:\\hargaikan-venv\\Scripts\\python.exe scripts/ingest.py --synthetic --dry-run --target development

All logic lives in :mod:`hargaikan.pipeline.ingest` (shared with notebooks/01_ingest.ipynb).
--target must equal APP_ENV. --synthetic is refused for production. Uses the Supabase service
role key (scripts and notebooks only); secret values are never printed.

Exit status: 0 = success, 1 = pipeline or publish failure, 2 = usage/configuration error.
"""

from __future__ import annotations

import sys
from pathlib import Path

from hargaikan.pipeline.ingest import load_dotenv_if_present, main

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    load_dotenv_if_present(ROOT / ".env")
    sys.exit(main(sys.argv[1:]))
