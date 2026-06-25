# ── Stdlib ────────────────────────────────────────────────────────────────────
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import re
import threading
import uuid
from typing import Optional

# ── Third-party ───────────────────────────────────────────────────────────────
import numpy as np
from asgiref.sync import async_to_sync
from dotenv import load_dotenv
from fastembed import SparseTextEmbedding
from qdrant_client import QdrantClient, models
from qdrant_client.models import (
    Distance,
    PointStruct,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)
from sentence_transformers import CrossEncoder, SentenceTransformer
import gc
import torch

load_dotenv()

logger = logging.getLogger(__name__)

os.environ["HF_TOKEN"] = os.getenv("HF_TOKEN", "")
_cpu_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=os.cpu_count(),
    thread_name_prefix="recommender-cpu",
)

BGE_M3_DIM = 1024


# ─────────────────────────────────────────────────────────────────────────────
# UTILS TEXTE
# ─────────────────────────────────────────────────────────────────────────────

def safe_to_text(val: object) -> str:
    """Convertit n'importe quelle valeur (str, dict, list, autre) en texte lisible."""
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        for key in ("description", "texte", "text", "nom", "titre", "name", "label"):
            if val.get(key):
                return val[key]
        return " ".join(str(v) for v in val.values() if v)
    if isinstance(val, list):
        return ", ".join(safe_to_text(i) for i in val)
    return str(val) if val is not None else ""


def _normalise_liste(valeur: object) -> list:
    """Garantit un retour liste quelle que soit l'entrée."""
    if isinstance(valeur, list):
        return valeur
    if isinstance(valeur, str) and valeur:
        return [valeur]
    return []


def safe_join_list(values: object) -> str:
    if not isinstance(values, list):
        return str(values) if values else ""
    return " ".join(str(v) for v in values if isinstance(v, (str, int, float)) and v)


def _tokenize(text: str) -> list[str]:
    """Tokenisation basique avec support des caractères français."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9àâçéèêëîïôûùüÿñæœ\+\#\.\s]", " ", text)
    text = text.replace("-", " ")
    return [t for t in text.split() if len(t) > 1]


def _fmt_item(item) -> str:
    """Convertit un projet ou certification (str ou dict) en texte lisible."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        parts = []
        for key in ("nom", "titre", "name", "label"):
            if item.get(key):
                parts.append(item[key])
                break
        if item.get("description"):
            parts.append(item["description"])
        techs = item.get("technologies", item.get("outils", []))
        if techs and isinstance(techs, list):
            parts.append("Technologies : " + ", ".join(
                t if isinstance(t, str) else str(t) for t in techs
            ))
        return " — ".join(parts) if parts else str(item)
    return str(item)


# ─────────────────────────────────────────────────────────────────────────────
# CV JSON → TEXTE PLAT (embedding dense)
# ─────────────────────────────────────────────────────────────────────────────
def cv_json_to_text_for_embedding_impl_v2(cv: dict) -> tuple[str, str]:

    def clean(text):
        return " ".join(str(text).split()) if text else ""

    # --- PROFIL ---
    profil_parts = [
        cv.get("titre_professionnel", ""),
        cv.get("niveau", ""),
        cv.get("contrat_souhaite", ""),
        cv.get("ville", ""),
        cv.get("pays", "")
    ]
    profil = " | ".join(clean(p) for p in profil_parts if p)

    # --- SKILLS ---
    skills_list = []

    skills_list.extend(cv.get("savoir_faire", []))
    skills_list.extend(cv.get("savoir_etre", []))

    # langues enrichies
    for lang in cv.get("langues", []):
        if isinstance(lang, dict):
            skills_list.append(lang.get("langue", ""))
            skills_list.append(lang.get("niveau", ""))

    skills = " ".join(clean(s) for s in skills_list if s)

    return profil, skills
# ─────────────────────────────────────────────────────────────────────────────
# BM25 / SPARSE
# ─────────────────────────────────────────────────────────────────────────────

STOPWORDS = {
    "de", "la", "le", "les", "des", "et", "en", "du",
    "un", "une", "pour", "dans", "avec", "sur",
    "au", "aux", "par", "ce", "cette", "ces",
}


def clean_tokens(tokens: list[str]) -> list[str]:
    return [t for t in tokens if t and t.strip() and t not in STOPWORDS and len(t) > 2]


