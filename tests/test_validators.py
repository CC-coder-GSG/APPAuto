import pytest

from app.core.exceptions import ValidationFailed
from app.utils.validators import parse_prefixed_ids, require_prefixed_id, validate_bug_id, validate_case_id, validate_req_id


def test_parse_prefixed_ids_placeholder():
    assert parse_prefixed_ids("b#1,b#2") == ["b#1", "b#2"]


def test_validate_prefixed_ids():
    assert validate_req_id("r#1001") is True
    assert validate_case_id("u#2002") is True
    assert validate_bug_id("b#3003") is True


def test_require_prefixed_id_raises_for_invalid_value():
    with pytest.raises(ValidationFailed):
        require_prefixed_id("x#123", "bug")
