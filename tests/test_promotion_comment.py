"""Unit tests for the PR-comment renderer (GH2) — pure function, no network."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "genie_reviewer"))
from promotion_comment import PROMOTION_COMMENT_MARKER, render_promotion_comment  # noqa: E402

_FAIL = {
    "timeline": [
        {"key": "checks", "label": "Checagens determinísticas", "status": "pass"},
        {"key": "review", "label": "Revisão do agente", "status": "fail"},
    ],
    "gate": {"conclusion": "failure", "blocker_count": 1, "summary": "🔴 Promoção bloqueada."},
    "findings": [{"rule_id": "EVAL-01", "severity": "BLOCKER", "message": "poucas perguntas",
                  "suggestion": "adicione perguntas", "citation": "Handbook › EVAL-01"}],
    "eval": {"status": "advisory", "summary": "🟡 eval indisponível"},
}


def test_marker_is_first_line_for_upsert():
    assert render_promotion_comment(_FAIL, "m@x").splitlines()[0] == PROMOTION_COMMENT_MARKER


def test_attributes_requester_and_ai_note():
    out = render_promotion_comment(_FAIL, "malcoln@x")
    assert "malcoln@x" in out
    assert "IA" in out  # the "achados gerados por IA — verifique" trust note
    assert "OBO" in out  # the truthful execution-identity note


def test_renders_steps_gate_and_findings():
    out = render_promotion_comment(_FAIL, "m@x")
    assert "Checagens determinísticas" in out and "Revisão do agente" in out
    assert "bloqueado" in out.lower()
    assert "EVAL-01" in out and "BLOCKER" in out and "poucas perguntas" in out
    assert "adicione perguntas" in out and "Handbook › EVAL-01" in out


def test_clean_review_says_no_findings():
    clean = {"timeline": [], "gate": {"conclusion": "success", "summary": "🟢 pronto"},
             "findings": [], "eval": {"summary": "x"}}
    out = render_promotion_comment(clean, "m@x")
    assert "Nenhum achado" in out and "liberado" in out.lower()


def test_marker_injection_in_a_finding_is_neutralized():
    # A finding message that smuggles the upsert marker must not duplicate it (which would break
    # in-place comment matching). The real marker stays the only occurrence (line 1).
    r = {"timeline": [], "gate": {"conclusion": "failure", "summary": "x"},
         "findings": [{"rule_id": "X", "severity": "BLOCKER",
                       "message": f"evil {PROMOTION_COMMENT_MARKER} injection"}], "eval": {}}
    out = render_promotion_comment(r, "m@x")
    assert out.count(PROMOTION_COMMENT_MARKER) == 1


def test_finding_without_suggestion_or_citation_omits_those_lines():
    r = {"timeline": [], "gate": {"conclusion": "failure", "summary": "x"},
         "findings": [{"rule_id": "ENV-01", "severity": "BLOCKER", "message": "ref fora do ambiente"}],
         "eval": {}}
    out = render_promotion_comment(r, None)
    assert "ENV-01" in out and "ref fora do ambiente" in out
    assert "↳" not in out  # no suggestion line
    assert "usuário autenticado" in out  # requester fallback when email is None