def deduplicate(tokens: list[str]) -> list[str]:
    return list(set(tokens))


def build_bm25_global(skills_tokens: list, missions_tokens: list, metier_tokens: list) -> str:
    return " ".join(skills_tokens + missions_tokens + metier_tokens)


def cv_json_to_text_for_bm25_v3(cv: dict) -> tuple[list, list, list]:
    skills_list     = _normalise_liste(cv.get("savoir_faire", []))
    skills_tokens   = [str(s).lower() for s in skills_list if s]
    missions_tokens = []
    metier_tokens   = []

    for exp in cv.get("experience", []):
        poste = exp.get("poste", "")
        if poste:
            metier_tokens.extend(_tokenize(poste.lower()))
        for m in _normalise_liste(exp.get("missions", [])):
            if isinstance(m, (str, int, float)) and m:
                missions_tokens.extend(_tokenize(str(m)))

    skills_tokens   = clean_tokens(skills_tokens)
    missions_tokens = clean_tokens(deduplicate(missions_tokens))
    metier_tokens   = clean_tokens(deduplicate(metier_tokens))

    skills_tokens = skills_tokens * 3
    metier_tokens = metier_tokens * 2

    return skills_tokens, missions_tokens, metier_tokens


# ─────────────────────────────────────────────────────────────────────────────
# OFFRE → TEXTE STRUCTURÉ
# ─────────────────────────────────────────────────────────────────────────────

def offre_to_structured_v2(offre: dict) -> tuple[str, str, str, str, str, str]:
    profil_parts = [
        offre.get("poste", {}).get("titre_professionnel", ""),
        offre.get("profil_recherche", ""),
        offre.get("poste", {}).get("type_contrat", ""),
        offre.get("poste", {}).get("localisation", ""),
    ]
    profil = " ".join(p for p in profil_parts if p)

    comp        = offre.get("competences_requises", {})
    skills_list = []
    skills_list.extend(comp.get("savoir_faire", []))
    skills_list.extend(comp.get("savoir_etre", []))
    for lang in comp.get("langues", []):
        if isinstance(lang, dict):
            skills_list.append(lang.get("langue", ""))
            skills_list.append(lang.get("niveau", ""))
    skills = " ".join(s for s in skills_list if s)

    experiences_list = []
    for exp in offre.get("experience_requise", []):
        text     = f"{exp.get('poste', '')} {exp.get('annees_experience', '')}"
        missions = " ".join(exp.get("missions_attendues", []))
        experiences_list.append(f"{text} {missions}")
    experiences = " ".join(experiences_list)

    projets_list = []
    projets_list.extend(offre.get("projets_valorises", []))
    projets_list.extend(offre.get("certifications_valorisees", []))
    projets = " ".join(_fmt_item(p) for p in projets_list)

    domaine = str(offre.get("poste", {}).get("domaine", ""))
    secteur = str(offre.get("secteur_activite", ""))

    return profil, skills, experiences, projets, domaine, secteur


# ─────────────────────────────────────────────────────────────────────────────
# TEXTES RERANKING
# ─────────────────────────────────────────────────────────────────────────────
def offre_to_rerank_text(offre: dict) -> str:
    def clean(x):
        return " ".join(str(x).split()) if x else ""

    poste = offre.get("poste", {})
    competences = offre.get("competences_requises", {})

    # --- POSTE ---
    titre = clean(poste.get("titre_professionnel", ""))
    contrat = clean(poste.get("type_contrat", ""))
    localisation = clean(poste.get("localisation", ""))

    # --- SKILLS ---
    skills_list = []
    skills_list.extend(competences.get("savoir_faire", []))
    skills_list.extend(competences.get("savoir_etre", []))

    for lang in competences.get("langues", []):
        if isinstance(lang, dict):
            skills_list.append(lang.get("langue", ""))
            skills_list.append(lang.get("niveau", ""))

    skills = " ".join(clean(s) for s in skills_list if s)

    # --- DOMAIN / SECTOR ---
    domaine = clean(poste.get("domaine", ""))
    secteur = clean(offre.get("secteur_activite", ""))

    return (
        f"[PROFILE] {titre}\n"
        f"[CONTRAT] {contrat}\n"
        f"[LOCALISATION] {localisation}\n"
        f"[SKILLS] {skills}\n"
        f"[DOMAIN] {domaine}\n"
        f"[SECTOR] {secteur}\n"
    )

