"""Typed judgments from Jev (TypeSafe System One) where clustering alone cannot tell things apart."""

import asyncio
import logging

from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul

from repeat_o_scope.transcripts import ToolFailure

_LOGGER = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 30
_CONCURRENCY = 8
_ASK = Choice(
    instructions=(
        'This is the last line of an AI coding assistant\'s turn, and the user answered it only with a go-ahead '
        'such as "ship", "go" or "yes". What kind of message is `closing`?'
    ),
    criteria={
        'permission': (
            'Asks for permission or a go-ahead for a next step the assistant could have taken by itself: push, '
            'commit, ship, post, run, continue. Includes "say "ship" and I\'ll …", "reply go and …", "want me to …?".'
        ),
        'choice': 'Asks the user to pick between real alternatives that change what gets built or done.',
        'clarifying': 'Asks for a fact or a preference that only the user knows.',
        'review': 'Asks the user to look at and approve something shown: a design, a draft, a diff, a plan.',
        'report': 'Asks for nothing: states a result, a status, a caveat or a list item.',
    },
)
_REAL = Noul(
    instructions=(
        'An AI coding assistant used the tool `tool` to do `attempt`, and the result was flagged as an error with '
        '`error` as its first line. Did the attempt really fail to do what it was meant to do? Answer no when '
        '`error` is only normal output written to stderr, a warning, a progress line, or a non-zero exit the '
        'command was expected to produce, such as a test run or a linter reporting what it found.'
    ),
)


def make_client() -> AsyncTypeSafeClient:
    """Returns a Jev client; reads the API key from `TYPESAFE_API_KEY`."""
    return AsyncTypeSafeClient(timeout=_TIMEOUT_SECONDS)


def kinds_of_asks(closings: list[str]) -> list[str]:
    """Returns the kind of ask (a key of `_ASK.criteria`) for each closing line."""
    answers = _ask_all([({'closing': c}, {'kind': _ASK}) for c in closings])
    return [a.choices['kind'].choice for a in answers]


def real_failures(failures: list[ToolFailure]) -> list[bool]:
    """Returns, for each failure, whether Jev judges it a real failure rather than noise on stderr."""
    states = [{'tool': f.tool, 'attempt': f.attempt, 'error': f.error} for f in failures]
    answers = _ask_all([(s, {'real': _REAL}) for s in states])
    return [a.nouls['real'].noul >= 0.5 for a in answers]


def _ask_all(requests: list[tuple[dict, dict]]) -> list:
    """Returns the answers to independent requests, run concurrently in request order."""

    async def run():
        limit = asyncio.Semaphore(_CONCURRENCY)
        async with make_client() as client:

            async def one(state, questions):
                async with limit:
                    return await client.system_one(state=state, questions=questions)

            return await asyncio.gather(*(one(s, q) for s, q in requests))

    answers = asyncio.run(run())
    _LOGGER.info('Asked Jev `%s` questions.', len(answers))
    return answers
