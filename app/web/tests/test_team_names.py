import pytest
from pydantic import ValidationError

from app.web.api.models import TeamCreate, TeamUpdate


@pytest.mark.parametrize("payload", [TeamCreate, TeamUpdate])
def test_team_name_accepts_word_separators_and_preserves_existing_identifiers(payload):
    for name in ["Research and Development", "team_1-A", "a" * 64]:
        assert payload(name=name).name == name
    for name in ["", " space", "space ", "two  spaces", "a\tb", "R&D", "a" * 65]:
        with pytest.raises(ValidationError):
            payload(name=name)
