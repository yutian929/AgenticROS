"""Session lifecycle invariants that prevent accidental replay or shared owners."""
import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from rosbody import chat


def test_exclusive_session(tmp_path):
    with chat.session_lock(tmp_path):
        with pytest.raises(RuntimeError, match='already open'):
            with chat.session_lock(tmp_path):
                pytest.fail('Concurrent owner admitted')
    with chat.session_lock(tmp_path):
        pass


def test_failed_turn_is_not_replayed(tmp_path):
    class Payload:
        def model_dump(self, **kwargs):
            return {'turn': {'status': 'failed', 'error': 'test failure'}}
    class Thread:
        calls = 0
        def turn(self, prompt):
            self.calls += 1
            return SimpleNamespace(stream=lambda: iter([SimpleNamespace(method='turn/completed', payload=Payload())]))
    thread = Thread()
    with pytest.raises(RuntimeError, match='failed'):
        chat.run_turn(thread, 'move', tmp_path)
    assert thread.calls == 1
    assert 'test failure' in (tmp_path / 'events.jsonl').read_text()


def test_missing_auth_does_not_start_simulator(monkeypatch):
    monkeypatch.setattr(chat, 'authenticated', lambda: False)
    with pytest.raises(RuntimeError, match='login'):
        chat.chat(argparse.Namespace())


def test_interruption_requests_turn_cancel(tmp_path):
    class Turn:
        interrupted = False
        def stream(self):
            raise KeyboardInterrupt
        def interrupt(self):
            self.interrupted = True
    turn = Turn()
    with pytest.raises(KeyboardInterrupt):
        chat.run_turn(SimpleNamespace(turn=lambda prompt: turn), 'move', tmp_path)
    assert turn.interrupted


def test_domain_leases_are_distinct(monkeypatch, tmp_path):
    monkeypatch.setattr(chat, 'ROOT', tmp_path)
    monkeypatch.setattr(chat.secrets, 'randbelow', lambda n: 0)
    with chat.domain_lease() as first, chat.domain_lease() as second:
        assert first != second
    with chat.domain_lease() as reused:
        assert reused == first


def test_perception_rejects_unknown_kind_before_reading_image():
    from rosbody.perception import locate_color
    with pytest.raises(ValueError, match='kind'):
        locate_color({}, kind='cube')
