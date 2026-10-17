"""Platform selection must preserve verified bundles and reject unsupported hosts."""
from types import SimpleNamespace

import pytest

from app import compression


@pytest.mark.parametrize('system,machine,expected', [
    ('Windows', 'AMD64', ('windows-x64', '.exe')),
    ('Linux', 'x86_64', ('linux-x64', '')),
    ('Linux', 'aarch64', None),
    ('Darwin', 'x86_64', None),
])
def test_tool_bundle_platform_selection(monkeypatch, system, machine, expected):
    monkeypatch.setattr(compression, 'platform', SimpleNamespace(
        system=lambda: system, machine=lambda: machine))
    if expected is None:
        with pytest.raises(ValueError, match='not available'):
            compression.tool_bundle()
    else:
        assert compression.tool_bundle() == expected


def test_missing_bundle_has_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(compression, 'resource_dir', lambda: tmp_path)
    with pytest.raises(ValueError, match='build_fits_tools.py'):
        compression.tools()
