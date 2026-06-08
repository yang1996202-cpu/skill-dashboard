"""Background diagnosis worker — spawned by serve.py via subprocess.

Usage: python3 _diag_worker.py <target_path>

Runs python_quick_check + upstream status checks, writes result to cache.
"""
import json
import sys
import time
from pathlib import Path

# Add parent dir to path so we can import from serve.py
sys.path.insert(0, str(Path(__file__).parent))

from serve import python_quick_check, check_upstream_status, save_cached_diagnosis


def main():
    if len(sys.argv) < 2:
        print("error: missing target_path argument", file=sys.stderr)
        sys.exit(1)

    target = sys.argv[1]

    if not Path(target).is_dir():
        print(f"error: not a directory: {target}", file=sys.stderr)
        sys.exit(1)

    result = python_quick_check(target)
    if not result:
        print("error: python_quick_check returned None", file=sys.stderr)
        sys.exit(1)

    # Check upstream status for each skill with metadata
    for u in result.get("upstream_sources", []):
        skill_dir = Path(target) / u["name"]
        # Validate skill name to prevent path traversal
        if u["name"].startswith(".") or "/" in u["name"] or "\\" in u["name"]:
            continue
        status = check_upstream_status(skill_dir)
        if status.get("status") in ("current", "outdated"):
            u["status"] = status["status"]
            u["installed_commit"] = status.get("installed_commit", "")
            u["latest_commit"] = status.get("latest_commit", "")
            u["ahead_by"] = status.get("ahead_by")

    result["source"] = "python-diagnosis"
    result["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_cached_diagnosis(target, result)
    print("done")


if __name__ == "__main__":
    main()
