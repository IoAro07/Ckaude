"""The version is stated in one clear way everywhere: pyproject, the package, the README and the changelog agree."""

import re
from pathlib import Path

import dwg2c4d

ROOT = Path(__file__).resolve().parent.parent


def test_every_place_that_states_the_version_agrees():
    pyproject = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    readme = re.search(r"\*\*Versione ([0-9.]+)\*\*", (ROOT / "README.md").read_text(encoding="utf-8")).group(1)
    changelog = re.search(r"^## \[([0-9.]+)\]", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M).group(1)
    assert dwg2c4d.__version__ == pyproject == readme == changelog


def test_the_changelog_lists_versions_from_newest_to_oldest():
    versions = re.findall(r"^## \[([0-9.]+)\]", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
    assert versions == sorted(versions, key=lambda v: tuple(map(int, v.split("."))), reverse=True)
    assert len(versions) == len(set(versions)) and versions[-1] == "0.1.0"
