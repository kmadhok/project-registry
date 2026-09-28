import datetime as dt

from project_registry.dashboard import render_dashboard
from project_registry.storage import Paths, Registry


def test_dashboard_builder_health(tmp_path):
    now = dt.datetime(2026, 9, 28, 13, tzinfo=dt.timezone.utc)
    rendered = render_dashboard(Registry(paths=Paths(tmp_path)), now=now)
    assert "## Builder health" in rendered
    assert "- **NEVER** — no runs in the journal" in rendered
    assert "- Last run: _No runs in the journal._" in rendered
    assert "- Next expected: 2026-09-29 12:00 UTC" in rendered
    assert "- Brakes: none" in rendered
    assert rendered.index("## Builder health") < rendered.index("## Build (")


def test_dashboard_without_paths_omits_builder_health():
    rendered = render_dashboard(Registry(), now=dt.datetime(2026, 9, 28, tzinfo=dt.timezone.utc))
    assert "## Builder health" not in rendered


def test_dashboard_shows_last_run_and_brakes(tmp_path):
    import json

    paths = Paths(tmp_path)
    paths.build_dir.mkdir(parents=True)
    paths.build_runs_file.write_text(json.dumps({
        "ts": "2026-09-28T12:00:00Z", "run_id": "r1", "host": "vps",
        "type": "run_started", "project_id": "builder", "chunk_id": None,
        "pr_url": None, "tag": None, "reason": None, "detail": None,
    }) + "\n")
    paths.build_stop_file.touch()
    paths.build_lease_file.write_text(json.dumps({"run_id": "r1", "project_id": "builder"}))
    rendered = render_dashboard(
        Registry(paths=paths), now=dt.datetime(2026, 9, 28, 13, tzinfo=dt.timezone.utc),
    )
    assert "- **OK** — ran today · builder · crashed? · 0 merged" in rendered
    assert "- Last run: `r1` · builder · crashed? · 0 merged · 2026-09-28 12:00 UTC" in rendered
    assert "- Brakes: STOP present / lease held by r1" in rendered
