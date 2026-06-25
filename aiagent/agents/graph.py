from __future__ import annotations
import logging
import markdown

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START

from .state import AgentState
from .supervisor import make_supervisor
from .sub_agents import make_db_agent, make_web_agent, make_esco_agent, make_rh_agent
from .llms import get_llm, get_llm_DB
from aiagent.auths.agent_memory_auth import get_checkpointer, get_store

logger = logging.getLogger(__name__)


async def _build_graph():
    """
    Construit le graphe entièrement à chaque requête.
    LLMs, Redis checkpointer et store sont tous recréés dans le loop courant.
    Nécessaire car Django crée un nouveau event loop par requête en développement.
    """
    checkpointer = await get_checkpointer()
    store        = await get_store()
    llm          = get_llm()      # nouvelle instance liée au loop courant
    llm_db       = get_llm_DB()   # idem

    builder = StateGraph(AgentState)
    builder.add_node("supervisor", make_supervisor(llm))
    builder.add_node("db_agent",   make_db_agent(llm_db))
    builder.add_node("web_agent",  make_web_agent(llm))
    builder.add_node("esco_agent", make_esco_agent(llm))
    builder.add_node("rh_agent",   make_rh_agent(llm, store))

    builder.add_edge(START, "supervisor")

    compiled = builder.compile(
        checkpointer=checkpointer,
        store=store,
    )
    return compiled, store


def _format_response(text: str) -> str:
    return markdown.markdown(text or "", extensions=["nl2br"])


async def ask_assistant(user_context: dict, user_message: str) -> str:
    try:
        app, _ = await _build_graph()

        config = {
            "configurable": {
                "thread_id": user_context['id'],
                "user_id":   user_context['id'],
            }
        }

        result = await app.ainvoke(
            {"messages": [HumanMessage(content=user_message)], "user_id": user_context['id']},
            config=config,
        )

        for msg in reversed(result["messages"]):
            if hasattr(msg, "content") and msg.content:
                return _format_response(msg.content)

        return "Réponse vide."

    except Exception as e:
        logger.error(f"[ask_assistant] {type(e).__name__}: {e}")
        return "Une erreur technique est survenue. Merci de réessayer."


def reset_graph() -> None:
    """Conservé pour compatibilité, sans effet en mode sans singleton."""
    logger.info("[graph] reset_graph appelé (sans effet)")