def cv_to_rerank_text(cv: dict) -> str:
    def clean(x):
        return " ".join(str(x).split()) if x else ""

    # --- PROFILE ---
    titre = clean(cv.get("titre_professionnel", ""))
    contrat = clean(cv.get("contrat_souhaite", ""))
    ville = clean(cv.get("ville", ""))

    # --- SKILLS ---
    skills_list = []
    skills_list.extend(cv.get("savoir_faire", []))
    skills_list.extend(cv.get("savoir_etre", []))
    for lang in cv.get("langues", []):
        if isinstance(lang, dict):
            skills_list.append(lang.get("langue", ""))
            skills_list.append(lang.get("niveau", ""))
    skills = " ".join(clean(s) for s in skills_list if s)

    # --- DOMAIN / SECTOR ---
    domaine = clean(cv.get("domaine", ""))
    secteur = clean(cv.get("secteur", ""))
    domaine = clean(domaine)
    secteur = clean(secteur)

    return (
        f"[PROFILE] {titre}\n"
        f"[CONTRAT] {contrat}\n"
        f"[LOCALISATION] {ville}\n"
        f"[SKILLS] {skills}\n"
        f"[DOMAIN] {domaine}\n"
        f"[SECTOR] {secteur}\n"
    )
# ─────────────────────────────────────────────────────────────────────────────
# SPARSE ENCODER 
# ─────────────────────────────────────────────────────────────────────────────

_bm25_model: Optional[SparseTextEmbedding] = None
_bm25_lock  = threading.Lock()


def get_bm25_model() -> SparseTextEmbedding:
    global _bm25_model
    if _bm25_model is None:
        with _bm25_lock:
            if _bm25_model is None:
                logger.info("Chargement du modèle BM25 FastEmbed…")
                _bm25_model = SparseTextEmbedding(model_name="Qdrant/bm25")
    return _bm25_model


