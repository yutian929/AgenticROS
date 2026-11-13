import argparse
import pytest
from rosbody.chat import add_chat_commands, run_turn
from rosbody.config import configure


def parse(*args):
    parser = argparse.ArgumentParser()
    add_chat_commands(parser.add_subparsers(dest='command'))
    return parser.parse_args(['chat', *args])


def test_cli_overrides_config_including_false(tmp_path):
    p = tmp_path / 'body.toml'
    p.write_text('model="test-model"\nreasoning_effort="high"\nviewer=true\nverbose=true\nseed=42\n')
    args = configure(parse('--config', str(p), '--no-viewer', '--seed', '0'))
    assert args.model == 'test-model' and args.reasoning_effort == 'high'
    assert args.viewer is False and args.verbose is True and args.seed == 0


@pytest.mark.parametrize('content', ['viewer="false"', 'seed=true', 'seed=-1', 'model=""', 'reasoning_effort="typo"', 'vewer=true'])
def test_invalid_config_rejected(tmp_path, content):
    p = tmp_path / 'body.toml'
    p.write_text(content)
    with pytest.raises(RuntimeError):
        configure(parse('--config', str(p)))


def test_missing_explicit_file_rejected(tmp_path):
    with pytest.raises(RuntimeError, match='does not exist'):
        configure(parse('--config', str(tmp_path / 'missing.toml')))


def test_effort_passed_to_each_turn(tmp_path):
    from types import SimpleNamespace
    seen = []
    def turn(prompt, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(stream=lambda: iter([]))
    run_turn(SimpleNamespace(turn=turn), 'hello', tmp_path, reasoning_effort='high')
    assert seen == [{'effort': 'high'}]
