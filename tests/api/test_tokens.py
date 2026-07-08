from __future__ import annotations

import time

import pytest
from pytest import MonkeyPatch

from selfevals.api.tokens import TokenError, issue_token, verify_token


def _with_secret(monkeypatch: MonkeyPatch, secret: str = "test-secret") -> None:
    monkeypatch.setenv("SELFEVALS_AUTH_SECRET", secret)


def test_issue_and_verify_round_trips(monkeypatch: MonkeyPatch) -> None:
    _with_secret(monkeypatch)
    token = issue_token("alice", ttl_seconds=60)
    verified = verify_token(token)
    assert verified.user_id == "alice"


def test_verify_rejects_tampered_signature(monkeypatch: MonkeyPatch) -> None:
    _with_secret(monkeypatch)
    token = issue_token("alice", ttl_seconds=60)
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    with pytest.raises(TokenError):
        verify_token(tampered)


def test_verify_rejects_tampered_user_id(monkeypatch: MonkeyPatch) -> None:
    _with_secret(monkeypatch)
    token = issue_token("alice", ttl_seconds=60)
    _user_id, expires_at, signature = token.split(".")
    forged = f"mallory.{expires_at}.{signature}"
    with pytest.raises(TokenError):
        verify_token(forged)


def test_verify_rejects_expired_token(monkeypatch: MonkeyPatch) -> None:
    _with_secret(monkeypatch)
    token = issue_token("alice", ttl_seconds=-1)
    with pytest.raises(TokenError, match="expired"):
        verify_token(token)


def test_verify_rejects_malformed_token(monkeypatch: MonkeyPatch) -> None:
    _with_secret(monkeypatch)
    with pytest.raises(TokenError, match="malformed"):
        verify_token("not-a-token")


def test_verify_rejects_wrong_secret(monkeypatch: MonkeyPatch) -> None:
    _with_secret(monkeypatch, secret="secret-a")
    token = issue_token("alice", ttl_seconds=60)
    _with_secret(monkeypatch, secret="secret-b")
    with pytest.raises(TokenError, match="invalid signature"):
        verify_token(token)


def test_issue_token_requires_user_id(monkeypatch: MonkeyPatch) -> None:
    _with_secret(monkeypatch)
    with pytest.raises(TokenError):
        issue_token("", ttl_seconds=60)


def test_issue_token_requires_configured_secret(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("SELFEVALS_AUTH_SECRET", raising=False)
    with pytest.raises(TokenError):
        issue_token("alice", ttl_seconds=60)


def test_expires_at_reflects_ttl(monkeypatch: MonkeyPatch) -> None:
    _with_secret(monkeypatch)
    before = int(time.time())
    token = issue_token("alice", ttl_seconds=120)
    verified = verify_token(token)
    assert before + 120 <= verified.expires_at <= before + 121
