from .utils import max_retries, get_next_llm
import re, json, logging
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)

# ─── Prompt système ───────────────────────────────────────────────
JOB_OFFER_PROMPT = """
RÔLE :
Tu es un expert en recrutement spécialisé dans la rédaction d'offres d'emploi.

CONTEXTE :
Tu reçois une demande utilisateur décrivant un besoin de recrutement.
Tu dois générer une offre d'emploi complète, professionnelle et exploitable pour un ATS.

RÈGLES :
- JSON strict uniquement
- Ne rien inventer d'irréaliste
- Champs vides = "" ou []
- Si ambigu → ""
- "projets_valorises" et "certifications_valorisees" ne doivent JAMAIS être [] si la demande contient des éléments correspondants
- Inclure TOUTES les compétences déduites du contexte métier

NORMALISATION :
- localisation = nom de ville uniquement (ex: "Casablanca")
- type_contrat ∈ [CDI, CDD, Freelance, Stage, Alternance] ou ""
- niveau_experience ∈ [junior, intermediaire, senior, expert] ou ""

CLASSIFICATION :
- domaine ∈ [
Production & Artisanat,
Qualité & Hygiène,
Opérations & Vente,
Logistique & Supply Chain,
Maintenance & Technique,
Fonctions Support,
Développement Logiciel,
Data & Intelligence Artificielle,
Cybersécurité,
Cloud & DevOps,
Infrastructure & Réseau,
Gestion de Projet,
Recherche & Innovation
]
- secteur_activite ∈ [
Retail & Restauration,
Industrie & Agroalimentaire,
Services Centraux,
Technologie & Numérique,
Télécommunications,
Finance & Assurance,
Santé & Pharmaceutique,
Éducation & Formation,
Transport & Mobilité,
Énergie & Utilities,
Conseil & Services,
Administration Publique
]

FORMAT JSON :
{{
  "poste": {{
    "titre_professionnel": "",
    "domaine": "",
    "localisation": "",
    "type_contrat": ""
  }},
  "secteur_activite": "",
  "niveau_experience": "",
  "profil_recherche": "",
  "formation_requise": [
    {{ "diplome": "", "domaine_etudes": "", "niveau": "" }}
  ],
  "experience_requise": [
    {{
      "poste": "",
      "annees_experience": "",
      "missions_attendues": []
    }}
  ],
  "competences_requises": {{
    "savoir_faire": [],
    "savoir_etre": [],
    "langues": [
      {{ "langue": "", "niveau": "" }}
    ]
  }},
  "projets_valorises": [
    {{ "type": "", "description": "", "technologies": [] }}
  ],
  "certifications_valorisees": [
    {{ "nom": "", "organisme": "" }}
  ]
}}
"""

