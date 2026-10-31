import json
from datetime import datetime, timedelta, timezone

import pytest


class Transcripts:
    """Writes transcript entries the way Claude Code lays them out under ~/.claude/projects."""

    def __init__(self, root):
        self.root = root
        self.now = datetime.now(timezone.utc)

    def write(self, entries, *, name='session', project='C--work', subagent=False):
        folder = self.root / project / (f'{name}/subagents' if subagent else '')
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'{name}.jsonl'
        with path.open('a', encoding='utf-8') as f:
            for entry in entries:
                f.write((entry if isinstance(entry, str) else json.dumps(entry)) + '\n')
        return path

    def stamp(self, *, days_ago=0):
        return (self.now - timedelta(days=days_ago)).isoformat().replace('+00:00', 'Z')

    def user(self, content, *, days_ago=0, session='s-1', **extra):
        return {
            'type': 'user',
            'message': {'role': 'user', 'content': content},
            'timestamp': self.stamp(days_ago=days_ago),
            'sessionId': session,
            'entrypoint': 'cli',
            **extra,
        }

    def assistant(self, content, *, session='s-1', **extra):
        return {
            'type': 'assistant',
            'message': {'role': 'assistant', 'content': content},
            'timestamp': self.stamp(),
            'sessionId': session,
            **extra,
        }


@pytest.fixture
def transcripts(tmp_path):
    return Transcripts(tmp_path / 'projects')


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    """Keeps every test's declined and last-findings state out of the real user data directory."""
    from repeat_o_scope import declined

    monkeypatch.setattr(declined, 'default_dir', lambda: tmp_path / 'data')