def encode_sparse_bm25(text: str) -> SparseVector:
    model  = get_bm25_model()
    result = list(model.embed([text]))[0]
    return SparseVector(
        indices=result.indices.tolist(),
        values=result.values.tolist(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# SYSTÈME DE RECOMMANDATION
# ─────────────────────────────────────────────────────────────────────────────

class DeliorRecommenderSysteme:

    def __init__(
        self,
        url:              str,
        api_key:          str,
        collection_name:  str,
        model_dense_name: str,
    ) -> None:
        self.url              = url
        self.api_key          = api_key
        self.collection_name  = collection_name
        self.model_dense_name = model_dense_name
        self._init()

    def _init(self) -> None:
        self.client_qdrant = QdrantClient(url=self.url, api_key=self.api_key)
        logger.info("Chargement du modèle dense %s…", self.model_dense_name)
        self.model_dense = SentenceTransformer(self.model_dense_name)
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        try:
            self.client_qdrant.get_collection(self.collection_name)
            logger.info("Collection '%s' déjà existante.", self.collection_name)
        except Exception:
            logger.info("Création de la collection '%s'…", self.collection_name)
            self.client_qdrant.create_collection(
                collection_name=self.collection_name,
                vectors_config={
                    "dense_profil": VectorParams(size=BGE_M3_DIM, distance=Distance.COSINE),
                    "dense_skills": VectorParams(size=BGE_M3_DIM, distance=Distance.COSINE),
                },
                sparse_vectors_config={
                    "sparse": SparseVectorParams(
                        modifier=models.Modifier.IDF,
                        index=models.SparseIndexParams(on_disk=False),
                    )
                },
            )

    def build_dense_vectors(self, profil: str, skills: str) -> dict:
        texts = [profil or "vide", skills or "vide"]
        vecs  = self.model_dense.encode(
            texts,
            normalize_embeddings=True,
            batch_size=4,
        )
        return {
            "dense_profil": vecs[0].tolist(),
            "dense_skills": vecs[1].tolist(),
        }

    def _make_point(self, candidat_id: str, donnees_structurees: dict) -> Optional[PointStruct]:
        try:
            profil, skills = cv_json_to_text_for_embedding_impl_v2(donnees_structurees)
            text_bm25      = build_bm25_global(*cv_json_to_text_for_bm25_v3(donnees_structurees))
            dense_vector   = self.build_dense_vectors(profil, skills)
            sparse_vector  = encode_sparse_bm25(text_bm25)

            domaine = donnees_structurees.get("domaine", "")
            secteur = donnees_structurees.get("secteur", "")
            niveau  = donnees_structurees.get("niveau", "")
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(candidat_id)))

            return PointStruct(
                id=point_id,
                vector={
                    **dense_vector,
                    "sparse": models.SparseVector(
                        indices=sparse_vector.indices,
                        values=sparse_vector.values,
                    ),
                },
                payload={
                    "candidat_id":        str(candidat_id),
                    "donnees_structurees": donnees_structurees,
                    "domaine":            domaine,
                    "secteur":            secteur,
                    "niveau":             niveau,
                },
            )
        except Exception as e:
            logger.error("Erreur _make_point | candidat_id=%s | erreur=%s", candidat_id, str(e), exc_info=True)
            return None

    def full_reindex(self, queryset, batch_size: int = 50) -> None:
        batch: list[PointStruct] = []
        for cv in queryset.values("candidat_id", "donnees_structurees").iterator():
            try:
                point = self._make_point(cv["candidat_id"], cv["donnees_structurees"])
                if point is None:
                    logger.warning("Point ignoré (None) | candidat_id=%s", cv["candidat_id"])
                    continue
                batch.append(point)
                if len(batch) >= batch_size:
                    self.client_qdrant.upsert(self.collection_name, points=batch)
                    logger.info("Batch de %d points upserted.", len(batch))
                    batch = []
            except Exception as e:
                logger.error("Erreur full_reindex | candidat_id=%s | erreur=%s", cv.get("candidat_id"), str(e), exc_info=True)
        if batch:
            self.client_qdrant.upsert(self.collection_name, points=batch)
            logger.info("Dernier batch de %d points upserted.", len(batch))

    def index_single(self, candidat_id: str, donnees_structurees: dict) -> None:
        try:
            point = self._make_point(candidat_id, donnees_structurees)
            if point is None:
                logger.warning("Index ignoré (point None) | candidat_id=%s", candidat_id)
                return
            self.client_qdrant.upsert(collection_name=self.collection_name, points=[point])
            logger.info("Candidat %s indexé.", candidat_id)
        except Exception as e:
            logger.error("Erreur index_single | candidat_id=%s | erreur=%s", candidat_id, str(e), exc_info=True)

    def delete_candidate(self, candidat_id: str) -> None:
        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(candidat_id)))
        self.client_qdrant.delete(
            collection_name=self.collection_name,
            points_selector=models.PointIdsList(points=[point_id]),
        )
        logger.info("Candidat %s supprimé.", candidat_id)

    async def recommender_cv(self, offre: dict, nb_result: int = 5):
        try:
            profil, skills, experiences, projets, domaine, secteur = offre_to_structured_v2(offre)

            offre_bm25 = {
                "savoir_faire": (offre.get("competences_requises") or {}).get("savoir_faire", []),
                "experience": [
                    {
                        "poste":    exp.get("poste", ""),
                        "missions": exp.get("missions_attendues", []),
                    }
                    for exp in (offre.get("experience_requise") or [])
                    if isinstance(exp, dict)
                ],
            }

            loop = asyncio.get_running_loop()

            dense_search = await loop.run_in_executor(
                _cpu_executor,
                lambda: self.build_dense_vectors(profil, skills),
            )

            sparse_query = await loop.run_in_executor(
                _cpu_executor,
                lambda: encode_sparse_bm25(
                    build_bm25_global(*cv_json_to_text_for_bm25_v3(offre_bm25))
                ),
            )

            result = await loop.run_in_executor(
                _cpu_executor,
                lambda: self.client_qdrant.query_points(
                    collection_name=self.collection_name,
                    prefetch=[
                        models.Prefetch(query=dense_search["dense_profil"], using="dense_profil", limit=100),
                        models.Prefetch(query=dense_search["dense_skills"], using="dense_skills", limit=100),
                        models.Prefetch(query=sparse_query,                 using="sparse",        limit=100),
                    ],
                    query=models.FusionQuery(fusion=models.Fusion.RRF),
                    limit=min(nb_result, 40),
                ),
            )

            return result

        except Exception as e:
            logger.error("Erreur recommender_cv | erreur=%s", str(e), exc_info=True)
            return None


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETONS
# ─────────────────────────────────────────────────────────────────────────────
_rsys_instance:     Optional[DeliorRecommenderSysteme] = None
_reranker_instance: Optional[CrossEncoder]             = None
_rsys_lock     = threading.Lock()
_reranker_lock = threading.Lock()

