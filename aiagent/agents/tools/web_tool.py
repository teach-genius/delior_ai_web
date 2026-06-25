from __future__ import annotations
import os
from dotenv import load_dotenv
from langchain_tavily import TavilySearch

load_dotenv()

tavily_tool = TavilySearch(
    name="recherche_web",
    max_results=3,
    tavily_api_key=os.getenv("TAVILY_API_KEY"),
    description="""
    Utile pour rechercher des informations actuelles sur le marché de l'emploi AU MAROC uniquement :
    - Les tendances RH et recrutement au Maroc
    - Les salaires et conditions du marché marocain
    - Les actualités sur un secteur ou un métier au Maroc
    Entrée : une question ou requête en français, toujours contextualisée au Maroc.
    IMPORTANT : toujours ajouter "Maroc" dans la requête envoyée à cet outil.
    """,
)

# ---------------------------------------------------------------------------
# Registre des outils
# ---------------------------------------------------------------------------
def get_web_tools() -> list:
    return [tavily_tool]