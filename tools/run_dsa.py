#!/usr/bin/env python3
"""CLI-only bridge to an unmodified Daily Stock Analysis checkout (Python 3.10+).

First run tools/setup_dsa.ps1 explicitly, then edit upstream .env locally with
YOUR model key/model/base URL and STOCK_LIST (or pass --stocks). Never send keys
as command-line arguments. This bridge neither opens nor prints .env; upstream
loads it normally, including ENV_FILE if explicitly set by the caller.

Audited upstream c820915: setup_env(override=False) preserves process env, and
Config._capture_bootstrap_runtime_env_overrides preserves differing bootstrap
SCHEDULE_ENABLED/RUN_IMMEDIATELY values even for file-priority runtime keys.
main._reload_env_file_values_preserving_overrides also preserves initial env.
Re-audit these semantics if --repo points at a different upstream revision.

Reports are upstream UTF-8 Markdown in <repo>/reports, not the host's reports
folder and not JSON. Point the host's dsa_report_dir there. Same-day report names
are overwritten; the bridge checks new/changed nonempty .md files, not analysis
completeness or financial accuracy. Upstream can skip closed markets. It may
write data/logs, make paid model/data requests, and partial reports may remain
on failure/timeout. --no-notify disables normal notifications. Because upstream's
Feishu cloud-document creation has a separate guard, this bridge also blanks its
three credential/config keys in the child environment and disables bot streams.
This does not prevent intended model/data/search API network requests.

--manifest-out is opt-in metadata-only JSON: repo, reports (absolute path,
mtime_ns, size for this run's changed nonempty Markdown files), and UTC
completed_at. Use a dedicated unique .json path: any prior output there is
removed before launch. Failed runs do not publish a success manifest. Consumers
must still check exit 0, validate report paths, and recheck mtime_ns/size before
reading: this does not lock upstream files against concurrent runs.

Output is inherited by the invoking terminal, never captured/returned to Web.
Use only from a trusted CLI: upstream logs themselves may contain sensitive
information. Do NOT invoke this through a Web log-capturing task runner.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def default_repo() -> Path:
    candidates = sorted(
        path for path in (ROOT / "vendor").glob("daily_stock_analysis*")
        if (path / "main.py").is_file() and (path / "requirements.txt").is_file()
    )
    if len(candidates) != 1:
        raise ValueError("Specify --repo: expected exactly one DSA checkout in vendor.")
    return candidates[0]


def report_snapshot(directory: Path) -> dict[Path, tuple[int, int]]:
    snapshot = {}
    if directory.is_dir():
        for path in directory.glob("*.md"):
            if path.is_file() and not path.is_symlink():
                info = path.stat()
                snapshot[path] = (info.st_mtime_ns, info.st_size)
    return snapshot


def write_manifest(output: Path, repo: Path, changed: list[Path], after: dict[Path, tuple[int, int]]) -> None:
    """Publish metadata only, atomically; never read report bodies or credentials."""
    payload = {
        "repo": str(repo),
        "reports": [
            {"path": str(path.absolute()), "mtime_ns": after[path][0], "size": after[path][1]}
            for path in sorted(changed)
        ],
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=output.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, help="Upstream checkout; default: unique vendor/daily_stock_analysis*")
    parser.add_argument("--python", dest="python_executable", help="Python executable (not a command string); default: vendor/.venv-dsa")
    parser.add_argument("--stocks", help="Comma-separated upstream stock codes; omitted: upstream STOCK_LIST")
    parser.add_argument("--timeout", type=float, default=600, help="Wall-clock timeout in seconds (default: 600)")
    parser.add_argument("--manifest-out", type=Path, help="Optional dedicated .json output path; stale output is removed before this run")
    args = parser.parse_args(argv)
    manifest = None
    succeeded = False
    try:
        if args.manifest_out is not None:
            output = args.manifest_out.expanduser().absolute()
            if output.suffix.lower() != ".json" or output.is_symlink():
                raise ValueError("--manifest-out must be a dedicated non-symlink .json path")
            # Resolve the parent, not the final component; never follow an output symlink.
            manifest = output.parent.resolve() / output.name
            manifest.unlink(missing_ok=True)
            manifest.parent.mkdir(parents=True, exist_ok=True)
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            parser.error("--timeout must be finite and greater than zero")
        if args.stocks is not None and (not args.stocks.strip() or args.stocks.lstrip().startswith("-")):
            parser.error("--stocks must contain stock codes, not options")
        repo = (args.repo if args.repo is not None else default_repo()).expanduser().resolve()
        if not (repo / "main.py").is_file() or not (repo / "requirements.txt").is_file():
            raise ValueError("--repo must contain real upstream main.py and requirements.txt")
        executable = args.python_executable or str(ROOT / "vendor" / ".venv-dsa" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        # Resolve before changing cwd so a relative interpreter path stays relative to the caller.
        interpreter = shutil.which(executable)
        if interpreter is None:
            raise ValueError("Python interpreter not found; run setup_dsa.ps1 or pass --python")
        # Keep venv symlinks intact: resolving bin/python can select the base interpreter.
        interpreter = str(Path(interpreter).absolute())
        reports = repo / "reports"
        before = report_snapshot(reports)
        env = os.environ.copy()
        env.update(
            SCHEDULE_ENABLED="false", WEBUI_ENABLED="false", RUN_IMMEDIATELY="true",
            FEISHU_APP_ID="", FEISHU_APP_SECRET="", FEISHU_FOLDER_TOKEN="",
            BOT_ENABLED="false", FEISHU_STREAM_ENABLED="false", DINGTALK_STREAM_ENABLED="false",
        )
        command = [interpreter, str(repo / "main.py"), "--no-notify"]
        if args.stocks is not None:
            command.extend(["--stocks", args.stocks])
        print("Starting real DSA in CLI-only mode; upstream output is inherited, not captured.", flush=True)
        result = subprocess.run(command, shell=False, cwd=repo, env=env, timeout=args.timeout, check=False)
        if result.returncode != 0:
            print(f"DSA failed (upstream exit code {result.returncode}); reports may be partial/stale.", file=sys.stderr)
            return result.returncode if 0 < result.returncode < 256 else 1
        after = report_snapshot(reports)
        changed = [path for path, info in after.items() if info[1] > 0 and before.get(path) != info]
        if not changed:
            print("DSA exited 0 but produced no new/updated nonempty Markdown report (possibly a closed-market skip).", file=sys.stderr)
            return 3
        if manifest is not None:
            write_manifest(manifest, repo, changed, after)
        print(f"DSA completed; {len(changed)} new/updated Markdown report(s). dsa_report_dir={reports}")
        succeeded = True
        return 0
    except subprocess.TimeoutExpired:
        # subprocess.run kills and waits for the direct child on timeout; no log payload is retained.
        print("DSA timed out; direct child terminated. Partial reports/logs may remain; check for upstream descendant processes.", file=sys.stderr)
        return 124
    except KeyboardInterrupt:
        print("DSA interrupted.", file=sys.stderr)
        return 130
    except (OSError, ValueError) as exc:
        print(f"DSA bridge failed: {exc}", file=sys.stderr)
        return 2
    finally:
        if manifest is not None and not succeeded:
            try:
                manifest.unlink(missing_ok=True)
            except OSError:
                # Never claim success when cleanup is blocked; caller must check exit status.
                print("Could not remove unsuccessful manifest output; do not consume it.", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
