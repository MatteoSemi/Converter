from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from generate_fixtures import generate


@pytest.fixture(scope="session")
def fixtures(tmp_path_factory):
    return generate(tmp_path_factory.mktemp("png-fixtures"))
