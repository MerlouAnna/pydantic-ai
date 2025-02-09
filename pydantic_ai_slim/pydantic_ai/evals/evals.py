from __future__ import annotations as _annotations

import uuid
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



P = ParamSpec('P')
T = TypeVar('T')


@dataclass
class ScoreableCall(Generic[T]):
    function: Callable[..., T]
    inputs: Any
    output: T

    scores: dict[str, bool | int | float | str]

    call_id: str  # f'{trace_id}:{span_id}' of relevant span
    example_id: str | None = None
    
    def __post_init__(self):
        eval_context = _EVAL_CONTEXT.get()
        if eval_context is not None:
            eval_context.scoreable_calls.append(self)

    def set_score(self, name: str, value: float | int) -> None:
        self.scores[name] = value


@dataclass(init=False)
class EvalContext:
    _eval_token: Token[Any] | None = None
    
    def __init__(self, attributes: dict[str, Any]):
        self.attributes = {**attributes, 'eval_id': uuid.uuid4()}
        self.scoreable_calls: list[ScoreableCall] = []

    def __enter__(self):
        self._eval_token = _EVAL_CONTEXT.set(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        assert self._eval_token is not None
        _EVAL_CONTEXT.reset(self._eval_token)

    def get_calls_data(self):
        data = {}
        for call in self.scoreable_calls:
            if call.function not in data:
                data[call.function] = {}
            data_for_function = data[call.function]
            if call.example_id not in data_for_function:
                data_for_function[call.example_id] = {}
            else:
                raise ValueError(f"Multiple calls to same function with the same example_id: {call}")
            data_for_function[call.example_id] = call.scores
        return data

    def print_summary(self):
        data = self.get_calls_data()
        for function, examples in data.items():
            print(f"Function: {function}")
            # TODO: Need to format the following as a table
            for example_id, scores in examples.items():
                print(f"  Example: {example_id}")
                for score_name, score_value in scores.items():
                    print(f"    {score_name}: {score_value}")


class Scoreable(Generic[P, T]):
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
    def __init__(self, f: Callable[P, T]):
        self.f = f

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> T:
        return self.call_for_scoring(*args, **kwargs).output

    def call_for_scoring(self, *args: P.args, **kwargs: P.kwargs) -> ScoreableCall[T]:
        # eval = _EVAL_CONTEXT.get()
        # attributes = {} if eval is None else eval.attributes

        with ExitStack() as _stack:
            # TODO: if otel is installed, start a span here in _stack with attributes, and store its trace and span_ids;
            #   otherwise, use the following trace_id and span_id
            # The started span should have some special attribute that can be used to determine it is a scored function
            trace_id = '00000000000000000000000000000000'
            span_id = '0000000000000000'

            with _ScoringContext() as scoring_context:
                result = self.f(*args, **kwargs)

            return ScoreableCall(self.f, (args, kwargs), result, scoring_context.scores, f'{trace_id}:{span_id}')


def set_score(name: str, value: bool | int | float | str) -> None:
    """Set a score on all currently-active scoring contexts."""
    for scoring_context in _SCORING_CONTEXTS.get():
        scoring_context.set_score(name, value)


def add_count(name: str, amount: int) -> None:
    """Increment a count metric on all currently-active scoring contexts."""
    for scoring_context in _SCORING_CONTEXTS.get():
        scoring_context.add_count(name, amount)


_EVAL_CONTEXT = ContextVar[EvalContext | None]('_EVAL_CONTEXT', default=None)
_SCORING_CONTEXTS = ContextVar[tuple[_ScoringContext, ...]]('_SCORING_CONTEXTS', default=())

def record_score(name: str, trace_id: str, span_id: str, value: float | int | bool | str, comment: str | None) -> None:
    """
    Using this approach allows you to record scores flexibly.

    Note: We still need to support 
    """
    pass
