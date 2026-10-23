import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def list_body():
    return json.loads((FIXTURES / "list_page.json").read_text())


@pytest.fixture
def detail_body():
    return json.loads((FIXTURES / "detail.json").read_text())


def signed_link(name: str) -> str:
    """A synthetic link shaped like a signed download link."""
    return (f"https://files.example.com/replay/2024/06/01/abc/{name}"
            "?response-content-disposition=attachment&Policy=POLICYPOLICYPOLICYPOLICYPOLICYPOLICYPOLICYPOLICY"
            "&Signature=SIGNATURESIGNATURESIGNATURESIGNATURESIGNATURESIGNATURE&Key-Pair-Id=KTEST")
