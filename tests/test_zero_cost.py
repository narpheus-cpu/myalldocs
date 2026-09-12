from pathlib import Path
from types import SimpleNamespace

import pytest

from indexer.config import Settings
from indexer.drive_quota import DriveQuotaGuard, DriveQuotaPaused
from indexer.model_selector import NoSupportedModel, rank_models


ROOT = Path(__file__).resolve().parents[1]


def test_repository_configuration_enforces_zero_cost():
    Settings.load(ROOT).assert_zero_cost()


def test_paid_only_or_unpublished_model_is_rejected_even_if_runtime_lists_it():
    policy = Settings.load(ROOT).model_policy
    paid = SimpleNamespace(name="models/gemini-99-pro", display_name="paid", supported_actions=["generateContent"], description="")
    with pytest.raises(NoSupportedModel):
        rank_models([paid], policy)


def test_current_stable_flash_lite_models_are_in_free_runtime_policy():
    policy = Settings.load(ROOT).model_policy
    models = [
        SimpleNamespace(name="models/gemini-3.5-flash-lite", display_name="3.5 lite", supported_actions=["generateContent"], description="Stable"),
        SimpleNamespace(name="models/gemini-3.1-flash-lite", display_name="3.1 lite", supported_actions=["generateContent"], description="Stable"),
    ]
    assert [item.name for item in rank_models(models, policy)] == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]


def test_free_model_policy_retries_once_and_has_bounded_cooldown_cycles():
    settings = Settings.load(ROOT)
    assert settings.model_policy["retriesPerModel"] == 1
    assert settings.model_policy["maxModelCyclesPerRequest"] >= 2
    assert settings.model_policy["modelCycleCooldownSeconds"] >= 60
    assert settings.quota["minimumSuccessfulRequestIntervalSeconds"] >= 60


def test_drive_quota_units_pause_before_crossing_budget():
    guard = DriveQuotaGuard({"maxQuotaUnitsPerRun": 100})
    guard.consume_units(90)
    with pytest.raises(DriveQuotaPaused):
        guard.consume_units(11)
    assert guard.usage.quota_units == 90


def test_drive_download_budget_refuses_unbounded_or_oversized_download():
    guard = DriveQuotaGuard({"maxDownloadBytesPerRun": 1000})
    with pytest.raises(DriveQuotaPaused):
        guard.reserve_download(None)
    with pytest.raises(DriveQuotaPaused):
        guard.reserve_download(1001)
    assert guard.usage.downloaded_bytes == 0


def test_indexer_has_no_search_grounding_or_paid_search_code():
    source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "indexer").rglob("*.py"))
    forbidden = ["GoogleSearch(", "google_search=", "verify_metadata_on_web", "customsearch"]
    assert all(token not in source for token in forbidden)
