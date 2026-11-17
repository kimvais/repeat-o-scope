import asyncio
import json

import httpx2
import pytest
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul

from repeat_o_scope import judges
from repeat_o_scope.findings import RepeatOScopeError
from repeat_o_scope.judges import Answer

_KEYS = ('TYPESAFE_API_KEY', 'TYPESAFE_AI_API_KEY', 'GEMINI_API_KEY', 'CLOUDFLARE_ACCOUNT_ID', 'CLOUDFLARE_API_KEY')


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    for name in _KEYS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def http(monkeypatch):
    """Answers wire judges from `http.reply(request)`, and records every request."""

    class Http:
        requests = []
        status = 200

        def reply(self, request):
            body = json.loads(request.content)
            answers = {k: {'choice': next(iter(q['criteria']))} if q['type'] == 'choice' else {'noul': 0.9} for k, q in body['questions'].items()}
            doc = {'answers': answers} if 'input' in body else {'result': {'answers': answers}}
            return httpx2.Response(self.status, json=doc)

    http = Http()

    def handler(request):
        http.requests.append(request)
        return http.reply(request)

    real = judges.http_client
    monkeypatch.setattr(judges, 'http_client', lambda **kw: real(transport=httpx2.MockTransport(handler), **kw))
    return http


def _jev_fails(monkeypatch):
    async def refuse(self, **kwargs):
        raise RuntimeError('401 Unauthorized')

    monkeypatch.setattr(AsyncTypeSafeClient, 'system_one', refuse)


def test_no_keys_means_no_judge():
    assert judges.open_judge() is None


def test_half_a_cloudflare_key_is_not_clef(monkeypatch, http):
    monkeypatch.setenv('CLOUDFLARE_ACCOUNT_ID', 'acc')
    assert judges.open_judge() is None
    assert http.requests == []


def test_failed_probe_falls_through_to_the_next_judge(monkeypatch, http):
    _jev_fails(monkeypatch)
    monkeypatch.setenv('TYPESAFE_AI_API_KEY', 'ts')
    monkeypatch.setenv('CLOUDFLARE_ACCOUNT_ID', 'acc')
    monkeypatch.setenv('CLOUDFLARE_API_KEY', 'cf')
    assert judges.open_judge().name == 'clef'


def test_error_status_is_not_available(monkeypatch, http):
    http.status = 401
    monkeypatch.setenv('GEMINI_API_KEY', 'g')
    assert judges.open_judge() is None


def test_gemini_comes_before_clef(monkeypatch, http):
    for name, value in [('GEMINI_API_KEY', 'g'), ('CLOUDFLARE_ACCOUNT_ID', 'acc'), ('CLOUDFLARE_API_KEY', 'cf')]:
        monkeypatch.setenv(name, value)
    assert judges.open_judge().name == 'gemini-decision-1'


_QUESTIONS = {
    'kind': Choice(instructions='Which?', criteria={'a': 'A thing.', 'b': ''}),
    'real': Noul(instructions='Real?'),
}
_WIRE = {
    'kind': {'type': 'choice', 'instructions': 'Which?', 'criteria': {'a': 'A thing.', 'b': ''}},
    'real': {'type': 'noul', 'instructions': 'Real?'},
}


async def _ask(judge):
    async with judge:
        return await judge.ask(state={'failure': 'x'}, questions=_QUESTIONS)


@pytest.mark.parametrize(('judge_class', 'env', 'url', 'header', 'body'), [
    (
        judges._Clef,
        {'CLOUDFLARE_ACCOUNT_ID': 'acc', 'CLOUDFLARE_API_KEY': 'cf'},
        'https://api.cloudflare.com/client/v4/accounts/acc/ai/run/@cf/cloudflare/clef',
        ('authorization', 'Bearer cf'),
        {'model': 'clef', 'state': {'failure': 'x'}, 'questions': _WIRE},
    ),
    (
        judges._Gemini,
        {'GEMINI_API_KEY': 'g'},
        'https://generativelanguage.googleapis.com/v1beta/decisions',
        ('x-goog-api-key', 'g'),
        {'model': 'gemini-decision-1', 'input': '{"failure": "x"}', 'questions': _WIRE},
    ),
])
def test_wire_format(monkeypatch, http, judge_class, env, url, header, body):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    answers = asyncio.run(_ask(judge_class()))
    request = http.requests[-1]
    assert (str(request.url), request.headers[header[0]], json.loads(request.content)) == (url, header[1], body)
    assert answers == {'kind': Answer(choice='a', noul=None), 'real': Answer(choice=None, noul=0.9)}


def test_missing_answer_raises_naming_the_judge(monkeypatch, http):
    monkeypatch.setenv('GEMINI_API_KEY', 'g')
    http.reply = lambda request: httpx2.Response(200, json={'answers': {'real': {'noul': 0.9}}})
    with pytest.raises(RepeatOScopeError, match='gemini-decision-1'):
        asyncio.run(_ask(judges._Gemini()))


@pytest.mark.parametrize('answer', [{'real': {'choice': None}}, {'real': {'noul': None}}])
def test_null_noul_raises_naming_the_judge(monkeypatch, http, answer):
    monkeypatch.setenv('GEMINI_API_KEY', 'g')
    http.reply = lambda request: httpx2.Response(200, json={'answers': {'kind': {'choice': 'a'}} | answer})
    with pytest.raises(RepeatOScopeError, match='gemini-decision-1'):
        asyncio.run(_ask(judges._Gemini()))


def test_choice_outside_criteria_raises(monkeypatch, http):
    monkeypatch.setenv('GEMINI_API_KEY', 'g')
    doc = {'answers': {'kind': {'choice': 'zzz'}, 'real': {'noul': 0.9}}}
    http.reply = lambda request: httpx2.Response(200, json=doc)
    with pytest.raises(RepeatOScopeError, match='zzz'):
        asyncio.run(_ask(judges._Gemini()))
