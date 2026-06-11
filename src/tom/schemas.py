PM_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["need-dev", "parent", "blocked"]},
        "type": {"type": "string", "enum": ["feature", "bug"]},
        "priority": {"type": "string", "enum": ["p0", "p1", "p2"]},
        "children": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "acceptanceCriteria": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "context": {"type": "string"},
                    "priority": {
                        "type": "string",
                        "enum": ["p0", "p1", "p2"],
                    },
                },
                "required": [
                    "title",
                    "description",
                    "acceptanceCriteria",
                    "context",
                    "priority",
                ],
            },
        },
        "reason": {"type": "string"},
    },
    "required": ["decision"],
}

DEV_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["success", "failure"]},
        "prTitle": {"type": "string"},
        "prBody": {"type": "string"},
        "comment": {"type": "string"},
        "failureReason": {"type": "string"},
    },
    "required": ["status"],
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["success", "failure"]},
        "verdict": {
            "type": "string",
            "enum": ["approved", "changes-requested"],
        },
        "comment": {"type": "string"},
        "failureReason": {"type": "string"},
    },
    "required": ["status"],
}

ANALYST_SCHEMA = {
    "type": "object",
    "properties": {
        "hasFindings": {"type": "boolean"},
        "title": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["hasFindings"],
}
