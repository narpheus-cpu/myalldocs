from __future__ import annotations


ALLOWED_TYPES = {
    "fiction", "drama", "poetry", "academic", "philosophy", "history_biography",
    "science_technical", "essay_general_nonfiction", "practical_manual", "mixed_anthology", "unknown",
}


def choose_profile(classification: dict, profiles_config: dict, override: str | None = None) -> tuple[str, dict]:
    requested = override or str(classification.get("documentType", "unknown"))
    if requested not in ALLOWED_TYPES or requested not in profiles_config["profiles"]:
        requested = "unknown"
    confidence = float(classification.get("confidence", 0) or 0)
    if not override and confidence < 0.55:
        requested = "unknown"
    return requested, profiles_config["profiles"][requested]
