from .utils import max_retries, get_next_llm
import re, json, logging
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)

CV_COMPLET_PROMPT = """
RÔLE :
Tu es un expert RH spécialisé en extraction de CV.

CONTEXTE :
Tu reçois un CV long (jusqu'à 3 pages ou plus) non structuré.
Tu dois le transformer en JSON exploitable pour un ATS.

RÈGLES :
- JSON strict uniquement
- Ne rien inventer
- Champs vides = "" ou []
- Si ambigu → ""
- "projets" et "certifications" ne doivent JAMAIS être [] si le CV contient des éléments correspondants
- Inclure TOUS les projets trouvés : académiques, personnels, professionnels
- Inclure TOUTES les certifications : officielles, en ligne, bootcamps, licences techniques

NORMALISATION :
- ville = nom de ville uniquement (ex: "Casablanca")
  - corriger casse, abréviations, doublons
- contrat_souhaite ∈ [CDI, CDD, Freelance, Stage, Alternance] ou ""
- poste_souhaite = poste recherché ou déduit du titre (sinon "")

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
- secteur ∈ [
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
- niveau ∈ [junior, intermediaire, senior, expert]

LOGIQUE :
- classification basée sur expérience dominante (récence + durée + fréquence)
- poste_souhaite priorise objectif ou dernier poste cohérent
- Les projets DOIVENT inclure :
  - projets académiques
  - projets personnels
  - projets professionnels mentionnés comme réalisations
- Les certifications DOIVENT inclure :
  - certifications officielles
  - formations en ligne (Coursera, Udemy, etc.)
  - bootcamps
  - licences techniques

FORMAT JSON :
{{
  "identite": {{
    "nom_complet": "",
    "titre_professionnel": "",
    "contact": {{
      "ville": "",
      "pays": "",
      "telephone": "",
      "email": "",
      "liens": []
    }}
  }},
  "classification": {{
    "domaine": "",
    "secteur": "",
    "niveau": ""
  }},
  "contrat_souhaite": "",
  "profil_resume": "",
  "formation": [
    {{ "periode": "", "diplome": "", "etablissement": "", "lieu": "" }}
  ],
  "experience_professionnelle": [
    {{ "periode": "", "poste": "", "entreprise": "", "missions": [] }}
  ],
  "competences": {{
    "savoir_faire": [],
    "savoir_etre": [],
    "langues": [
      {{ "langue": "", "niveau": "" }},
      {{ "langue": "", "niveau": "" }}
    ]
  }},
  "projets_et_certifications": {{
    "projets": [
      {{ "nom": "", "description": "", "technologies": [] }},
      {{ "nom": "", "description": "", "technologies": [] }}
    ],
    "certifications": [
      {{ "nom": "", "organisme": "", "annee": "" }},
      {{ "nom": "", "organisme": "", "annee": "" }}
    ]
  }}
}}
"""

