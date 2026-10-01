from pathlib import Path
import subprocess


def test_tonight_chart_strokes_each_blocked_run():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", str(root / "tests" / "js" / "tonight_chart.mjs")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
