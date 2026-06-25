from __future__ import annotations
import logging
import re

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import Command

from .state import AgentState
from .memory import load_long_term_memory, save_long_term_memory
from .tools.db_tool           import get_db_tools, get_schema_info
from .tools.web_tool          import get_web_tools
from .tools.esco_tool         import get_esco_tools
from .tools.email_tool        import get_email_tools         
from .tools.email_folder_tool import get_email_folder_tools 
from .tools.notification_tool import get_notification_tools
from langchain_core.runnables import RunnableConfig

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
# PROMPTS
# ══════════════════════════════════════════════════════

DB_SYSTEM_PROMPT = """
## RÔLE
Tu es un expert SQL PostgreSQL senior. Tu interroges la base RH de Delior Group en lecture seule.
Tu réponds TOUJOURS en français, avec précision et concision.

## TABLE PRINCIPALE : candidat_candidatcv
Colonnes : candidat_id, nom_complet, titre_professionnel, ville, pays,
           telephone, email, domaine, secteur, niveau, contrat_souhaite,
           resume_profil, etat_analyse ('en_attente'|'en_cours'|'termine'),
           actif (boolean), date_importation, donnees_structurees (JSONB)

## RÈGLES SQL OBLIGATOIRES
1. Recherche nom     → nom_complet ILIKE '%Nom%'
2. Filtre actif      → actif = TRUE toujours
3. Listes            → ORDER BY date_importation DESC LIMIT 20
4. JSONB fallback    → donnees_structurees::text ILIKE '%mot%'
5. Jamais de ```sql ni de point-virgule final

## JOINTURE COMPÉTENCES
SELECT c.nom_complet, c.titre_professionnel
FROM candidat_candidatcv c
JOIN candidat_candidatcv_competences cc ON c.candidat_id = cc.candidatcv_id
JOIN candidat_competence comp ON cc.competence_id = comp.id
WHERE c.actif = TRUE AND comp.nom ILIKE '%python%'

## MAPPAGE STATUTS
- "en attente" → etat_analyse = 'en_attente'
- "en cours"   → etat_analyse = 'en_cours'
- "terminé"    → etat_analyse = 'termine'
"""

WEB_SYSTEM_PROMPT = """
## RÔLE
Tu es un expert du marché de l'emploi au Maroc pour Delior Group.
Tu réponds TOUJOURS en français, de manière concise (3-5 lignes max).

## TON OUTIL
- recherche_web : toujours ajouter "Maroc" dans chaque requête

## RÈGLES
- Sources marocaines uniquement (hcp.ma, anapec.org, rekrute.com)
- Si aucune donnée fiable : orienter vers hcp.ma ou anapec.org
- Ne jamais utiliser des données françaises comme substitut
- Ne jamais inventer un chiffre ou une tendance

## FORMAT
- Factuel et direct
- Citer la source si disponible
- Emojis courts si utile (📊 ⚠️ 💡)
"""

ESCO_SYSTEM_PROMPT = """
## RÔLE
Tu es un expert en référentiels métiers et compétences pour Delior Group.
Tu utilises le référentiel européen ESCO pour répondre avec précision.
Tu réponds TOUJOURS en français, de manière concise et structurée.

## TON OUTIL
- esco_tool : appelle cet outil avant toute réponse sur un métier ou compétence

## RÈGLES
- Toujours appeler esco_tool avant de répondre
- Adapter les compétences au contexte Delior (boulangerie, restauration, distribution)
- Si métier absent d'ESCO : proposer le plus proche

## FORMAT
- Compétences clés en liste (max 6)
- Note de contexte Delior si pertinent
"""

