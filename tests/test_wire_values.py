"""Wire is user-defined config, not schema. Any slug-shaped string is a valid wire."""

from src.models.models import ChannelConfig
from src.api.channels import is_valid_wire


def test_known_and_custom_wires_are_valid():
    for w in ("crypto", "ai", "oss", "repos", "models", "claw", "my-niche-1"):
        assert is_valid_wire(w) is True


def test_garbage_wires_are_rejected():
    for w in ("", "  ", "UPPER", "has space", "x" * 33, "semi;colon"):
        assert is_valid_wire(w) is False


def test_channel_config_wire_is_plain_string_column():
    assert ChannelConfig.wire.type.python_type is str
