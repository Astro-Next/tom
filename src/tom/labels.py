from __future__ import annotations

import logging
from dataclasses import dataclass

from tom.github import GitHubClient

_log = logging.getLogger("tom")


@dataclass(frozen=True)
class LabelDef:
    name: str
    color: str
    description: str


LABELS: list[LabelDef] = [
    # Workflow — dev track (blue)
    LabelDef("need-dev", "C8E1FF", "Waiting for dev dispatch"),
    LabelDef("in-dev", "1D76DB", "Dev agent working"),
    # Workflow — review track (purple)
    LabelDef("need-review", "D4C5F9", "Waiting for review dispatch"),
    LabelDef("in-review", "7057FF", "Review agent working"),
    # Workflow — escalation
    LabelDef("blocked", "B60205", "Needs human intervention"),
    # Structural
    LabelDef("parent", "065F0A", "Multi-PR issue broken into children"),
    # Priority (red — darker = more urgent)
    LabelDef("p0", "B60205", "Critical — dispatched first"),
    LabelDef("p1", "E99695", "High priority"),
    LabelDef("p2", "F9D0C4", "Normal priority"),
    # Type
    LabelDef("feature", "0E8A16", "New functionality"),
    LabelDef("bug", "D73A4A", "Defect fix"),
]


async def sync_labels(client: GitHubClient, *, clean: bool = False) -> None:
    existing = await client.list_labels()
    existing_by_name = {label["name"]: label for label in existing}
    managed_names = {label_def.name for label_def in LABELS}

    if clean:
        for name in existing_by_name:
            if name not in managed_names:
                await client.delete_label(name)
                _log.info("Deleted label: %s", name)

    for label_def in LABELS:
        if label_def.name in existing_by_name:
            existing_label = existing_by_name[label_def.name]
            if existing_label["color"] != label_def.color or existing_label.get("description", "") != label_def.description:
                await client.update_label(label_def.name, color=label_def.color, description=label_def.description)
                _log.info("Updated label: %s", label_def.name)
        else:
            await client.create_label(label_def.name, label_def.color, label_def.description)
            _log.info("Created label: %s", label_def.name)