RH_SYSTEM_PROMPT = """
Tu es Delior RH Assistant, expert en recrutement pour Delior Group (Paul, DELI'S, Chopain — Maroc).
Tu réponds toujours en français, de manière claire et concise (3-6 lignes max).
Tu gères les questions générales RH qui ne nécessitent pas de base de données,
de recherche web ou de référentiel métier.

## OUTILS EMAIL — ENVOI
- envoyer_convocation   : convoquer un candidat à un entretien
- envoyer_refus         : informer un candidat d'un refus
- envoyer_relance       : relancer un candidat sans réponse
- envoyer_email_libre   : envoyer un message personnalisé

## OUTILS EMAIL — GESTION BOÎTE
- lister_dossiers_email    : voir tous les dossiers Gmail
- creer_dossier_email      : créer un nouveau dossier
- lister_emails_dossier    : voir les emails d'un dossier (avec filtres)
- deplacer_emails          : déplacer des emails d'un dossier à un autre
- trier_boite_reception    : trier automatiquement la boîte selon une règle

## OUTILS NOTIFICATIONS — CENTRE RH
- creer_notification           : créer une notification dans le centre RH
  → tags disponibles : Infos | Succes | Error | Warning
- lire_notifications           : lire les notifications (filtrable par tag ou non lues)
- marquer_notifications_lues   : marquer une ou toutes les notifications comme lues

## RÈGLES EMAIL
- Toujours demander confirmation avant d'envoyer un email
- Toujours demander confirmation avant de déplacer des emails en masse
- Ne jamais supprimer d'emails — uniquement déplacer
- Si l'email du candidat n'est pas fourni, le demander au RH
- Confirmer chaque action avec un résumé clair

## RÈGLES NOTIFICATIONS
- Créer automatiquement une notification après chaque action importante :
  ✅ Email envoyé → tag Succes
  ❌ Échec d'envoi → tag Error
  📋 Action RH réalisée → tag Infos
  ⚠️ Anomalie détectée → tag Warning
- Ne jamais supprimer une notification

## RÈGLES GÉNÉRALES
- Réponses courtes et directes
- Valeurs Delior : équité, diversité, objectivité
- Si hors périmètre RH : rediriger poliment
- Emojis courts si utile (✅ ⚠️ 💡 📧 📁 🔔)
"""


# ══════════════════════════════════════════════════════
# HELPER : correction ordre des messages pour Mistral
# ══════════════════════════════════════════════════════

def _trim_trailing_ai(messages: list) -> list:
    """
    Mistral exige que le dernier message soit de rôle 'user' ou 'tool'.
    Cette fonction supprime les AIMessage en fin de liste avant chaque appel API.
    """
    messages = list(messages)
    while messages and isinstance(messages[-1], AIMessage):
        messages.pop()
    return messages


# ══════════════════════════════════════════════════════
# FACTORY GÉNÉRIQUE
# ══════════════════════════════════════════════════════

def _make_subgraph(llm, tools: list, system_prompt: str):
    """Sous-graphe générique LLM ↔ ToolNode."""
    llm_with_tools = llm.bind_tools(tools)

    async def call_llm(
        state: AgentState,
        config: RunnableConfig | None = None,
    ) -> AgentState:
        system_msg = SystemMessage(content=system_prompt)
        try:
            messages = _trim_trailing_ai(state["messages"])
            response = await llm_with_tools.ainvoke(
                [system_msg] + messages,
                config=config,
            )
            return {"messages": [response]}
        except Exception as e:
            if "429" in str(e):
                return {"messages": [AIMessage(content="Service surchargé, réessayez.")]}
            logger.error(f"[subgraph/llm] {type(e).__name__}: {e}")
            return {"messages": [AIMessage(content=f"Erreur : {e}")]}

    sg = StateGraph(AgentState)
    sg.add_node("llm",   call_llm)
    sg.add_node("tools", ToolNode(tools=tools))
    sg.set_entry_point("llm")
    sg.add_conditional_edges("llm", tools_condition)
    sg.add_edge("tools", "llm")
    return sg.compile()


# ══════════════════════════════════════════════════════
# DB AGENT
# ══════════════════════════════════════════════════════

