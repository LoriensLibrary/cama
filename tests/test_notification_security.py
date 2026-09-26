"""Untrusted Hive signals must never become executable PowerShell source."""
import json
import sys

from cama.hive import cama_hive_watcher as watcher


def test_toast_passes_untrusted_content_as_data(monkeypatch):
    monkeypatch.setitem(sys.modules, 'plyer', None)
    calls = []
    monkeypatch.setattr(watcher.subprocess, 'run', lambda *a, **kw: calls.append((a, kw)))
    payload = '$(Start-Process calc); "quoted" <xml> café 🌿'
    watcher.notify_windows(payload, payload)
    (args, kwargs), = calls
    assert payload not in args[0][-1]
    assert json.loads(kwargs['input']) == {'title': payload, 'message': payload}
    assert '-NoProfile' in args[0]
    assert kwargs['timeout'] == 5


def test_toast_failure_is_nonfatal(monkeypatch):
    monkeypatch.setitem(sys.modules, 'plyer', None)
    def fail(*args, **kwargs):
        raise OSError('PowerShell unavailable')
    monkeypatch.setattr(watcher.subprocess, 'run', fail)
    watcher.notify_windows('title', 'message')
