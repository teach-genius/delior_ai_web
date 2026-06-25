import logging
import os
import re

import requests
from dotenv import load_dotenv
from django.conf import settings

from .models import Configuration
from candidat.utils import save_candidat_from_cv_path

# ── Auth (même pattern que tool) ──────────────────────────────────────────────
from aiagent.auths.auth import get_token
from aiagent.auths.config import GRAPH_BASE

load_dotenv()

logger = logging.getLogger(__name__)

SAVE_FOLDER     = os.path.join(settings.MEDIA_ROOT, "candidatures_email")
ARCHIVE_FOLDER  = "Archive"          # nom du dossier Mail cible
CV_EXTENSIONS   = re.compile(r'\.(pdf|docx|doc)$', re.IGNORECASE)
SUBJECT_FILTER  = re.compile(r"candidature", re.IGNORECASE)
CV_NAME_RE      = re.compile(r'\bcv\b', re.IGNORECASE)

MAILBOX = "recrutement@deliorgroup.com"


# ── Helpers HTTP ──────────────────────────────────────────────────────────────

def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _url(*parts: str) -> str:
    return f"{GRAPH_BASE}/users/{MAILBOX}/" + "/".join(parts)


# ── Dossier Archive ───────────────────────────────────────────────────────────

def _resolve_or_create_folder(token: str, display_name: str) -> str:
    """Retourne l'ID Graph du dossier (le crée s'il n'existe pas)."""
    resp = requests.get(
        _url("mailFolders"),
        headers=_h(token),
        params={"$top": 100, "$select": "id,displayName"},
    )
    resp.raise_for_status()
    for f in resp.json().get("value", []):
        if f["displayName"].lower() == display_name.lower():
            return f["id"]

    # Créer le dossier
    resp = requests.post(
        _url("mailFolders"),
        headers={**_h(token), "Content-Type": "application/json"},
        json={"displayName": display_name},
    )
    resp.raise_for_status()
    return resp.json()["id"]


# ── Sélection de la pièce jointe CV ──────────────────────────────────────────

def select_cv_attachment(attachments: list[dict]) -> dict | None:
    try:
        if not attachments:
            return None
        if len(attachments) == 1:
            return attachments[0]
        for att in attachments:
            if CV_NAME_RE.search(att.get("name", "")):
                return att
        return attachments[0]
    except Exception as exc:
        logger.warning("[SELECT_CV] Erreur sélection pièce jointe : %s", exc)
        return None


# ── Suppression sécurisée ─────────────────────────────────────────────────────

