from __future__ import annotations
import logging
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import Command
from langgraph.graph import END

from .state import AgentState

logger = logging.getLogger(__name__)

SUPERVISOR_SYSTEM = """Tu es le superviseur de l'assistant RH Delior Group.
Tu dois router chaque demande vers le bon agent spécialisé.

AGENTS DISPONIBLES :
- db_agent   : candidats en base (recherche, comptage, matching, profils, CV)
- web_agent  : marché emploi Maroc, salaires, tendances RH marocaines
- esco_agent : compétences officielles d'un métier, référentiel ESCO
- rh_agent   : questions RH générales, conseils recrutement, culture Delior
- FINISH     : réponse finale déjà complète dans les messages

RÈGLES DE ROUTING :
- "candidat", "profil", "base", "trouve", "cherche", "liste", "combien", "CV" → db_agent
- "salaire", "marché", "tendance", "Maroc", "recrutement au Maroc"            → web_agent
- "compétence", "métier", "formation", "ESCO", "diplôme"                      → esco_agent
- Question RH générale, conseil, culture Delior, salutation                   → rh_agent
- La dernière réponse est complète et satisfaisante                            → FINISH

Réponds UNIQUEMENT avec un seul mot : db_agent | web_agent | esco_agent | rh_agent | FINISH
Aucune explication. Aucun autre mot."""

VALID_DESTINATIONS = {"db_agent", "web_agent", "esco_agent", "rh_agent"}


def _last_human_message(messages: list) -> list:
    last_human_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            last_human_idx = i
            break

    if last_human_idx is None:
        return messages  

    relevant = messages[last_human_idx:]

    while relevant and isinstance(relevant[-1], AIMessage):
        relevant = relevant[:-1]

    return relevant if relevant else messages


def make_supervisor(llm):
    async def supervisor_node(
        state: AgentState,
    ) -> Command[Literal["db_agent", "web_agent", "esco_agent", "rh_agent", "__end__"]]:

        all_messages = state["messages"]

        if all_messages and isinstance(all_messages[-1], AIMessage):
            last_ai = all_messages[-1]
            if last_ai.content and not getattr(last_ai, "tool_calls", None):
                logger.debug("[supervisor] Dernier message est AIMessage avec contenu → FINISH direct")
                return Command(goto=END)

        trimmed  = _last_human_message(all_messages)
        messages = [SystemMessage(content=SUPERVISOR_SYSTEM)] + trimmed

        response    = await llm.ainvoke(messages)
        destination = response.content.strip().lower()

        logger.debug(f"[supervisor] → {destination!r}")

        if destination == "finish":
            return Command(goto=END)

        if destination not in VALID_DESTINATIONS:
            logger.warning(f"[supervisor] Destination inconnue {destination!r}, fallback rh_agent")
            destination = "rh_agent"

        return Command(
            goto=destination,
            update={"next_agent": destination},
        )

    return supervisor_node