def make_db_agent(llm_db, extra_tools: list | None = None):
    tools          = get_db_tools() + (extra_tools or [])
    llm_with_tools = llm_db.bind_tools(tools)

    async def call_llm(
        state: AgentState,
        config: RunnableConfig | None = None,
    ) -> AgentState:
        schema     = get_schema_info()
        system_msg = SystemMessage(
            content=DB_SYSTEM_PROMPT + f"\n## SCHEMA RÉEL\n{schema}"
        )
        try:
            messages = _trim_trailing_ai(state["messages"])
            response = await llm_with_tools.ainvoke(
                [system_msg] + messages,
                config=config,
            )
            return {"messages": [response]}
        except Exception as e:
            if "429" in str(e):
                return {"messages": [AIMessage(content="Service surchargé, réessayez.")]}
            logger.error(f"[db_agent/llm] {type(e).__name__}: {e}")
            return {"messages": [AIMessage(content=f"Erreur : {e}")]}

    sg = StateGraph(AgentState)
    sg.add_node("llm",   call_llm)
    sg.add_node("tools", ToolNode(tools=tools))
    sg.set_entry_point("llm")
    sg.add_conditional_edges("llm", tools_condition)
    sg.add_edge("tools", "llm")
    _compiled = sg.compile()

    async def db_agent_node(state: AgentState) -> Command:
        result = await _compiled.ainvoke(state)
        return Command(goto="supervisor", update={"messages": result["messages"]})

    return db_agent_node


# ══════════════════════════════════════════════════════
# WEB AGENT
# ══════════════════════════════════════════════════════

def make_web_agent(llm, extra_tools: list | None = None):
    tools     = get_web_tools() + (extra_tools or [])
    _compiled = _make_subgraph(llm, tools, WEB_SYSTEM_PROMPT)

    async def web_agent_node(state: AgentState) -> Command:
        result = await _compiled.ainvoke(state)
        return Command(goto="supervisor", update={"messages": result["messages"]})

    return web_agent_node


# ══════════════════════════════════════════════════════
# ESCO AGENT
# ══════════════════════════════════════════════════════

def make_esco_agent(llm, extra_tools: list | None = None):
    tools     = get_esco_tools() + (extra_tools or [])
    _compiled = _make_subgraph(llm, tools, ESCO_SYSTEM_PROMPT)

    async def esco_agent_node(state: AgentState) -> Command:
        result = await _compiled.ainvoke(state)
        return Command(goto="supervisor", update={"messages": result["messages"]})

    return esco_agent_node


# ══════════════════════════════════════════════════════
# RH AGENT  (envoi email + gestion dossiers + notification)
# ══════════════════════════════════════════════════════

def make_rh_agent(llm, store, extra_tools: list | None = None):
    # Tous les outils email regroupés
    tools     = get_email_tools() + get_notification_tools() + get_email_folder_tools() + (extra_tools or [])
    llm_bound = llm.bind_tools(tools)
    tool_node = ToolNode(tools=tools)

    async def call_llm(
        state: AgentState,
        config: RunnableConfig | None = None,
    ) -> AgentState:
        user_id    = state.get("user_id", "anonymous")
        lt_memory  = await load_long_term_memory(store, user_id)
        system_msg = SystemMessage(content=RH_SYSTEM_PROMPT + lt_memory)

        try:
            messages = _trim_trailing_ai(state["messages"])
            response = await llm_bound.ainvoke(
                [system_msg] + messages,
                config=config,
            )

            if isinstance(response.content, str) and "[MÉMORISER:" in response.content:
                matches = re.findall(r"\[MÉMORISER:\s*(.+?)\]", response.content)
                for fact in matches:
                    await save_long_term_memory(store, user_id, fact.strip())
                clean    = re.sub(r"\[MÉMORISER:.*?\]", "", response.content).strip()
                response = response.model_copy(update={"content": clean})

            return {"messages": [response]}

        except Exception as e:
            if "429" in str(e):
                return {"messages": [AIMessage(content="Service surchargé, réessayez.")]}
            logger.error(f"[rh_agent/llm] {type(e).__name__}: {e}")
            return {"messages": [AIMessage(content=f"Erreur : {e}")]}

    sg = StateGraph(AgentState)
    sg.add_node("llm",   call_llm)
    sg.add_node("tools", tool_node)
    sg.set_entry_point("llm")
    sg.add_conditional_edges("llm", tools_condition)
    sg.add_edge("tools", "llm")
    _compiled = sg.compile()

    async def rh_agent_node(state: AgentState) -> Command:
        result = await _compiled.ainvoke(state)
        return Command(goto="supervisor", update={"messages": result["messages"]})

    return rh_agent_node