def _safe_remove(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
            logger.debug("Fichier supprimé : %s", path)
    except Exception as exc:
        logger.warning("Impossible de supprimer %s : %s", path, exc)


# ── Traitement d'un fichier CV ────────────────────────────────────────────────

def process_cv_file(file_path: str) -> tuple[bool, str]:
    if not file_path or not os.path.isfile(file_path):
        logger.error("[PROCESS] Fichier introuvable : %s", file_path)
        return False, "Fichier introuvable"

    try:
        logger.info("[PROCESS] Traitement : %s", file_path)
        result = save_candidat_from_cv_path(file_path)

        if not result:
            raise ValueError("save_candidat_from_cv_path a retourné un résultat vide.")

        candidat, created = result
        status = "Créé" if created else "Mis à jour"
        logger.info("[PROCESS] %s : %s", status, candidat.nom_complet)
        _safe_remove(file_path)
        return True, f"{status} : {candidat.nom_complet}"

    except (ValueError, FileNotFoundError) as exc:
        logger.warning("[PROCESS] CV invalide — %s : %s", file_path, exc)
        _safe_remove(file_path)
        return False, str(exc)

    except Exception as exc:
        logger.exception("[PROCESS] Erreur inattendue sur %s : %s", file_path, exc)
        return False, str(exc)


# ── Loader principal ──────────────────────────────────────────────────────────

def email_candidature_loader() -> int:
    """
    Lit les emails non lus de la boîte recrutement via Graph API,
    télécharge les pièces jointes CV (pdf/docx/doc) des mails dont
    le sujet contient « candidature », puis déplace le mail vers Archive.

    Retourne le nombre de CV téléchargés.
    """
    try:
        os.makedirs(SAVE_FOLDER, exist_ok=True)
    except Exception as exc:
        raise RuntimeError(f"Impossible de créer le dossier de sauvegarde : {exc}") from exc

    try:
        token = get_token()
    except Exception as exc:
        raise RuntimeError(f"Impossible d'obtenir le token Graph : {exc}") from exc

    # Résoudre (ou créer) le dossier Archive une seule fois
    try:
        archive_id = _resolve_or_create_folder(token, ARCHIVE_FOLDER)
    except Exception as exc:
        raise RuntimeError(f"Impossible de résoudre le dossier Archive : {exc}") from exc

    # ── Récupérer les messages non lus de l'inbox ─────────────────────────────
    try:
        resp = requests.get(
            _url("mailFolders", "inbox", "messages"),
            headers=_h(token),
            params={
                "$filter": "isRead eq false",
                "$top": 50,
                "$select": "id,subject,isRead",
            },
        )
        resp.raise_for_status()
        messages = resp.json().get("value", [])
    except Exception as exc:
        logger.exception("[EMAIL] Erreur récupération messages : %s", exc)
        return 0

    logger.info("[EMAIL] %d mail(s) non lu(s)", len(messages))
    cv_count = 0

    for msg in messages:
        msg_id  = msg["id"]
        subject = msg.get("subject") or ""
        saved_files: list[str] = []

        # ── Filtre sujet ──────────────────────────────────────────────────────
        if not SUBJECT_FILTER.search(subject):
            continue
        logger.info("[EMAIL] Traitement : %s", subject)

        # ── Récupérer les pièces jointes (metadata seulement) ────────────────
        # Graph rejette contentBytes dans $select → contenu récupéré via /$value
        try:
            att_resp = requests.get(
                _url("messages", msg_id, "attachments"),
                headers=_h(token),
                params={"$select": "id,name,contentType,size"},
            )
            att_resp.raise_for_status()
            raw_attachments = att_resp.json().get("value", [])
        except Exception as exc:
            logger.exception("[EMAIL] Erreur récupération pièces jointes msg %s : %s", msg_id, exc)
            continue

        # Filtrer sur l'extension
        attachments = [
            a for a in raw_attachments
            if CV_EXTENSIONS.search(a.get("name", ""))
        ]

        cv_att = select_cv_attachment(attachments)
        if cv_att is None:
            logger.info("[EMAIL] Mail %s ignoré — aucune pièce jointe valide", msg_id)
            continue

        skipped = [a["name"] for a in attachments if a is not cv_att]
        if skipped:
            logger.info("[EMAIL] Pièce(s) ignorée(s) : %s", skipped)

        # ── Télécharger le contenu via /$value (toutes tailles) ──────────────
        try:
            dl_resp = requests.get(
                _url("messages", msg_id, "attachments", cv_att["id"], "$value"),
                headers=_h(token),
            )
            dl_resp.raise_for_status()
            content_bytes: bytes = dl_resp.content
        except Exception as exc:
            logger.error("[EMAIL] Téléchargement pièce jointe échoué %s : %s", cv_att["name"], exc)
            continue

        # ── Sauvegarder sur disque ────────────────────────────────────────────
        filename = cv_att["name"]
        filepath = os.path.join(SAVE_FOLDER, filename)

        if os.path.exists(filepath):
            base, ext = os.path.splitext(filename)
            counter = 1
            while os.path.exists(filepath):
                filepath = os.path.join(SAVE_FOLDER, f"{base}_{counter}{ext}")
                counter += 1

        try:
            with open(filepath, "wb") as f:
                f.write(content_bytes)
            saved_files.append(filepath)
            logger.info("[EMAIL] CV sauvegardé : %s", filepath)
            cv_count += 1
        except OSError as exc:
            logger.error("[EMAIL] Erreur écriture %s : %s", filename, exc)
            continue

        # ── Marquer comme lu + déplacer vers Archive ──────────────────────────
        try:
            requests.patch(
                _url("messages", msg_id),
                headers={**_h(token), "Content-Type": "application/json"},
                json={"isRead": True},
            )
            move_resp = requests.post(
                _url("messages", msg_id, "move"),
                headers={**_h(token), "Content-Type": "application/json"},
                json={"destinationId": archive_id},
            )
            if move_resp.ok:
                logger.info("[EMAIL] Mail %s archivé", msg_id)
            else:
                logger.warning(
                    "[EMAIL] Déplacement vers Archive échoué pour %s : %s",
                    msg_id, move_resp.text,
                )
        except Exception as exc:
            logger.warning("[EMAIL] Erreur archivage mail %s : %s", msg_id, exc)

    logger.info("[EMAIL] Total CV téléchargés : %d", cv_count)
    return cv_count
