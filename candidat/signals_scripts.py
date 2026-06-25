import logging
import os

from django.conf import settings
from django.db.models.signals import post_save, post_delete
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from aiagent.recommender.recommender_sys import get_recommender
from .models import CandidatCV

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# QDRANT — INDEXATION / SUPPRESSION
# ─────────────────────────────────────────────

@receiver(post_save, sender=CandidatCV)
def update_qdrant(sender, instance, created, update_fields, **kwargs):
    """
    Ré-indexe le CV dans Qdrant à la création ou si donnees_structurees a changé.
    - created=True        → toujours indexer
    - update_fields=None  → save() complet, on indexe par sécurité
    - update_fields défini → indexer seulement si donnees_structurees est dedans
    """
    if not instance.donnees_structurees:
        return

    if not created:
        if update_fields is not None and "donnees_structurees" not in update_fields:
            return

    try:
        rsys = get_recommender()
        rsys.index_single(str(instance.candidat_id), instance.donnees_structurees)
        logger.info("Candidat %s indexé dans Qdrant.", instance.candidat_id)
    except Exception as e:
        logger.error("Erreur indexation Qdrant [candidat=%s] : %s", instance.candidat_id, e)


@receiver(post_delete, sender=CandidatCV)
def delete_from_qdrant(sender, instance, **kwargs):
    try:
        rsys = get_recommender()
        rsys.delete_candidate(str(instance.candidat_id))
        logger.info("Candidat %s supprimé de Qdrant.", instance.candidat_id)
    except Exception as e:
        logger.error("Erreur suppression Qdrant [candidat=%s] : %s", instance.candidat_id, e)


# ─────────────────────────────────────────────
# FICHIERS — NETTOYAGE À LA SUPPRESSION
# ─────────────────────────────────────────────

def _delete_file(path: str) -> None:
    """Supprime un fichier si il existe, log en cas d'erreur."""
    try:
        if path and os.path.isfile(path):
            os.remove(path)
            logger.info("Fichier supprimé : %s", path)
    except OSError as e:
        logger.error("Impossible de supprimer le fichier [%s] : %s", path, e)


@receiver(post_delete, sender=CandidatCV)
def delete_cv_files(sender, instance, **kwargs):
    if instance.fichier_pdf_origine:
        _delete_file(instance.fichier_pdf_origine.path)

    if hasattr(instance, "preview_image") and instance.preview_image:
        img_path = (
            instance.preview_image.path
            if hasattr(instance.preview_image, "path")
            else os.path.join(settings.MEDIA_ROOT, str(instance.preview_image))
        )
        _delete_file(img_path)


# ─────────────────────────────────────────────
# AUTH — LOGS
# ─────────────────────────────────────────────

@receiver(user_logged_in)
def notify_user_login(sender, request, user, **kwargs):
    if user:
        logger.info("Utilisateur connecté : %s", user.username)


@receiver(user_logged_out)
def notify_user_logout(sender, request, user, **kwargs):
    # FIX: user peut être None si session expirée avant logout
    if user:
        logger.info("Utilisateur déconnecté : %s", user.username)
    else:
        logger.info("Déconnexion d'une session expirée (user=None).")