# ─── Helpers ──────────────────────────────────────────────────────
def _safe_str(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if value.lower() in ["null", "none", "nan", "undefined"]:
        return ""
    return value

def _safe_list(value):
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

def _safe_cv_complet(data: dict) -> dict:
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

    identite       = data.get("identite", {}) if isinstance(data.get("identite"), dict) else {}
    contact        = identite.get("contact", {}) if isinstance(identite.get("contact"), dict) else {}
    classification = data.get("classification", {}) if isinstance(data.get("classification"), dict) else {}
    competences    = data.get("competences", {}) if isinstance(data.get("competences"), dict) else {}

    # ── projets & certifications : cherche dans la clé imbriquée ET à la racine ──
    pac = data.get("projets_et_certifications", {})
    if not isinstance(pac, dict):
        pac = {}

    projets_raw        = _safe_list(pac.get("projets"))        or _safe_list(data.get("projets"))
    certifications_raw = _safe_list(pac.get("certifications")) or _safe_list(data.get("certifications"))

    # Normalisation des projets (liste de dicts)
    projets = [
        {
            "nom":          _safe_str(p.get("nom")),
            "description":  _safe_str(p.get("description")),
            "technologies": _safe_str(p.get("technologies")),
        }
        for p in projets_raw
        if isinstance(p, dict)
    ]

    # Normalisation des certifications (liste de dicts)
    certifications = [
        {
            "nom":       _safe_str(c.get("nom")),
            "organisme": _safe_str(c.get("organisme")),
            "annee":     _safe_str(c.get("annee")),
        }
        for c in certifications_raw
        if isinstance(c, dict)
    ]

    # ── Autres champs ─────────────────────────────────────────────
    domaine = _safe_str(classification.get("domaine"))
    secteur = _safe_str(classification.get("secteur"))
    niveau  = _safe_str(classification.get("niveau"))
    contrat = _safe_str(data.get("contrat_souhaite"))

    formation = [
        {
            "periode":       _safe_str(f.get("periode")),
            "diplome":       _safe_str(f.get("diplome")),
            "etablissement": _safe_str(f.get("etablissement")),
            "lieu":          _safe_str(f.get("lieu")),
        }
        for f in _safe_list(data.get("formation"))
        if isinstance(f, dict)
    ]

    experience = [
        {
            "periode":    _safe_str(e.get("periode")),
            "poste":      _safe_str(e.get("poste")),
            "entreprise": _safe_str(e.get("entreprise")),
            "missions":   _safe_list(e.get("missions")),
        }
        for e in _safe_list(data.get("experience_professionnelle"))
        if isinstance(e, dict)
    ]

    langues = [
        {
            "langue": _safe_str(l.get("langue")),
            "niveau": _safe_str(l.get("niveau")),
        }
        for l in _safe_list(competences.get("langues"))
        if isinstance(l, dict)
    ]

    return {
        "identite": {
            "nom_complet":         _safe_str(identite.get("nom_complet")),
            "titre_professionnel": _safe_str(identite.get("titre_professionnel")),
            "contact": {
                "ville":     _safe_str(contact.get("ville")),
                "pays":      _safe_str(contact.get("pays")),
                "telephone": _safe_str(contact.get("telephone")),
                "email":     _safe_str(contact.get("email")),
                "liens":     _safe_list(contact.get("liens")),
            }
        },
        "classification": {
            "domaine": domaine if domaine in DOMAINES_VALIDES else "",
            "secteur": secteur if secteur in SECTEURS_VALIDES else "",
            "niveau":  niveau  if niveau  in NIVEAUX_VALIDES  else "",
        },
        "contrat_souhaite": contrat if contrat in CONTRATS_VALIDES else "",
        "profil_resume":    _safe_str(data.get("profil_resume")),
        "formation":        formation,
        "experience_professionnelle": experience,
        "competences": {
            "savoir_faire": _safe_list(competences.get("savoir_faire")),
            "savoir_etre":  _safe_list(competences.get("savoir_etre")),
            "langues":      langues,
        },
        "projets_et_certifications": {
            "projets":        projets,
            "certifications": certifications,
        }
    }


_FALLBACK = {
    "identite": {
        "nom_complet": "", "titre_professionnel": "",
        "contact": { "ville": "", "pays": "", "telephone": "", "email": "", "liens": [] }
    },
    "classification": { "domaine": "", "secteur": "", "niveau": "" },
    "contrat_souhaite": "",
    "profil_resume": "",
    "formation": [],
    "experience_professionnelle": [],
    "competences": { "savoir_faire": [], "savoir_etre": [], "langues": [] },
    "projets_et_certifications": { "projets": [], "certifications": [] }
}


# ─── Fonction principale ──────────────────────────────────────────

def extract_cv_complet(cv_text: str) -> dict:
    messages = [
        SystemMessage(content=CV_COMPLET_PROMPT),
        HumanMessage(content=cv_text)
    ]

    last_error = None

    for attempt in range(max_retries):
        llm = get_next_llm()
        try:
            resp = llm.invoke(messages).content
            data = _parse_json(resp)

            if not isinstance(data, dict):
                raise ValueError("Réponse JSON n'est pas un objet")

            result = _safe_cv_complet(data)
            logger.info(f"[extract_cv_complet] Succès tentative #{attempt + 1}")
            return result

        except Exception as e:
            last_error = e
            logger.warning(f"[extract_cv_complet] Tentative #{attempt + 1} échouée : {e}")
            continue

    logger.error(f"[extract_cv_complet] Toutes les tentatives ont échoué : {last_error}")
    return _FALLBACK.copy()