# ─────────────────────────────────────────────────────────────────────────────
# SINGLETONS  (remplace l'ancienne section)
# ─────────────────────────────────────────────────────────────────────────────

def _free_memory() -> None:
    """Force le GC + vide le cache CUDA/MPS si disponible."""
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def get_recommender() -> DeliorRecommenderSysteme:
    global _rsys_instance
    if _rsys_instance is None:
        with _rsys_lock:
            if _rsys_instance is None:
                # S'assure que le reranker est déchargé avant
                unload_reranker()
                logger.info("Initialisation du recommender…")
                _rsys_instance = DeliorRecommenderSysteme(
                    url=os.getenv("URL_QDRANT"),
                    api_key=os.getenv("API_QDRANT"),
                    collection_name=os.getenv("COLLECTION_NAME"),
                    model_dense_name="BAAI/bge-m3",
                )
    return _rsys_instance


def unload_recommender() -> None:
    global _rsys_instance
    with _rsys_lock:
        if _rsys_instance is not None:
            logger.info("Déchargement du modèle dense…")
            # Supprime le modèle dense du recommender sans fermer le client Qdrant
            if hasattr(_rsys_instance, "model_dense"):
                del _rsys_instance.model_dense
                _rsys_instance.model_dense = None
            _rsys_instance = None
            _free_memory()
            logger.info("Modèle dense déchargé.")


def get_reranker() -> CrossEncoder:
    global _reranker_instance
    if _reranker_instance is None:
        with _reranker_lock:
            if _reranker_instance is None:
                # S'assure que le recommender est déchargé avant
                unload_recommender()
                logger.info("Chargement du reranker BAAI/bge-reranker-v2-m3…")
                _reranker_instance = CrossEncoder(
                    "BAAI/bge-reranker-v2-m3",
                    max_length=512,
                )
    return _reranker_instance


def unload_reranker() -> None:
    global _reranker_instance
    with _reranker_lock:
        if _reranker_instance is not None:
            logger.info("Déchargement du reranker…")
            del _reranker_instance
            _reranker_instance = None
            _free_memory()
            logger.info("Reranker déchargé.")


# ─────────────────────────────────────────────────────────────────────────────
# RECOMMANDATION + RERANKING  (remplace l'ancienne fonction)
# ─────────────────────────────────────────────────────────────────────────────

async def recommendation_candidat_cv_async(
    query: dict,
    limit: int = 5,
    rerank_threshold: float = 0.55,
) -> list:
    try:
        # ── ÉTAPE 1 : recherche vectorielle (modèle dense + BM25) ────────────
        rsys = get_recommender()          # charge dense si besoin
        data = await rsys.recommender_cv(query, limit)

        if not data or not data.points:
            logger.info("Aucun résultat vectoriel.")
            return []

        hits = data.points

        # Prépare les paires AVANT de décharger quoi que ce soit
        query_text = offre_to_rerank_text(query)
        query_vs_cvs = [
            (query_text, cv_to_rerank_text(hit.payload["donnees_structurees"]))
            for hit in hits
        ]

        # ── ÉTAPE 2 : décharge le dense, charge le reranker ─────────────────
        unload_recommender()              # libère BGE-M3
        reranker = get_reranker()         # charge bge-reranker-v2-m3

        loop = asyncio.get_running_loop()
        scores: np.ndarray = await loop.run_in_executor(
            _cpu_executor,
            lambda: reranker.predict(query_vs_cvs),
        )

        # ── ÉTAPE 3 : décharge le reranker (optionnel, selon ton workflow) ───
        unload_reranker()

        # ── ÉTAPE 4 : tri et seuillage ───────────────────────────────────────
        sorted_indices = np.argsort(scores)[::-1].tolist()

        results = []
        for i in sorted_indices:
            if scores[i] >= rerank_threshold:
                results.append((hits[i], float(scores[i])))
            if len(results) >= limit:
                break

        logger.info("Résultats finaux : %d", len(results))
        return results

    except Exception as e:
        logger.error("Erreur pipeline recommendation | erreur=%s", str(e), exc_info=True)
        return []

