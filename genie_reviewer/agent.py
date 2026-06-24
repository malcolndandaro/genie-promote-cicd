"""Genie Reviewer — MLflow ResponsesAgent (S5).

Imperative shell around the pure cores: serialized_space JSON -> build_space_context ->
build_review_prompt (ALL handbook rules inline, PT) -> Claude -> parse_review.
Deployable to Model Serving via the agent-as-code lifecycle (reuses bimbo's pattern).
The handbook is small, so rules are grounded fully in-context (Vector Search is the
scale path, not needed at 9 rules).
"""
from __future__ import annotations

import json

import handbook_rules
import mlflow
import review_core
from mlflow.deployments import get_deploy_client
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse

LLM_ENDPOINT = "databricks-claude-opus-4-8"


def _input_text(req: ResponsesAgentRequest) -> str:
    parts: list[str] = []
    for item in req.input:
        d = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        c = d.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            for seg in c:
                if isinstance(seg, dict) and seg.get("text"):
                    parts.append(seg["text"])
    return "\n".join(parts)


def _call_llm(system: str, user: str) -> str:
    client = get_deploy_client("databricks")
    resp = client.predict(
        endpoint=LLM_ENDPOINT,
        # No `temperature`: opus-4-8 rejects it (400). max_tokens generous so a verbose
        # PT review with citations doesn't truncate mid-JSON (which would parse to empty).
        inputs={"messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "max_tokens": 3000},
    )
    return resp["choices"][0]["message"]["content"]


class GenieReviewer(ResponsesAgent):
    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        # input = the serialized_space JSON, or {"serialized_space": {...},
        # "grant_findings": [...]} with the deterministic GRANT-01 check results.
        raw_in = _input_text(request)
        try:
            payload = json.loads(raw_in)
        except (ValueError, TypeError):
            return self._respond({
                "summary": "Entrada inválida — não é um serialized_space JSON.",
                "findings": [], "gate": {"conclusion": "neutral", "blocker_count": 0,
                                         "summary": "⚠️ entrada ilegível — revisar manualmente."}})
        grant_findings = None
        if isinstance(payload, dict) and "serialized_space" in payload:
            space, grant_findings = payload["serialized_space"], payload.get("grant_findings")
        else:
            space = payload
        if isinstance(space, str):
            space = json.loads(space)

        ctx = review_core.build_space_context(space)
        system, user = review_core.build_review_prompt(ctx, handbook_rules.RULES, grant_findings)
        raw = _call_llm(system, user)
        review = review_core.parse_review(raw)
        # Deterministic GRANT-01/EVAL-01 are merged into the gate — they can't be
        # softened or injection-suppressed by the LLM (S5 review fix).
        findings = review_core.finalize_findings(review["findings"], grant_findings, ctx["n_benchmark"])
        gate = review_core.decide_gate(findings)
        # A non-empty but unparseable LLM response must not read as a clean pass.
        if raw.strip() and not review["summary"] and not review["findings"] and gate["conclusion"] == "success":
            gate = {"conclusion": "neutral", "blocker_count": 0,
                    "summary": "⚠️ Revisão do agente ilegível — revisar manualmente."}
        return self._respond({"summary": review["summary"] or gate["summary"],
                              "findings": findings, "gate": gate})

    def _respond(self, review: dict) -> ResponsesAgentResponse:
        return ResponsesAgentResponse(
            output=[self.create_text_output_item(
                text=json.dumps(review, ensure_ascii=False), id="acme_review_1")])


mlflow.models.set_model(GenieReviewer())
