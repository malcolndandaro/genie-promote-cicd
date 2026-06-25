"""Acme · Promotor de Genie/AI-BI — the non-technical front door (S8-S11).

Streamlit Databricks App. Author lists/creates Genie Spaces and requests promotion;
the engine reviews live (pre-render + allowlist + Genie Reviewer + eval gate); the
Steward approves (separation of duties). Wired to the live Genie API + the proven
engine via app_logic. (AppKit/React polish is the D7 follow-up.)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app_logic
import streamlit as st

# Identities are config, not literals (ADR-0004). The Steward is config; the REQUESTER is
# the authenticated user (derived below from the OBO principal header on the deployed app).
STEWARD = os.environ.get("APP_STEWARD", "pedro.perdomo@databricks.com")

st.set_page_config(page_title="Acme · Promotor de Genie/AI-BI", layout="wide")


def _auth() -> tuple[str | None, str | None, str]:
    """Return (profile, user_token, requester) for the current context.

    Deployed app -> On-Behalf-Of: the platform forwards the signed-in user's OAuth token
    (`x-forwarded-access-token`) and email (`x-forwarded-email`), so the app acts as the
    user and respects their own data access. Local dev -> a CLI profile (APP_PROFILE) and
    a configured requester. (Headers only exist when deployed.)
    """
    # NB: st.context.headers is case-insensitive — call .get() on it directly. Do NOT
    # dict()-convert first: the proxy sends Title-Cased keys (X-Forwarded-Access-Token) and
    # a plain dict lookup with a lowercase key would miss.
    try:
        headers = st.context.headers
    except Exception:  # noqa: BLE001
        headers = None
    token = headers.get("x-forwarded-access-token") if headers else None
    email = headers.get("x-forwarded-email") if headers else None
    if token:
        return None, token, (email or "usuário autenticado")
    return (os.environ.get("APP_PROFILE") or None), None, \
        os.environ.get("APP_REQUESTER", "malcoln.dandaro@databricks.com")


PROFILE, USER_TOKEN, REQUESTER = _auth()
ON_BEHALF = USER_TOKEN is not None
ss = st.session_state
ss.setdefault("review", None)
ss.setdefault("approved", False)

# --- header / persona -----------------------------------------------------------
st.title("Acme · Promotor de Genie/AI-BI")
persona_label = st.sidebar.radio("Logado como", ["Malcoln · Autor", "Pedro · Steward"])
persona = "steward" if "Steward" in persona_label else "author"
_origem = f"OBO · {REQUESTER}" if ON_BEHALF else (PROFILE or "auth padrão")
st.sidebar.caption(f"Origem: `{_origem}` · domínio: `{app_logic.DOMAIN}`")
st.sidebar.caption("Segregação de funções: Autor ≠ Steward ≠ service principal (ADR-0002).")

tab_spaces, tab_new = st.tabs(["Meus espaços", "＋ Novo Genie Space"])

# --- Meus espaços (S8) ----------------------------------------------------------
with tab_spaces:
    try:
        spaces = app_logic.list_spaces(PROFILE, user_token=USER_TOKEN)
    except Exception as e:  # noqa: BLE001
        st.error(f"Não foi possível listar os espaços: {e}")
        spaces = []
    if not spaces:
        st.info("Nenhum Genie Space encontrado neste workspace.")
    titles = {f'{s["title"]}  ·  {s["space_id"][:8]}': s for s in spaces}
    pick = st.selectbox("Selecione um espaço", list(titles)) if titles else None
    sel = titles.get(pick) if pick else None

    if sel and persona == "author":
        if st.button("Solicitar promoção →", type="primary"):
            with st.spinner("Pré-renderizando, rodando allowlist + grants e a revisão do agente…"):
                try:
                    ss.review = app_logic.review_space(sel["space_id"], PROFILE, user_token=USER_TOKEN)
                    ss.approved = False
                except Exception as e:  # noqa: BLE001 — surface friendly PT, never a traceback
                    st.error(f"Não foi possível revisar o espaço: {e}")
                    ss.review = None

    review = ss.review
    if review:
        st.subheader("Pipeline de promoção")
        # rebuild live so the approval/deploy rows advance after the Steward approves (S9)
        timeline = app_logic.build_timeline(
            not review["allowlist_violations"], review["gate"], review["eval"],
            approved=ss.approved, deployed=ss.approved)
        for t in timeline:
            icon = {"pass": "✅", "fail": "🔴", "running": "🟡", "pending": "•"}.get(t["status"], "•")
            st.write(f"{icon} {t['label']}")

        gate = review["gate"]
        (st.error if gate["conclusion"] == "failure" else st.success
         if gate["conclusion"] == "success" else st.warning)(gate["summary"])

        st.subheader("Achados do Genie Reviewer")
        for f in review["findings"]:
            sev = {"BLOCKER": "🔴", "SUGGESTION": "🟡", "STYLE": "⚪"}.get(f["severity"], "•")
            st.markdown(f"{sev} **{f['rule_id']}** — {f['message']}  \n_{f.get('citation', '')}_")
        if not review["findings"]:
            st.write("Nenhum achado — espaço limpo. ✅")
        st.caption(f"eval-run: {review['eval']['summary']}")

        # --- Steward approval (S10) ---------------------------------------------
        st.divider()
        approver = STEWARD if persona == "steward" else REQUESTER
        ok, reason = app_logic.can_approve(persona, REQUESTER, approver)
        if persona == "steward":
            st.subheader("Aprovação do Steward")
            if gate["conclusion"] == "failure":
                st.warning("A promoção está bloqueada por hallazgos BLOCKER — resolva (ex.: /genie-fix) antes de aprovar.")
            elif ok and st.button("✔ Aprovar promoção", type="primary"):
                ss.approved = True
            if ss.approved:
                st.success("Aprovado pelo Steward — o gate de produção seria liberado; o service principal faz o deploy.")
        else:
            st.info("Aguardando o Steward (Pedro) aprovar. Você é o solicitante — não pode aprovar a própria promoção (SoD).")

# --- New Genie Space wizard (S11) -----------------------------------------------
with tab_new:
    st.subheader("Novo Genie Space")
    st.caption("Cria um espaço no workspace de dev e abre o Genie nativo para você ajustar.")
    name = st.text_input("Nome", "Recebíveis Q&A")
    cat = st.text_input("Catálogo (dev)", f"dev_{app_logic.DOMAIN}")
    st.text_area("Instruções iniciais", "Responda perguntas sobre recebíveis usando as tabelas Diamond.")
    st.button("Criar e abrir no Genie →", disabled=(persona == "steward"),
              help="Chama a Genie create-space API e faz deep-link ao Genie nativo (D3).")
    st.caption("(Wizard fino — a autoria rica acontece no Genie nativo, por D3.)")