# _rsys_instance:     Optional[DeliorRecommenderSysteme] = None
# _reranker_instance: Optional[CrossEncoder]             = None
# _rsys_lock     = threading.Lock()
# _reranker_lock = threading.Lock()


# def get_recommender() -> DeliorRecommenderSysteme:
#     global _rsys_instance
#     if _rsys_instance is None:
#         with _rsys_lock:
#             if _rsys_instance is None:
#                 logger.info("Initialisation du recommender…")
#                 _rsys_instance = DeliorRecommenderSysteme(
#                     url=os.getenv("URL_QDRANT"),
#                     api_key=os.getenv("API_QDRANT"),
#                     collection_name=os.getenv("COLLECTION_NAME"),
#                     model_dense_name="BAAI/bge-m3",
#                 )
#     return _rsys_instance


# def get_reranker() -> CrossEncoder:
#     global _reranker_instance
#     if _reranker_instance is None:
#         with _reranker_lock:
#             if _reranker_instance is None:
#                 import gc
#                 gc.collect()
#                 logger.info("Chargement du reranker BAAI/bge-reranker-v2-m3…")
#                 _reranker_instance = CrossEncoder(
#                     "BAAI/bge-reranker-v2-m3",
#                     max_length=512,
#                 )
#     return _reranker_instance


# ─────────────────────────────────────────────────────────────────────────────
# RECOMMANDATION + RERANKING
# ─────────────────────────────────────────────────────────────────────────────

# async def recommendation_candidat_cv_async(query: dict, limit: int = 5, rerank_threshold: float = 0.55) -> list:
#     try:
#         rsys     = get_recommender()
#         reranker = get_reranker()

#         data = await rsys.recommender_cv(query, limit)

#         if not data or not data.points:
#             logger.info("Aucun résultat vectoriel.")
#             return []

#         hits = data.points
#         print(offre_to_rerank_text(query),'\n\n')
#         for hit in hits:
#             print(cv_to_rerank_text(hit.payload["donnees_structurees"]))
#         query_vs_cvs = [
#             (
#                 offre_to_rerank_text(query),
#                 cv_to_rerank_text(hit.payload["donnees_structurees"]),
#             )
#             for hit in hits
#         ]

#         loop = asyncio.get_running_loop()
#         scores: np.ndarray = await loop.run_in_executor(
#             _cpu_executor,
#             lambda: reranker.predict(query_vs_cvs),
#         )

#         sorted_indices = np.argsort(scores)[::-1].tolist()

#         results = []
#         for i in sorted_indices:
#             print(scores[i])
#             if scores[i] >= rerank_threshold:
#                 results.append((hits[i], float(scores[i])))
#             if len(results) >= limit:
#                 break

#         logger.info("Résultats finaux : %d", len(results))
#         return results

#     except Exception as e:
#         logger.error("Erreur pipeline recommendation | erreur=%s", str(e), exc_info=True)
#         return []


def recommendation_candidat_cv_impl(query: dict, limit: int = 5) -> list:
    return async_to_sync(recommendation_candidat_cv_async)(query, limit)


# ─────────────────────────────────────────────────────────────────────────────
# CV / OFFRE → TEXTE POUR RAISONNEMENT LLM
# ─────────────────────────────────────────────────────────────────────────────

