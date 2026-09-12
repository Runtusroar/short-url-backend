import subprocess
import sys
from pathlib import Path


def test_backfill_script_can_run_as_a_direct_cli_entrypoint():
    """The Makefile's direct script invocation must reach argument parsing."""
    project_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [sys.executable, "scripts/backfill_user_agents.py", "--help"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--batch-size" in completed.stdout
