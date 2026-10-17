"""Select expensive CI checks; unknown diffs conservatively run all checks."""
import os
from pathlib import Path
import subprocess


def code_changed(base, repository=None):
    if not base or set(base) == {'0'}:
        return True
    # A code file renamed to Markdown still removes code. Disable rename
    # detection so its old path is included along with the destination.
    result = subprocess.run(['git', 'diff', '--no-renames', '--name-only', '-z', base, 'HEAD'],
                            cwd=repository, capture_output=True)
    if result.returncode:
        return True
    return any(not path.lower().endswith(b'.md') for path in result.stdout.split(b'\0') if path)


def main():
    value = str(code_changed(os.environ.get('CI_DIFF_BASE'))).lower()
    target = os.environ.get('GITHUB_OUTPUT')
    if target:
        with Path(target).open('a', encoding='utf-8') as stream:
            stream.write(f'code={value}\n')
    print(f'code={value}')


if __name__ == '__main__':
    main()
