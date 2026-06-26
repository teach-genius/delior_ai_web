import logging
import os
import shutil
from pathlib import Path
import hashlib
from celery import shared_task
from django.conf import settings
from django_redis import get_redis_connection

logger = logging.getLogger(__name__)

CV_EXTENSIONS = {".pdf", ".docx", ".doc"}
LOCK_TTL      = 1800  # 30 min

def sha256_file(path):
    h = hashlib.sha256()

    with open(path,'rb') as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def _lock_key(path: str) -> str:
    return f"cv_lock:{path}"

def _dispatch_cv(rc, path: str, session_folder: str | None = None) -> bool:
    try:
        digest = sha256_file(path)
    except Exception as exc:
        logger.warning("[DISPATCH] Impossible de calculer le hash de %s : %s", path, exc)
        digest = None

    # Doublon détecté via hash
    if digest and rc.exists(f"cv_digest:{digest}"):
        logger.info("[DISPATCH] Doublon détecté (hash connu), ignoré : %s", path)
        _safe_remove(path)
        return False

    if rc.set(_lock_key(path), "queued", nx=True, ex=LOCK_TTL):
        process_cv_task.delay(path, session_folder=session_folder, digest=digest)
        logger.info("[DISPATCH] Tâche publiée : %s", path)
        return True

    logger.info("[DISPATCH] Déjà en file ou en cours, ignoré : %s", path)
    return False

def _release_lock(rc, path: str) -> None:
    rc.delete(_lock_key(path))

def _cleanup_session_if_empty(session_folder: str | None) -> None:
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
def process_cv_task(self, path: str, session_folder: str | None = None, digest: str | None = None):
    rc = get_redis_connection("default")

    try:
        if not os.path.isfile(path):
            logger.warning("[CELERY] Fichier introuvable : %s", path)
            _release_lock(rc, path)
            _cleanup_session_if_empty(session_folder)
            return {"status": "missing", "path": path}

        from candidat.email_utils import process_cv_file

        logger.info("[CELERY] Début traitement : %s", path)
        ok, msg = process_cv_file(path)

        if not ok:
            raise RuntimeError(msg)

        # Enregistrer le hash pour éviter les doublons futurs
        if digest:
            rc.set(f"cv_digest:{digest}", "1", ex=60 * 60 * 24 * 90)  # 90 jours
            logger.debug("[CELERY] Hash enregistré : %s", digest)

        logger.info("[CELERY] Succès : %s — %s", path, msg)
        _release_lock(rc, path)
        _cleanup_session_if_empty(session_folder)
        return {"status": "success", "message": msg}

    except ValueError as exc:
        logger.warning("[CELERY] CV ignoré : %s", exc)
        _release_lock(rc, path)
        _cleanup_session_if_empty(session_folder)
        return {"status": "ignored", "reason": str(exc)}

    except Exception as exc:
        logger.error("[CELERY] Erreur sur %s : %s", path, exc)
        if self.request.retries >= self.max_retries:
            logger.error("[CELERY] Retries épuisés, abandon : %s", path)
            _release_lock(rc, path)
            _cleanup_session_if_empty(session_folder)
            return {"status": "failed", "path": path}
        raise self.retry(exc=exc)

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
    rc       = get_redis_connection("default")
    lock_key = "lock:load_emails_task"

    if not rc.set(lock_key, "running", nx=True, ex=300):  # 5 min max
        logger.info("[EMAIL_TASK] Déjà en cours sur un autre worker, ignoré")
        return {"status": "skipped", "reason": "lock actif"}

    try:
        from candidat.email_utils import email_candidature_loader

        count = email_candidature_loader(rc=rc)
        logger.info("[EMAIL_TASK] %d CV téléchargé(s) et dispatché(s)", count)
        return {"emails_loaded": count}

    except Exception as exc:
        logger.exception("[EMAIL_TASK] Erreur : %s", exc)
        raise self.retry(exc=exc)

    finally:
        rc.delete(lock_key) 

# ─────────────────────────────────────────────────────────────────────────────
# TÂCHE : watchdog
# ─────────────────────────────────────────────────────────────────────────────
@shared_task(acks_late=True)
def watchdog_task():
    """
    Tâche de surveillance exécutée toutes les 5 minutes.

    Actions :
        1. Scanne candidatures_email/ — relance les fichiers orphelins (verrou expiré).
        2. Scanne cv_temps/*/ — relance les fichiers orphelins, supprime les
           dossiers sessions devenus vides.
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
                if _dispatch_cv(rc, str(f), session_folder=None):
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
                shutil.rmtree(str(session_dir), ignore_errors=True)
                cleaned += 1
                logger.info("[WATCHDOG] Dossier session vide supprimé : %s", session_dir.name)
                continue

            for f in files:
                if _dispatch_cv(rc, str(f), session_folder=str(session_dir)):
                    launched += 1
                    logger.info("[WATCHDOG] Relance session : %s / %s", session_dir.name, f.name)

    logger.info("[WATCHDOG] %d relancé(s), %d dossier(s) nettoyé(s)", launched, cleaned)
    return {"launched": launched, "cleaned": cleaned}