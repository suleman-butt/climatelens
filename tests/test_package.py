import re
from importlib.metadata import version

from climatelens import __version__


def test_version_is_semantic() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_package_metadata_uses_the_same_version() -> None:
    assert version("climatelens") == __version__
