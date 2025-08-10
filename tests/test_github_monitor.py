import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
from webhook_monitor import GitHubMonitor


def test_verify_signature_invalid_header_returns_false():
    monitor = GitHubMonitor()
    monitor.secret = "secret"
    assert monitor.verify_signature(b"payload", "invalid") is False
