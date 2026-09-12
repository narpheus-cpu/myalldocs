from types import SimpleNamespace

import pytest

from indexer.model_selector import NoSupportedModel, rank_models, select_model


POLICY = {
    "allowNamePatterns": [r"^gemini-"],
    "preferencePatterns": [r"flash$", r"pro$"],
    "denyNameFragments": ["preview", "experimental", "latest", "deprecated", "retired"],
}


def model(name, actions=("generateContent",), description=""):
    return SimpleNamespace(name="models/" + name, display_name=name, supported_actions=actions, description=description)


def test_runtime_selector_rejects_aliases_and_prefers_flash():
    selected = select_model([model("gemini-9-pro"), model("gemini-9-flash-preview"), model("gemini-9-flash")], POLICY)
    assert selected.name == "gemini-9-flash"


def test_runtime_ranking_prefers_newer_stable_version_then_falls_back():
    ranked = rank_models([model("gemini-8-flash"), model("gemini-9-flash"), model("gemini-9-pro")], POLICY)
    assert [item.name for item in ranked] == ["gemini-9-flash", "gemini-8-flash", "gemini-9-pro"]


def test_runtime_selector_requires_generate_content():
    with pytest.raises(NoSupportedModel):
        select_model([model("gemini-9-flash", ("embedContent",))], POLICY)


def test_runtime_selector_rejects_deprecated_description():
    with pytest.raises(NoSupportedModel):
        select_model([model("gemini-9-flash", description="deprecated model")], POLICY)
