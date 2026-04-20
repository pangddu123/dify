from abc import ABC, abstractmethod
from typing import ClassVar, TypedDict


class AggregationInput(TypedDict):
    source_id: str
    text: str


class AggregationResult(TypedDict):
    text: str
    metadata: dict[str, object]


class AggregationStrategy(ABC):
    name: ClassVar[str] = ""
    config_schema: ClassVar[dict[str, object]] = {}

    @abstractmethod
    def aggregate(
        self,
        inputs: list[AggregationInput],
        config: dict[str, object],
    ) -> AggregationResult: ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
