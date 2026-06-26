import logging
import os
import shutil
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django_redis import get_redis_connection

logger = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

CV_EXTENSIONS = {".pdf", ".docx", ".doc"}
LOCK_TTL      = 1800 


# ── Helpers Redis lock ────────────────────────────────────────────────────────

def _lock_key(path: str) -> str:
    return f"cv_lock:{path}"


def _acquire_lock(rc, path: str, task_id: str) -> bool:
    """
    Pose un verrou atomique NX/EX sur le fichier.
    Retourne True si le verrou a été acquis, False s'il était déjà posé.
    """
    return bool(rc.set(_lock_key(path), task_id, nx=True, ex=LOCK_TTL))


def _release_lock(rc, path: str) -> None:
    rc.delete(_lock_key(path))


def _is_locked(rc, path: str) -> bool:
    return bool(rc.exists(_lock_key(path)))


# ── Nettoyage dossier session ─────────────────────────────────────────────────

def _cleanup_session_if_empty(session_folder: str | None) -> None:
    """
    Supprime le dossier session s'il n'existe plus ou s'il est vide.
    Appel silencieux — ne lève jamais d'exception.
    """
    if not session_folder:
        return
    try:
        p = Path(session_folder)
        if p.is_dir() and not any(p.iterdir()):
            shutil.rmtree(session_folder, ignore_errors=True)
            logger.info("[CLEANUP] Dossier session supprimé : %s", session_folder)
    except Exception as exc:
        logger.warning("[CLEANUP] Erreur suppression %s : %s", session_folder, exc)


# ─────────────────────────────────────────────────────────────────────────────
# TÂCHE : traitement d'un CV
# ─────────────────────────────────────────────────────────────────────────────
@shared_task(
    bind=True,
    max_retries=3,
    retry_backoff=10,
    retry_backoff_max=120,
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_cv_task(self, path: str, session_folder: str | None = None):
    rc = get_redis_connection("default")

    if not _acquire_lock(rc, path, self.request.id):
        logger.info("[CELERY] Déjà en cours, ignoré : %s", path)
        return {"status": "skipped", "path": path}

    try:
        if not os.path.isfile(path):
            logger.warning("[CELERY] Fichier introuvable : %s", path)
            return {"status": "missing", "path": path}

        from candidat.email_utils import process_cv_file

        logger.info("[CELERY] Début traitement : %s", path)
        ok, msg = process_cv_file(path)

        if not ok:
            raise RuntimeError(msg)

        logger.info("[CELERY] Succès : %s — %s", path, msg)
        return {"status": "success", "message": msg}

    except ValueError as e:
        # Données insuffisantes — pas de retry
        logger.warning("[CELERY] CV ignoré : %s", e)
        return {"status": "ignored", "reason": str(e)}

    except Exception as exc:
        logger.error("[CELERY] Erreur sur %s : %s", path, exc)
        raise self.retry(exc=exc)

    finally:
        # Toujours exécuté — lock + cleanup garantis
        _release_lock(rc, path)
        _cleanup_session_if_empty(session_folder)
        
# ─────────────────────────────────────────────────────────────────────────────
# TÂCHE : chargement des emails
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(
    bind=True,
    max_retries=2,
    retry_backoff=30,
    acks_late=True,
)
def load_emails_task(self):
    """
    Télécharge les candidatures reçues par email et dispatche leur traitement.

    Flux :
        1. email_candidature_loader() → télécharge les CVs dans candidatures_email/
        2. Scanne candidatures_email/ et dispatche process_cv_task pour chaque fichier
           non encore verrouillé (évite les doublons si la tâche tourne en parallèle).
    """
    try:
        from candidat.email_utils import email_candidature_loader, SAVE_FOLDER

        count = email_candidature_loader()
        logger.info("[EMAIL_TASK] %d email(s) traité(s)", count)

        # Dispatch des fichiers téléchargés
        rc             = get_redis_connection("default")
        email_folder   = Path(SAVE_FOLDER)
        dispatched     = 0

        if email_folder.is_dir():
            for f in email_folder.iterdir():
                if f.is_file() and f.suffix.lower() in CV_EXTENSIONS:
                    if not _is_locked(rc, str(f)):
                        process_cv_task.delay(str(f), session_folder=None)
                        dispatched += 1

        logger.info("[EMAIL_TASK] %d fichier(s) dispatchés", dispatched)
        return {"emails_loaded": count, "dispatched": dispatched}

    except Exception as exc:
        logger.exception("[EMAIL_TASK] Erreur : %s", exc)
        raise self.retry(exc=exc)


# ─────────────────────────────────────────────────────────────────────────────
# TÂCHE : watchdog
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(acks_late=True)
def watchdog_task():
    """
    Tâche de surveillance exécutée toutes les 5 minutes.

    Actions :
        1. Scanne candidatures_email/ — relance les fichiers sans verrou actif.
        2. Scanne cv_temps/*/ — relance les fichiers orphelins, supprime les
           dossiers sessions devenus vides (tous les CVs traités avec succès).

    Cas couverts :
        - Worker crashé en cours de traitement (verrou expiré, fichier restant).
        - Dispatch raté lors d'un upload (fichier présent, pas de tâche en queue).
        - Dossier session non nettoyé (ex : cleanup appelé après un crash).
    """
    rc         = get_redis_connection("default")
    media_root = Path(settings.MEDIA_ROOT)
    launched   = 0
    cleaned    = 0

    # ── 1. candidatures_email/ ────────────────────────────────────────────────
    email_folder = media_root / "candidatures_email"
    if email_folder.is_dir():
        for f in email_folder.iterdir():
            if f.is_file() and f.suffix.lower() in CV_EXTENSIONS:
                if not _is_locked(rc, str(f)):
                    process_cv_task.delay(str(f), session_folder=None)
                    launched += 1
                    logger.info("[WATCHDOG] Relance email : %s", f.name)

    # ── 2. cv_temps/*/ ────────────────────────────────────────────────────────
    cv_temps = media_root / "cv_temps"
    if cv_temps.is_dir():
        for session_dir in cv_temps.iterdir():
            if not session_dir.is_dir():
                continue

            files = [
                f for f in session_dir.iterdir()
                if f.is_file() and f.suffix.lower() in CV_EXTENSIONS
            ]

            if not files:
                # Session terminée (ou corrompue sans fichiers) → nettoyage
                shutil.rmtree(str(session_dir), ignore_errors=True)
                cleaned += 1
                logger.info("[WATCHDOG] Dossier session vide supprimé : %s", session_dir.name)
                continue

            for f in files:
                if not _is_locked(rc, str(f)):
                    process_cv_task.delay(str(f), session_folder=str(session_dir))
                    launched += 1
                    logger.info("[WATCHDOG] Relance session : %s / %s", session_dir.name, f.name)

    logger.info("[WATCHDOG] %d relancé(s), %d dossier(s) nettoyé(s)", launched, cleaned)
    return {"launched": launched, "cleaned": cleaned}