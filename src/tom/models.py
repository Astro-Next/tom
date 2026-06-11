from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PatrolSettings:
    interval: str = "30m"


@dataclass
class RetroSettings:
    interval: str = "1d"
    time: str = "22:00"


@dataclass
class AgentSettings:
    timeout: str = "30m"
    max_retries: int = 2


@dataclass
class DevSettings:
    concurrent: int = 2


@dataclass
class ReviewSettings:
    concurrent: int = 2


@dataclass
class Settings:
    id: str
    patrol: PatrolSettings = field(default_factory=PatrolSettings)
    retro: RetroSettings = field(default_factory=RetroSettings)
    agent: AgentSettings = field(default_factory=AgentSettings)
    dev: DevSettings = field(default_factory=DevSettings)
    review: ReviewSettings = field(default_factory=ReviewSettings)
