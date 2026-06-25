from __future__ import annotations
import logging
import os
import re

import sqlparse
from langchain_community.utilities import SQLDatabase
from langchain_core.tools import tool
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_db = None


# ══════════════════════════════════════════════════════
# DB UTILS
# ══════════════════════════════════════════════════════

def _sanitize_sql(query: str) -> str:
    query = query.strip()
    query = re.sub(r"^```sql\s*", "", query, flags=re.IGNORECASE)
    query = re.sub(r"^```\s*",    "", query, flags=re.IGNORECASE)
    query = re.sub(r"\s*```$",    "", query)
    if query.endswith(";"):
        query = query[:-1].strip()
    return query.strip()


def _validate_sql(query: str) -> str | None:
    stripped = query.strip().rstrip(";").strip()

    if re.search(r"(=|!=|<>|>=|<=|<|>)\s*$", stripped, re.IGNORECASE):
        if "etat_analyse" in stripped:
            return (
                "etat_analyse sans valeur. "
                "Valeurs autorisées : 'en_attente' | 'en_cours' | 'termine'. "
                "Réécrire la requête complète."
            )
        return "Comparaison sans valeur à droite. Compléter la condition."

    if re.search(r"\b(AND|OR|WHERE|HAVING)\s*$", stripped, re.IGNORECASE):
        return "Clause logique sans condition associée. Compléter la requête."

    parsed = sqlparse.parse(stripped)
    if not parsed or not parsed[0].tokens:
        return "Requête vide ou non parseable."

    return None


def get_db() -> SQLDatabase:
    global _db
    if _db is None:
        db = SQLDatabase.from_uri(
            os.getenv("Agent_URL_POSTGRESQL"),
            include_tables=None,
            sample_rows_in_table_info=3,
        )
        original_run = db.run

        def safe_run(command: str, **kwargs):
            cleaned = _sanitize_sql(command)
            if cleaned != command:
                logger.warning(f"[safe_run] nettoyée : {cleaned}")
            return original_run(cleaned, **kwargs)

        db.run = safe_run
        _db = db
    return _db


def get_schema_info() -> str:
    return get_db().get_table_info(table_names=["candidat_candidatcv"])


# ══════════════════════════════════════════════════════
# TOOL
# ══════════════════════════════════════════════════════

@tool
def query_database_tool(query: str) -> str:
    """
    Utile pour interroger la base de données candidats de Delior Group.
    Permet d'obtenir des informations sur les candidats, leurs compétences,
    leurs expériences, et leur adéquation avec les postes à pourvoir.
    Entrée : une question en français sur les candidats ou le matching.
    Exemple : "Quels sont les candidats avec des compétences en Python à Casablanca ?"
    Exécute une requête SQL en lecture seule sur la base RH de Delior Group.
    Passe uniquement la requête SQL brute, sans point-virgule ni bloc markdown.
    """
    try:
        db = get_db()

        if _validate_sql(query) is None:
            result = db.run(query)
            return str(result) if result else "Aucun résultat trouvé."

        cleaned = _sanitize_sql(query)
        error   = _validate_sql(cleaned)

        if error is None:
            result = db.run(cleaned)
            return str(result) if result else "Aucun résultat trouvé."

        logger.warning(f"[query_database_tool] Invalide : {cleaned!r}")
        return (
            f"ERREUR SQL : {error}\n"
            f"Requête reçue : {cleaned!r}\n"
            f"ACTION REQUISE : Réécrire une requête SQL COMPLÈTE et VALIDE."
        )

    except Exception as e:
        logger.error(f"[query_database_tool] {e}")
        return f"Erreur lors de l'exécution : {e}"

# ---------------------------------------------------------------------------
# Registre des outils
# ---------------------------------------------------------------------------
def get_db_tools() -> list:
    return [query_database_tool]