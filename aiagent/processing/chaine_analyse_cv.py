from .utils import max_retries,get_next_llm
import re, json
from langchain_core.messages import SystemMessage, HumanMessage

ANALYSE_PROMPT = """
Tu es un agent senior RH avec 15 ans d'expérience, expert en analyse CV et matching poste–profil.
Tu appliques méthodes professionnelles : compétences, adéquation poste–profil, lecture chronologique,
incohérences parcours, évaluation seniorité.
Mission : analyser CV vs poste, évaluer cohérence, identifier forces/écarts/risques, générer rapport structuré.
Règles : pas de scores, pas d'estimations, pas d'invention, info absente = null, conclusions justifiées.
Éthique RH : aucune discrimination, aucune supposition non fondée.

FORMAT DE SORTIE (JSON STRICT) :
{{
  "candidat": {{
    "nom_complet": "",
    "titre_professionnel": "",
    "ville": "",
    "email": "",
    "telephone": ""
  }},
  "poste": {{
    "titre_professionnel": "",
    "localisation": "",
    "type_contrat": ""
  }},
  "detail": {{
    "formation": {{"analyse": ""}},
    "experience": {{"analyse": ""}},
    "competences_techniques": {{
      "competences_match": [],
      "competences_manquantes": [],
      "analyse": ""
    }},
    "competences_comportementales": {{"analyse": ""}},
    "langues": {{"analyse": ""}},
    "projets_certifications": {{"analyse": ""}}
  }},
  "points_forts": [],
  "points_a_ameliorer": [],
  "recommandation_finale": {{
    "decision": "",
    "niveau_priorite": "",
    "justification": ""
  }}
}}
"""

def extract_analyse_cv(cv_text: str, poste_text: str, max_retries=max_retries) -> dict:
    def safe_str(value):
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        value = value.strip()
        if value.lower() in ["null", "none", "nan", "undefined"]:
            return ""
        return value

    def safe_list(value):
        if not isinstance(value, list):
            return []
        return value

    def safe_analyse(data):
        if not isinstance(data, dict):
            data = {}

        # --- candidat ---
        candidat = data.get("candidat", {})
        if not isinstance(candidat, dict):
            candidat = {}

        # --- poste ---
        poste = data.get("poste", {})
        if not isinstance(poste, dict):
            poste = {}

        # --- detail ---
        detail = data.get("detail", {})
        if not isinstance(detail, dict):
            detail = {}

        def safe_section(key):
            s = detail.get(key, {})
            if not isinstance(s, dict):
                return {"analyse": ""}
            return {"analyse": safe_str(s.get("analyse"))}

        comp_tech = detail.get("competences_techniques", {})
        if not isinstance(comp_tech, dict):
            comp_tech = {}

        # --- recommandation ---
        reco = data.get("recommandation_finale", {})
        if not isinstance(reco, dict):
            reco = {}

        return {
            "candidat": {
                "nom_complet":         safe_str(candidat.get("nom_complet")),
                "titre_professionnel": safe_str(candidat.get("titre_professionnel")),
                "ville":               safe_str(candidat.get("ville")),
                "email":               safe_str(candidat.get("email")),
                "telephone":           safe_str(candidat.get("telephone")),
            },
            "poste": {
                "titre_professionnel": safe_str(poste.get("titre_professionnel")),
                "localisation":        safe_str(poste.get("localisation")),
                "type_contrat":        safe_str(poste.get("type_contrat")),
            },
            "detail": {
                "formation":                  safe_section("formation"),
                "experience":                 safe_section("experience"),
                "competences_techniques": {
                    "competences_match":      safe_list(comp_tech.get("competences_match")),
                    "competences_manquantes": safe_list(comp_tech.get("competences_manquantes")),
                    "analyse":                safe_str(comp_tech.get("analyse")),
                },
                "competences_comportementales": safe_section("competences_comportementales"),
                "langues":                      safe_section("langues"),
                "projets_certifications":       safe_section("projets_certifications"),
            },
            "points_forts":        safe_list(data.get("points_forts")),
            "points_a_ameliorer":  safe_list(data.get("points_a_ameliorer")),
            "recommandation_finale": {
                "decision":        safe_str(reco.get("decision")),
                "niveau_priorite": safe_str(reco.get("niveau_priorite")),
                "justification":   safe_str(reco.get("justification")),
            },
        }

    # ─── Appel LLM avec rotation de clés ─────────────────────────
    user_content = f"""CV DU CANDIDAT :
    {cv_text}
    ---
    DESCRIPTION DU POSTE :
    {poste_text}
    """
    messages = [
        SystemMessage(content=ANALYSE_PROMPT),
        HumanMessage(content=user_content)
    ]

    last_error = None

    for attempt in range(max_retries):
        llm = get_next_llm()
        try:
            resp = llm.invoke(messages).content

            clean = resp.strip()
            # Supprimer balises <think>
            clean = re.sub(r'<think>.*?</think>', '', clean, flags=re.DOTALL).strip()
            # Supprimer fences markdown
            clean = re.sub(r'```(?:json)?', '', clean).strip()
            # Extraire le JSON
            match = re.search(r'\{.*\}', clean, re.DOTALL)
            if not match:
                raise ValueError("Aucun JSON trouvé")
            clean = match.group(0)

            data = json.loads(clean)
            if not isinstance(data, dict):
                raise ValueError("format invalide")

            print(f"[OK] Tentative #{attempt + 1} réussie")
            return safe_analyse(data)

        except Exception as e:
            last_error = e
            print(f"[RETRY] Tentative #{attempt + 1} échouée : {e}")
            continue

    # ─── Fallback total ───────────────────────────────────────────
    print(f"[FAIL] Toutes les clés ont échoué : {last_error}")
    return {
        "candidat": {
            "nom_complet": "", "titre_professionnel": "",
            "ville": "", "email": "", "telephone": ""
        },
        "poste": {
            "titre_professionnel": "", "localisation": "", "type_contrat": ""
        },
        "detail": {
            "formation":                    {"analyse": ""},
            "experience":                   {"analyse": ""},
            "competences_techniques": {
                "competences_match":        [],
                "competences_manquantes":   [],
                "analyse":                  ""
            },
            "competences_comportementales": {"analyse": ""},
            "langues":                      {"analyse": ""},
            "projets_certifications":       {"analyse": ""}
        },
        "points_forts":       [],
        "points_a_ameliorer": [],
        "recommandation_finale": {
            "decision": "", "niveau_priorite": "", "justification": ""
        }
    }