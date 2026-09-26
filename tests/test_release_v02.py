"""Метаданные и CLI называют одну версию новой поставки."""
import subprocess
import sys
import tomllib
from pathlib import Path

from sozvon import __version__


def test_release_version_matches_metadata_and_cli():
    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
    assert __version__ == data['project']['version'] == '0.2.0'
    result = subprocess.run([sys.executable, '-m', 'sozvon', '--version'],
                            capture_output=True, timeout=10, check=False)
    assert result.returncode == 0
    assert b'0.2.0' in result.stdout
