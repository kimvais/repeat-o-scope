"""Opens the first judge that answers: Jev, then Gemini Decision 1, then Clef."""

import abc
import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import override

from repeat_o_scope.findings import RepeatOScopeError

_LOGGER = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Answer:
    choice: str | None
    noul: float | None


class Judge(abc.ABC):
    """Answers typed questions (TypeSafe `Choice` and `Noul`) about a state; use inside `async with`."""

    name: str = NotImplemented
    env: tuple[str, ...] = NotImplemented

    def available(self) -> bool:
        return all(os.environ.get(name) for name in self.env)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def ask(self, *, questions: dict, state: dict) -> dict[str, Answer]:
        """Returns one answer per question, or raises naming this judge."""
        try:
            answers = await self._ask(questions=questions, state=state)
        except Exception as err:
            raise RepeatOScopeError(f'Judge `{self.name}` failed: {err}') from err
        if set(answers) != set(questions):
            raise RepeatOScopeError(f'Judge `{self.name}` answered `{sorted(answers)}`, asked `{sorted(questions)}`!')
        return answers

    @abc.abstractmethod
    async def _ask(self, *, questions: dict, state: dict) -> dict[str, Answer]: ...


class _Jev(Judge):
    name = 'jev'
    # vainu-data names the key TYPESAFE_AI_API_KEY.
    _KEYS = ('TYPESAFE_API_KEY', 'TYPESAFE_AI_API_KEY')

    @override
    def available(self) -> bool:
        return any(os.environ.get(k) for k in self._KEYS)

    @override
    async def __aenter__(self):
        from typesafe_sdk import AsyncTypeSafeClient

        api_key = next(os.environ[k] for k in self._KEYS if os.environ.get(k))
        self._client = AsyncTypeSafeClient(api_key=api_key, timeout=_TIMEOUT_SECONDS)
        await self._client.__aenter__()
        return self

    @override
    async def __aexit__(self, *exc):
        return await self._client.__aexit__(*exc)

    @override
    async def _ask(self, *, questions: dict, state: dict) -> dict[str, Answer]:
        r = await self._client.system_one(state=state, questions=questions)
        return {k: Answer(choice=c.choice, noul=None) for k, c in r.choices.items()} | {
            k: Answer(choice=None, noul=n.noul) for k, n in r.nouls.items()
        }


class _Wire(Judge):
    """A judge behind plain HTTP that takes Jev's question format."""

    @override
    async def __aenter__(self):
        self._client = http_client(headers=self._headers())
        return self

    @override
    async def __aexit__(self, *exc):
        await self._client.aclose()
        return False

    @override
    async def _ask(self, *, questions: dict, state: dict) -> dict[str, Answer]:
        wire = {k: _to_wire(q) for k, q in questions.items()}
        response = await self._client.post(self._url(), json=self._body(questions=wire, state=state))
        response.raise_for_status()
        return {
            k: Answer(choice=a.get('choice'), noul=a.get('noul'))
            for k, a in self._answers(response.json()).items()
        }

    @abc.abstractmethod
    def _headers(self) -> dict: ...

    @abc.abstractmethod
    def _url(self) -> str: ...

    @abc.abstractmethod
    def _body(self, *, questions: dict, state: dict) -> dict: ...

    @abc.abstractmethod
    def _answers(self, doc: dict) -> dict: ...


class _Gemini(_Wire):
    name = 'gemini-decision-1'
    env = ('GEMINI_API_KEY',)

    @override
    def _headers(self) -> dict:
        return {'x-goog-api-key': os.environ['GEMINI_API_KEY']}

    @override
    def _url(self) -> str:
        return 'https://generativelanguage.googleapis.com/v1beta/decisions'

    @override
    def _body(self, *, questions: dict, state: dict) -> dict:
        # The Decisions API takes one input string, not a state object.
        return {'model': self.name, 'input': json.dumps(state, ensure_ascii=False), 'questions': questions}

    @override
    def _answers(self, doc: dict) -> dict:
        return doc['answers']


class _Clef(_Wire):
    name = 'clef'
    env = ('CLOUDFLARE_ACCOUNT_ID', 'CLOUDFLARE_API_KEY')

    @override
    def _headers(self) -> dict:
        return {'Authorization': f'Bearer {os.environ["CLOUDFLARE_API_KEY"]}'}

    @override
    def _url(self) -> str:
        account = os.environ['CLOUDFLARE_ACCOUNT_ID']
        return f'https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/@cf/cloudflare/clef'

    @override
    def _body(self, *, questions: dict, state: dict) -> dict:
        return {'model': self.name, 'state': state, 'questions': questions}

    @override
    def _answers(self, doc: dict) -> dict:
        return doc['result']['answers']


_JUDGES = (_Jev, _Gemini, _Clef)


def http_client(**kwargs):
    """Returns the HTTP client the wire judges use."""
    # httpx2 because typesafe-sdk already brings it.
    import httpx2

    return httpx2.AsyncClient(timeout=_TIMEOUT_SECONDS, **kwargs)


def open_judge() -> Judge | None:
    """Returns the first judge whose keys are set and that answers a test question; None if none does."""
    for judge_class in _JUDGES:
        judge = judge_class()
        if not judge.available():
            continue
        try:
            asyncio.run(_probe(judge))
        # Any failure, a missing library, a bad key or no network, only means trying the next judge.
        except Exception as err:
            _LOGGER.warning('Judge `%s` unavailable: %s', judge.name, err)
            continue
        _LOGGER.info('Judging with `%s`.', judge.name)
        return judge
    _LOGGER.info('No judge available.')
    return None


async def _probe(judge: Judge) -> None:
    from typesafe_sdk import Noul

    async with judge:
        await judge.ask(state={'a': 2, 'b': 1}, questions={'probe': Noul(instructions='Is `a` greater than `b`?')})


def _to_wire(question) -> dict:
    from typesafe_sdk import Noul

    if isinstance(question, Noul):
        # Our nouls carry no criteria; Gemini and Clef would want `true_description` / `false_description`.
        assert question.criteria is None, 'Noul criteria are not sent to wire judges'
        return {'type': 'noul', 'instructions': question.instructions}
    return {'type': 'choice', 'instructions': question.instructions, 'criteria': dict(question.criteria)}
