from __future__ import annotations as _annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Callable, Generic, ParamSpec, TypeVar



@dataclass
class _ScoringCounter:
    count: int = 0

    def add(
        self,
        amount: int,
    ) -> None:
        pass


@dataclass(init=False)
class _ScoringContext:
    def __init__(self):
        self.scores: dict[str, int | float] = {}
        # TODO: Use an API more similar to opentelemetry metrics for getting counters etc., e.g.:
        #   self.meter = MeterProvider().get_meter(__name__)
        # TODO: Consider adding support for up_down_counter, histogram, gauge, etc.
        self._counters: dict[str, _ScoringCounter] = {}

    def __enter__(self):
        # TODO: If otel is present, start a span here, and store the `trace_id:span_id` as self.feedback_id
        #    May want to have a similar API to logfire for this
        _SCORING_CONTEXTS.set(_SCORING_CONTEXTS.get() + (self,))
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # TODO: If otel is present, end the span here
        _SCORING_CONTEXTS.set(_SCORING_CONTEXTS.get()[:-1])
        for k, v in self._counters.items():
            self.scores[f'count:{k}'] = v.count

    def set_score(self, name: str, value: float | int) -> None:
        self.scores[name] = value

    def add_count(self, name: str, amount: int) -> None:
        if name not in self._counters:
            self._counters[name] = _ScoringCounter()
        self._counters[name].add(amount)



@contextmanager
def scoring_attributes(attributes: dict[str, Any]) -> Iterator[None]:
    existing_attributes = _SCORING_ATTRIBUTES_CONTEXT.get()
    token = _SCORING_ATTRIBUTES_CONTEXT.set({**existing_attributes, **attributes})
    try:
        yield
    finally:
        _SCORING_ATTRIBUTES_CONTEXT.reset(token)


P = ParamSpec('P')
T = TypeVar('T')


@dataclass
class ScoredResult(Generic[T]):
    function: Callable[..., T]
    inputs: Any
    output: T

    scores: dict[str, int | float]

    trace_id: str
    span_id: str
    attributes: dict[str, Any]


@dataclass(init=False)
class EvalContext:
    _eval_token: Token[Any] | None = None
    _scoring_attributes_token: Token[Any] | None = None

    def __init__(self, attributes: dict[str, Any]):
        self.attributes = attributes
        self.scored_function_calls: list[ScoredResult] = []

    def __enter__(self):
        self._scoring_attributes_token = _SCORING_ATTRIBUTES_CONTEXT.set(self.attributes)
        self._eval_token = _EVAL_CONTEXT.set(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        assert self._scoring_attributes_token is not None
        _SCORING_ATTRIBUTES_CONTEXT.reset(self._scoring_attributes_token)
        assert self._eval_token is not None
        _EVAL_CONTEXT.reset(self._eval_token)


class ScoreableFunction(Generic[P, T]):
    """
    Decorator for a function. If applied, should ensure enough information is recorded to do scoring later.

    In particular, we should record the function's inputs, outputs, and any scores that are generated.
    Scorers _can_ be added for eager execution, but this is not necessary.
    There also needs to be a way to call the function that returns something that includes the feedback id.

    Should be possible to add additional attributes (possibly via the EvalContext thing?) that can be used
    to filter to groups of these function calls for aggregated analysis.

    Decorating a function in this way also ensures that any calls to `set_score` or `add_count` within the function
    are recorded in the context of the function call.

    NOTE: it may be possible to get updates to the metrics "for free" from otel instrumentations by using
    an appropriately-defined span processor.

    Still need APIs for performing post-hoc scoring, recording feedback, consuming scores, and consuming feedback.
    """
    def __init__(self, f: Callable[P, T], scorers: dict[str, Callable[[T], int | float]]):
        self.f = f
        self.scorers = scorers

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        return self.call_with_scoring(*args, **kwargs).scores

    def with_scorers(
        self, scorers: dict[str, Callable[[T], int | float]], replace: bool = False
    ) -> ScoreableFunction[P, T]:
        # This provides a way to have example-specific scorers, e.g. during benchmark-like evals
        new_scorers = scorers if replace else {**self.scorers, **scorers}
        return ScoreableFunction(self.f, new_scorers)

    def call_with_scoring(self, *args: P.args, **kwargs: P.kwargs) -> ScoredResult[T]:
        attributes = _SCORING_ATTRIBUTES_CONTEXT.get()

        with ExitStack() as _stack:
            # TODO: if otel is installed, start a span here in _stack with attributes, and store its trace and span_ids;
            #   otherwise, use the following trace_id and span_id
            # The started span should have some special attribute that can be used to determine it is a scored function
            trace_id = '00000000000000000000000000000000'
            span_id = '0000000000000000'

            with _ScoringContext() as scoring_context:
                result = self.f(*args, **kwargs)

            for name, output_scorer in self.scorers.items():
                scoring_context.set_score(name, output_scorer(result))

            return ScoredResult(self.f, (args, kwargs), result, scoring_context.scores, trace_id, span_id, attributes)


def set_score(name: str, value: float | int) -> None:
    for scoring_context in _SCORING_CONTEXTS.get():
        scoring_context.set_score(name, value)


def add_count(name: str, amount: int) -> None:
    for scoring_context in _SCORING_CONTEXTS.get():
        scoring_context.add_count(name, amount)


_EVAL_CONTEXT = ContextVar[EvalContext | None]('_EVAL_CONTEXT', default=None)
_SCORING_ATTRIBUTES_CONTEXT = ContextVar[dict[str, Any]]('_SCORING_ATTRIBUTES', default={})
_SCORING_CONTEXTS = ContextVar[tuple[_ScoringContext, ...]]('_SCORING_CONTEXTS', default=())