def cv_json_to_text_for_reasoning_impl(cv: dict) -> str:
    sections: list[str] = []

    identity_text = (
        f"Nom: {cv.get('nom_complet', '')}\n"
        f"Titre: {cv.get('titre_professionnel', '')}\n"
        f"Ville: {cv.get('ville', 'mobilité')}\n"
        f"Pays: {cv.get('pays', '')}\n"
        f"Telephone: {cv.get('telephone', '')}\n"
        f"Email: {cv.get('email', '')}"
    )
    sections.append(identity_text.strip())

    if cv.get("profil_resume"):
        sections.append(f"Profil: {cv['profil_resume']}")

    formation_text = [
        f"{f.get('periode', '')} - {f.get('diplome', '')} - "
        f"{f.get('etablissement', '')} ({f.get('lieu', '')})"
        for f in (cv.get("formation", []) or [])
        if isinstance(f, dict)
    ]
    if formation_text:
        sections.append("Formation:\n" + "\n".join(formation_text))

    exp_text = []
    for exp in (cv.get("experience", []) or []):
        if not isinstance(exp, dict):
            continue
        missions_str = ", ".join(safe_to_text(m) for m in (exp.get("missions", []) or []))
        exp_text.append(
            f"{exp.get('periode', '')} - {exp.get('poste', '')} "
            f"chez {exp.get('entreprise', '')} : {missions_str}"
        )
    if exp_text:
        sections.append("Expérience:\n" + "\n".join(exp_text))

    skills_text: list[str] = []
    savoir_faire = cv.get("savoir_faire", []) or []
    if savoir_faire:
        skills_text.append("Techniques: " + ", ".join(safe_to_text(s) for s in savoir_faire))
    savoir_etre = cv.get("savoir_etre", []) or []
    if savoir_etre:
        skills_text.append("Soft Skills: " + ", ".join(safe_to_text(s) for s in savoir_etre))
    langues = cv.get("langues", []) or []
    if langues:
        langs = [
            f"{l.get('langue', '')} ({l.get('niveau', '')})" if isinstance(l, dict) else safe_to_text(l)
            for l in langues
        ]
        skills_text.append("Langues: " + ", ".join(langs))
    if skills_text:
        sections.append("Compétences:\n" + "\n".join(skills_text))

    projects = cv.get("projets", []) or []
    if projects:
        sections.append("Projets:\n" + "\n".join(_fmt_item(p) for p in projects))

    certs = cv.get("certifications", []) or []
    if certs:
        sections.append("Certifications:\n" + "\n".join(_fmt_item(c) for c in certs))

    return "\n\n".join(s for s in sections if s.strip())


def offre_json_to_text_for_reasoning_impl(offre: dict) -> str:
    sections: list[str] = []

    poste = offre.get("poste", {}) or {}
    sections.append((
        f"Titre: {poste.get('titre_professionnel', '')}\n"
        f"Domaine: {poste.get('domaine', '')}\n"
        f"Localisation: {poste.get('localisation', '')}\n"
        f"Contrat: {poste.get('type_contrat', '')}\n"
        f"Secteur: {offre.get('secteur_activite', '')}"
    ).strip())

    if offre.get("profil_recherche"):
        sections.append(f"Profil recherché: {offre['profil_recherche']}")

    formations = offre.get("formation_requise", []) or []
    if formations:
        sections.append("Formation requise:\n" + "\n".join(f"- {safe_to_text(f)}" for f in formations))

    exp_text = []
    for exp in (offre.get("experience_requise", []) or []):
        if not isinstance(exp, dict):
            exp_text.append(safe_to_text(exp))
            continue
        missions     = exp.get("missions_attendues", []) or []
        missions_str = "\n".join(f"  • {safe_to_text(m)}" for m in missions)
        exp_text.append(
            f"{exp.get('poste', '')} — {exp.get('annees_experience', '')}"
            + (f"\n{missions_str}" if missions_str else "")
        )
    if exp_text:
        sections.append("Expérience requise:\n" + "\n".join(exp_text))

    comp = offre.get("competences_requises", {}) or {}
    skills_text: list[str] = []
    if comp.get("savoir_faire"):
        skills_text.append("Techniques: " + ", ".join(safe_to_text(s) for s in comp["savoir_faire"]))
    if comp.get("savoir_etre"):
        skills_text.append("Soft Skills: " + ", ".join(safe_to_text(s) for s in comp["savoir_etre"]))
    langues = comp.get("langues", []) or []
    if langues:
        langs = [
            f"{l.get('langue', '')} ({l.get('niveau', '')})" if isinstance(l, dict) else safe_to_text(l)
            for l in langues
        ]
        skills_text.append("Langues: " + ", ".join(langs))
    if skills_text:
        sections.append("Compétences requises:\n" + "\n".join(skills_text))

    projets = offre.get("projets_valorises", []) or []
    if projets:
        sections.append("Projets valorisés:\n" + "\n".join(f"- {safe_to_text(p)}" for p in projets))

    certifs = offre.get("certifications_valorisees", []) or []
    if certifs:
        sections.append("Certifications valorisées:\n" + "\n".join(f"- {safe_to_text(c)}" for c in certifs))

    return "\n\n".join(s for s in sections if s.strip())

