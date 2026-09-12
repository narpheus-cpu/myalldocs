from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


MetadataStatus = Literal["confirmed", "inferred", "NEEDS_METADATA_REVIEW"]


@dataclass
class Evidence:
    source: str
    title: str | None = None
    author: str | None = None
    weight: float = 0.0
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class MetadataResolution:
    title: str
    author: str
    confidence: float
    evidence: list[Evidence]
    titleSource: str
    authorSource: str
    conflictDetected: bool
    metadataStatus: MetadataStatus
    manualOverrideApplied: bool = False

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["evidence"] = [item.to_dict() for item in self.evidence]
        return result


@dataclass
class ParsedBook:
    text: str
    format: str
    embedded_title: str | None = None
    embedded_author: str | None = None
    sections: list[str] = field(default_factory=list)


@dataclass
class DriveBook:
    id: str
    name: str
    mimeType: str
    modifiedTime: str | None = None
    md5Checksum: str | None = None
    size: str | None = None
    webViewLink: str | None = None
    parents: list[str] = field(default_factory=list)
    folderPath: list[str] = field(default_factory=list)
