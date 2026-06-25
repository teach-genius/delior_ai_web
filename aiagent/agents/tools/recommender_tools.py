from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from aiagent.recommender.recommender_sys import (
    get_recommender,
    get_reranker,
    recommendation_candidat_cv_async,
    cv_json_to_text_for_reasoning_impl,
    offre_json_to_text_for_reasoning_impl,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# HELPERS INTERNES
# ══════════════════════════════════════════════════════════════════

def _get_offre_dict(offre_id: str) -> dict | None:
    """Charge une offre depuis la DB Django par son identifiant."""
    try:
        from candidat.models import QueryMatching
        offre = QueryMatching.objects.select_related("poste").get(identifiant=offre_id)
        return offre.content
    except Exception as e:
        logger.error("[recommender_tools] Offre introuvable : %s — %s", offre_id, e)
        return None


def _get_candidat_dict(candidat_id: str) -> dict | None:
    """Charge un CV candidat depuis la DB Django par son identifiant."""
    try:
        from candidat.models import CV
        cv = CV.objects.get(candidat_id=candidat_id)
        return cv.donnees_structurees
    except Exception as e:
        logger.error("[recommender_tools] Candidat introuvable : %s — %s", candidat_id, e)
        return None


def _fmt_candidats(results: list[tuple[Any, float]], verbose: bool = False) -> str:
    """Formate la liste de résultats (hit, score) en texte lisible pour le LLM."""
    if not results:
        return "Aucun candidat correspondant trouvé."

    lignes = []
    for i, (hit, score) in enumerate(results, 1):
        cv      = hit.payload.get("donnees_structurees", {})
        nom     = cv.get("nom_complet", "Inconnu")
        titre   = cv.get("titre_professionnel", "—")
        ville   = cv.get("ville", "—")
        email   = cv.get("email", "—")
        tel     = cv.get("telephone", "—")
        cid     = hit.payload.get("candidat_id", "—")

        ligne = (
            f"{i}. **{nom}** — {titre}\n"
            f"   📍 {ville} | 📧 {email} | 📞 {tel}\n"
            f"   🔑 ID : {cid} | Score : {score:.3f}"
        )

        if verbose:
            texte_cv = cv_json_to_text_for_reasoning_impl(cv)
            ligne += f"\n\n{texte_cv}\n"

        lignes.append(ligne)

    return f"**{len(results)} candidat(s) trouvé(s)** :\n\n" + "\n\n".join(lignes)


# ══════════════════════════════════════════════════════════════════
# OUTIL 1 : Recommander des CVs pour une offre
# ══════════════════════════════════════════════════════════════════

@tool
async def recommander_candidats_pour_offre(
    offre_id: str,
    limite: int = 5,
    seuil_rerank: float = 0.55,
    verbose: bool = False,
) -> str:
    """
    Recommande les meilleurs candidats pour une offre d'emploi donnée.
    Utilise la recherche hybride (dense BGE-M3 + sparse BM25) + reranking.

    Args:
        offre_id     : identifiant de l'offre dans la base de données
        limite       : nombre max de candidats à retourner (défaut: 5)
        seuil_rerank : score minimum de reranking pour inclure un candidat (défaut: 0.55)
        verbose      : si True, inclut le détail complet du CV dans la réponse
    """
    offre_dict = _get_offre_dict(offre_id)
    if not offre_dict:
        return f"❌ Offre introuvable : {offre_id}"

    try:
        results = await recommendation_candidat_cv_async(
            query=offre_dict,
            limit=limite,
            rerank_threshold=seuil_rerank,
        )
        header = f"Recommandations pour l'offre **{offre_dict.get('poste', {}).get('titre_professionnel', offre_id)}** :\n\n"
        return header + _fmt_candidats(results, verbose=verbose)

    except Exception as e:
        logger.error("[recommender_tools] recommander_candidats : %s", e)
        return f"❌ Erreur lors de la recommandation : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 2 : Recommander depuis un dict offre direct (sans DB)
# ══════════════════════════════════════════════════════════════════

@tool
async def recommander_candidats_depuis_criteres(
    titre_poste: str,
    competences: list[str],
    localisation: str = "",
    type_contrat: str = "",
    limite: int = 5,
    seuil_rerank: float = 0.50,
) -> str:
    """
    Recommande des candidats à partir de critères libres sans offre existante en DB.
    Utile pour une recherche rapide ou exploratoire.

    Args:
        titre_poste  : intitulé du poste recherché (ex: 'Responsable Qualité FSSC')
        competences  : liste de compétences requises (ex: ['FSSC 22000', 'HACCP', 'audit'])
        localisation : ville ou région (ex: 'Rabat', optionnel)
        type_contrat : type de contrat (ex: 'CDI', 'CDD', optionnel)
        limite       : nombre max de candidats (défaut: 5)
        seuil_rerank : score minimum de reranking (défaut: 0.50)
    """
    offre_dict = {
        "poste": {
            "titre_professionnel": titre_poste,
            "localisation":        localisation,
            "type_contrat":        type_contrat,
            "domaine":             "",
        },
        "competences_requises": {
            "savoir_faire": competences,
            "savoir_etre":  [],
            "langues":      [],
        },
        "experience_requise":        [],
        "projets_valorises":         [],
        "certifications_valorisees": [],
        "secteur_activite":          "",
        "profil_recherche":          "",
    }

    try:
        results = await recommendation_candidat_cv_async(
            query=offre_dict,
            limit=limite,
            rerank_threshold=seuil_rerank,
        )
        header = f"Candidats pour **{titre_poste}** ({localisation or 'toute localisation'}) :\n\n"
        return header + _fmt_candidats(results)

    except Exception as e:
        logger.error("[recommender_tools] recommander_depuis_criteres : %s", e)
        return f"❌ Erreur lors de la recherche : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 3 : Lire le profil complet d'un candidat
# ══════════════════════════════════════════════════════════════════

@tool
def lire_profil_candidat(candidat_id: str) -> str:
    """
    Retourne le profil complet d'un candidat formaté pour le raisonnement.
    Utile après une recommandation pour analyser un candidat en détail.

    Args:
        candidat_id : identifiant du candidat (retourné par les tools de recommandation)
    """
    cv_dict = _get_candidat_dict(candidat_id)
    if not cv_dict:
        return f"❌ Candidat introuvable : {candidat_id}"

    try:
        texte = cv_json_to_text_for_reasoning_impl(cv_dict)
        nom   = cv_dict.get("nom_complet", candidat_id)
        return f"**Profil complet — {nom}**\n\n{texte}"

    except Exception as e:
        logger.error("[recommender_tools] lire_profil : %s", e)
        return f"❌ Erreur lecture profil : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 4 : Indexer ou réindexer un candidat
# ══════════════════════════════════════════════════════════════════

@tool
def indexer_candidat(candidat_id: str) -> str:
    """
    Indexe ou réindexe un candidat dans le moteur de recommandation Qdrant.
    À appeler après modification du CV d'un candidat.

    Args:
        candidat_id : identifiant du candidat à (ré)indexer
    """
    cv_dict = _get_candidat_dict(candidat_id)
    if not cv_dict:
        return f"❌ Candidat introuvable : {candidat_id}"

    try:
        rsys = get_recommender()
        rsys.index_single(candidat_id, cv_dict)
        nom = cv_dict.get("nom_complet", candidat_id)
        logger.info("[recommender_tools] Candidat indexé : %s", candidat_id)
        return f"✅ Candidat **{nom}** indexé avec succès dans Qdrant."

    except Exception as e:
        logger.error("[recommender_tools] indexer_candidat : %s", e)
        return f"❌ Erreur d'indexation : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 5 : Supprimer un candidat de l'index
# ══════════════════════════════════════════════════════════════════

@tool
def supprimer_candidat_index(candidat_id: str) -> str:
    """
    Supprime un candidat du moteur de recommandation Qdrant.
    À appeler quand un candidat est archivé ou supprimé.

    Args:
        candidat_id : identifiant du candidat à supprimer de l'index
    """
    try:
        rsys = get_recommender()
        rsys.delete_candidate(candidat_id)
        logger.info("[recommender_tools] Candidat supprimé de l'index : %s", candidat_id)
        return f"✅ Candidat {candidat_id} supprimé de l'index Qdrant."

    except Exception as e:
        logger.error("[recommender_tools] supprimer_candidat : %s", e)
        return f"❌ Erreur suppression index : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 6 : Lire le texte structuré d'une offre
# ══════════════════════════════════════════════════════════════════

@tool
def lire_offre(offre_id: str) -> str:
    """
    Retourne le texte structuré d'une offre d'emploi formaté pour le raisonnement.

    Args:
        offre_id : identifiant de l'offre dans la base de données
    """
    offre_dict = _get_offre_dict(offre_id)
    if not offre_dict:
        return f"❌ Offre introuvable : {offre_id}"

    try:
        texte = offre_json_to_text_for_reasoning_impl(offre_dict)
        titre = offre_dict.get("poste", {}).get("titre_professionnel", offre_id)
        return f"**Offre — {titre}**\n\n{texte}"

    except Exception as e:
        logger.error("[recommender_tools] lire_offre : %s", e)
        return f"❌ Erreur lecture offre : {e}"


# ══════════════════════════════════════════════════════════════════
# EXPORT
# ══════════════════════════════════════════════════════════════════

def get_recommender_tools() -> list:
    return [
        recommander_candidats_pour_offre,
        recommander_candidats_depuis_criteres,
        lire_profil_candidat,
        indexer_candidat,
        supprimer_candidat_index,
        lire_offre,
    ]