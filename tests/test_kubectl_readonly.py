"""Unit tests for bin/kubectl-readonly — the read-only kubectl wrapper that pins
~/.kube/ai-agent.kubeconfig and fails closed on identity, expiry, and RBAC.
Every kubectl round-trip is mocked; nothing here touches a cluster."""
import base64
import json
import subprocess
from unittest import mock

import pytest

from tests import load_script

wrapper = load_script("bin/kubectl-readonly")

RBAC_HEADER = "Resources  Non-Resource URLs  Resource Names  Verbs\n"


def jwt(claims: dict[str, object]) -> str:
    """A structurally valid JWT with the given payload claims. Signature is
    irrelevant — the wrapper only decodes the middle segment."""
    seg = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{seg}.sig"


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def patch_run(**kwargs):
    return mock.patch.object(
        wrapper.subprocess, "run", return_value=completed(**kwargs))


def test_forces_pinned_kubeconfig():
    with mock.patch.dict(wrapper.os.environ, {"KUBECONFIG": "/somewhere/else"}):
        assert wrapper.pinned_env()["KUBECONFIG"] == wrapper.KUBECONFIG


def test_pinned_token_extracts_token():
    cfg = {"users": [{"user": {"token": "tok"}}]}
    with patch_run(stdout=json.dumps(cfg)):
        assert wrapper.pinned_token() == "tok"


@pytest.mark.parametrize("kwargs", [
    {"returncode": 1},
    {"stdout": "not-json"},
    {"stdout": "{}"},
    {"stdout": json.dumps({"users": [{"user": {}}]})},
], ids=["kubectl-error", "invalid-json", "no-users", "no-token"])
def test_pinned_token_fails_closed(kwargs):
    with patch_run(**kwargs):
        assert wrapper.pinned_token() == ""


def test_expiring_token_passes():
    wrapper.assert_token_expires(jwt({"exp": 1}))


@pytest.mark.parametrize("token", [
    "", "static-secret", "a.!!!.c", jwt({"sub": "x"}),
], ids=["empty", "not-a-jwt", "undecodable-claims", "no-exp-claim"])
def test_assert_token_expires_rejects(token):
    with pytest.raises(SystemExit):
        wrapper.assert_token_expires(token)


def test_readonly_grants_pass():
    listing = RBAC_HEADER + (
        "selfsubjectaccessreviews.authorization.k8s.io  []  []  [create]\n"
        "pods  []  []  [get list watch]\n"
        "[/healthz]  []  [get]\n"          # non-resource URL grant
    )
    with patch_run(stdout=listing):
        wrapper.assert_readonly_rbac()


@pytest.mark.parametrize("verbs", ["[get delete]", "[*]"])
def test_write_verb_is_refused(verbs):
    listing = RBAC_HEADER + f"pods  []  []  {verbs}\n"
    with patch_run(stdout=listing), pytest.raises(SystemExit) as excinfo:
        wrapper.assert_readonly_rbac()
    assert "write access" in str(excinfo.value.code)


def test_rbac_enumeration_failure_fails_closed():
    with patch_run(returncode=1), pytest.raises(SystemExit):
        wrapper.assert_readonly_rbac()


@pytest.mark.parametrize("argv", [
    ["--kubeconfig", "/x"], ["--context=other"], ["get", "--as=admin"],
])
def test_redirecting_flags_die_before_any_kubectl(argv):
    with mock.patch.object(wrapper.sys, "argv", ["kubectl-readonly", *argv]), \
            mock.patch.object(wrapper.subprocess, "run") as run, \
            pytest.raises(SystemExit):
        wrapper.main()
    run.assert_not_called()