# ─── Helpers ──────────────────────────────────────────────────────
def _safe_str(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if value.lower() in ["null", "none", "nan", "undefined"]:
        return ""
    return value

def _safe_list(value) -> list:
    if not isinstance(value, list):
        return []
    return value

def _parse_json(resp: str) -> dict:
    """Nettoie et parse la réponse LLM en dict JSON."""
    clean = resp.strip()

    # 1. Supprimer balises <think> (Qwen)
    clean = re.sub(r'<think>.*?</think>', '', clean, flags=re.DOTALL).strip()

    # 2. Supprimer fences markdown ```json ... ``` ou ``` ... ```
    clean = re.sub(r'```(?:json)?', '', clean).strip()
    clean = clean.replace('```', '').strip()

    # 3. Extraire le premier objet JSON { ... }
    match = re.search(r'\{.*\}', clean, re.DOTALL)
    if not match:
        raise ValueError("Aucun bloc JSON trouvé dans la réponse")

    return json.loads(match.group(0))


def _safe_job_offer(data: dict) -> dict:
    """Normalise et valide le dict extrait par le LLM."""

    DOMAINES_VALIDES = {
        "Production & Artisanat", "Qualité & Hygiène", "Opérations & Vente",
        "Logistique & Supply Chain", "Maintenance & Technique", "Fonctions Support",
        "Développement Logiciel", "Data & Intelligence Artificielle", "Cybersécurité",
        "Cloud & DevOps", "Infrastructure & Réseau", "Gestion de Projet",
        "Recherche & Innovation"
    }
    SECTEURS_VALIDES = {
        "Retail & Restauration", "Industrie & Agroalimentaire", "Services Centraux",
        "Technologie & Numérique", "Télécommunications", "Finance & Assurance",
        "Santé & Pharmaceutique", "Éducation & Formation", "Transport & Mobilité",
        "Énergie & Utilities", "Conseil & Services", "Administration Publique"
    }
    NIVEAUX_VALIDES  = {"junior", "intermediaire", "senior", "expert"}
    CONTRATS_VALIDES = {"CDI", "CDD", "Freelance", "Stage", "Alternance"}

    poste       = data.get("poste", {}) if isinstance(data.get("poste"), dict) else {}
    competences = data.get("competences_requises", {}) if isinstance(data.get("competences_requises"), dict) else {}

    # ── Poste ─────────────────────────────────────────────────────
    domaine  = _safe_str(poste.get("domaine"))
    contrat  = _safe_str(poste.get("type_contrat"))

    # ── Expérience requise ─────────────────────────────────────────
    experience = [
        {
            "poste":              _safe_str(e.get("poste")),
            "annees_experience":  _safe_str(e.get("annees_experience")),
            "missions_attendues": _safe_list(e.get("missions_attendues")),
        }
        for e in _safe_list(data.get("experience_requise"))
        if isinstance(e, dict)
    ]

    # ── Formation requise ──────────────────────────────────────────
    formation = [
        {
            "diplome":        _safe_str(f.get("diplome")),
            "domaine_etudes": _safe_str(f.get("domaine_etudes")),
            "niveau":         _safe_str(f.get("niveau")),
        }
        for f in _safe_list(data.get("formation_requise"))
        if isinstance(f, dict)
    ]

    # ── Langues ────────────────────────────────────────────────────
    langues = [
        {
            "langue": _safe_str(l.get("langue")),
            "niveau": _safe_str(l.get("niveau")),
        }
        for l in _safe_list(competences.get("langues"))
        if isinstance(l, dict)
    ]

    # ── Projets valorisés ──────────────────────────────────────────
    projets = [
        {
            "type":         _safe_str(p.get("type")),
            "description":  _safe_str(p.get("description")),
            "technologies": _safe_list(p.get("technologies")),
        }
        for p in _safe_list(data.get("projets_valorises"))
        if isinstance(p, dict)
    ]

    # ── Certifications valorisées ──────────────────────────────────
    certifications = [
        {
            "nom":       _safe_str(c.get("nom")),
            "organisme": _safe_str(c.get("organisme")),
        }
        for c in _safe_list(data.get("certifications_valorisees"))
        if isinstance(c, dict)
    ]

    # ── Niveau expérience ──────────────────────────────────────────
    niveau = _safe_str(data.get("niveau_experience"))
    secteur = _safe_str(data.get("secteur_activite"))

    return {
        "poste": {
            "titre_professionnel": _safe_str(poste.get("titre_professionnel")),
            "domaine":             domaine if domaine in DOMAINES_VALIDES else "",
            "localisation":        _safe_str(poste.get("localisation")),
            "type_contrat":        contrat if contrat in CONTRATS_VALIDES else "",
        },
        "secteur_activite":  secteur if secteur in SECTEURS_VALIDES else "",
        "niveau_experience": niveau  if niveau  in NIVEAUX_VALIDES  else "",
        "profil_recherche":  _safe_str(data.get("profil_recherche")),
        "formation_requise": formation,
        "experience_requise": experience,
        "competences_requises": {
            "savoir_faire": _safe_list(competences.get("savoir_faire")),
            "savoir_etre":  _safe_list(competences.get("savoir_etre")),
            "langues":      langues,
        },
        "projets_valorises":       projets,
        "certifications_valorisees": certifications,
    }


_FALLBACK = {
    "poste": {
        "titre_professionnel": "",
        "domaine":             "",
        "localisation":        "",
        "type_contrat":        "",
    },
    "secteur_activite":   "",
    "niveau_experience":  "",
    "profil_recherche":   "",
    "formation_requise":  [],
    "experience_requise": [],
    "competences_requises": {
        "savoir_faire": [],
        "savoir_etre":  [],
        "langues":      [],
    },
    "projets_valorises":         [],
    "certifications_valorisees": [],
}


# ─── Fonction principale ──────────────────────────────────────────

def build_job_offer_from_query(query: str) -> dict:
    if not query or not query.strip():
        raise ValueError("query vide.")

    messages = [
        SystemMessage(content=JOB_OFFER_PROMPT),
        HumanMessage(content=query),
    ]

    last_error = None

    for attempt in range(max_retries):
        llm = get_next_llm()
        try:
            resp = llm.invoke(messages).content
            data = _parse_json(resp)

            if not isinstance(data, dict):
                raise ValueError("Réponse JSON n'est pas un objet")

            result = _safe_job_offer(data)
            logger.info(f"[build_job_offer_from_query] Succès tentative #{attempt + 1}")
            return result

        except Exception as e:
            last_error = e
            logger.warning(f"[build_job_offer_from_query] Tentative #{attempt + 1} échouée : {e}")
            continue

    logger.error(f"[build_job_offer_from_query] Toutes les tentatives ont échoué : {last_error}")
    return _FALLBACK.copy()