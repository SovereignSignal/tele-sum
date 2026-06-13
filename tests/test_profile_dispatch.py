from src.services.processor import resolve_profile


class FakeChannelConfig:
    def __init__(self, extraction_profile=None, prompt_hint=None):
        self.extraction_profile = extraction_profile
        self.prompt_hint = prompt_hint


def test_default_profile_is_grant():
    assert resolve_profile(None) == ("grant", None)
    assert resolve_profile(FakeChannelConfig()) == ("grant", None)
    assert resolve_profile(FakeChannelConfig(extraction_profile="")) == ("grant", None)


def test_digest_profile_carries_hint():
    cc = FakeChannelConfig(extraction_profile="digest", prompt_hint="repo digests")
    assert resolve_profile(cc) == ("digest", "repo digests")


def test_unknown_profile_falls_back_to_grant():
    cc = FakeChannelConfig(extraction_profile="surprise")
    assert resolve_profile(cc) == ("grant", None)
