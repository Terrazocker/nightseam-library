import subprocess

from scripts.select_ci_checks import code_changed


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True,
                          text=True).stdout.strip()


def test_ci_runs_for_code_removal_even_when_renamed_to_markdown(tmp_path):
    git(tmp_path, 'init')
    git(tmp_path, 'config', 'user.name', 'CI selection test')
    git(tmp_path, 'config', 'user.email', 'ci@example.invalid')
    (tmp_path / 'module.py').write_text('print(1)\n', encoding='utf-8')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-m', 'base')
    base = git(tmp_path, 'rev-parse', 'HEAD')
    (tmp_path / 'notes.md').write_text('Documentation\n', encoding='utf-8')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-m', 'docs')
    assert not code_changed(base, tmp_path)
    (tmp_path / 'module.py').rename(tmp_path / 'module.md')
    git(tmp_path, 'add', '-A')
    git(tmp_path, 'commit', '-m', 'remove code')
    assert code_changed(base, tmp_path)
    assert code_changed('not-a-commit', tmp_path)
    assert code_changed('', tmp_path)
    assert code_changed('0' * 40, tmp_path)
