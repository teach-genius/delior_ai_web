import os
from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)


class AiagentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "aiagent"

    def ready(self):
        if os.environ.get("RUN_MAIN")!= "false":
            self._warmup()

    @staticmethod
    def _warmup():
        try:
            from aiagent.recommender.recommender_sys import get_bm25_model, get_recommender, get_reranker
            logger.info("Warm-up BM25…")
            get_bm25_model()
            logger.info("Warm-up recommender (BGE-M3 + Qdrant)…")
            get_recommender()
            # logger.info("Warm-up reranker (BGE-reranker-v2-m3)…")
            # get_reranker()
            logger.info("Tous les modèles sont prêts.")
        except Exception:
            logger.exception("Échec du warm-up des modèles ML.")