from __future__ import annotations
import requests
from langchain_core.tools import tool


@tool
def esco_tool(query: str) -> str:
    """
    Recherche des informations officielles sur un métier ou une compétence
    dans le référentiel européen ESCO :
    - Compétences requises pour un métier
    - Formations et diplômes associés
    - Définition officielle d'un poste
    Entrée : un titre de métier ou une compétence (ex: 'boulanger', 'chef de projet').
    """
    try:
        response = requests.get(
            "https://ec.europa.eu/esco/api/search",
            params={
                "text":     query,
                "language": "fr",
                "type":     "occupation",
                "limit":    3,
                "full":     "true",
            },
            timeout=10,
        )
        response.raise_for_status()
        results = response.json().get("_embedded", {}).get("results", [])

        if not results:
            return f"Aucun métier trouvé pour '{query}' dans le référentiel ESCO."

        output = []
        for item in results:
            title       = item.get("title", "N/A")
            description = item.get("description", {}).get("fr", {}).get("literal", "")
            uri         = item.get("uri", "")

            skills_text = "Non disponible"
            if uri:
                skills_resp = requests.get(
                    "https://ec.europa.eu/esco/api/resource/occupation",
                    params={"uri": uri, "language": "fr"},
                    timeout=10,
                )
                if skills_resp.ok:
                    essential   = skills_resp.json().get("_links", {}).get("hasEssentialSkill", [])
                    skill_names = [s.get("title", "") for s in essential[:6]]
                    skills_text = ", ".join(skill_names) if skill_names else "Non disponible"

            output.append(
                f"Métier : {title}\n"
                f"Description : {description[:300] if description else 'N/A'}\n"
                f"Compétences clés : {skills_text}\n"
            )

        return "\n---\n".join(output)

    except Exception as e:
        return f"Erreur ESCO : {e}"

# ---------------------------------------------------------------------------
# Registre des outils
# ---------------------------------------------------------------------------
def get_esco_tools() -> list:
    return [esco_tool]