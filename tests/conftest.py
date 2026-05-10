"""
Pytest configuration and shared fixtures for MediaFusion tests.
"""

import importlib.util as _ilu
import sys
from datetime import date
from pathlib import Path

import pytest

# Map the deprecated Python API directory as the `reference` package so that
# existing tests that import `from reference.routers.*` continue to work after
# the root-level `reference` symlink was removed.
_deprecated_api_path = Path(__file__).parent.parent / "services" / "api-py-deprecated"
_spec = _ilu.spec_from_file_location(
    "reference",
    _deprecated_api_path / "__init__.py",
    submodule_search_locations=[str(_deprecated_api_path)],
)
_mod = _ilu.module_from_spec(_spec)
sys.modules.setdefault("reference", _mod)


@pytest.fixture
def sample_nfl_title():
    """Sample NFL torrent title."""
    return "NFL.2026.02.08.Super.Bowl.LX.Seattle.Seahawks.Vs.New.England.Patriots.1080p.HDTV.H264-DARKSPORT"


@pytest.fixture
def sample_f1_title():
    """Sample Formula 1 torrent title."""
    return "Formula1.2024.Round07.British.Grand.Prix.Race.F1TV.1080p.WEB-DL.AAC2.0.H.264-F1CARRERAS"


@pytest.fixture
def sample_ufc_title():
    """Sample UFC torrent title."""
    return "UFC.300.Alex.Pereira.vs.Jamahal.Hill.PPV.1080p.WEB.h264-VERUM"


@pytest.fixture
def sample_wwe_title():
    """Sample WWE torrent title."""
    return "WWE.Monday.Night.Raw.2024.02.19.720p.HDTV.x264-NWCHD"


@pytest.fixture
def sample_motogp_title():
    """Sample MotoGP torrent title."""
    return "MotoGP.2024x03.San.Marino.Sprint.BTSportHD.1080p"


@pytest.fixture
def sample_nba_title():
    """Sample NBA torrent title."""
    return "NBA.2024.02.15.Los.Angeles.Lakers.vs.Golden.State.Warriors.720p.WEB.h264"


@pytest.fixture
def sample_date():
    """Sample date for testing fallback date functionality."""
    return date(2026, 2, 10)
