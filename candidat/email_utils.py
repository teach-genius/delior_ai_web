import logging
import os
import re

import requests
from dotenv import load_dotenv
from django.conf import settings

from candidat.utils import save_candidat_from_cv_path

from aiagent.auths.auth import get_token
from aiagent.auths.config import GRAPH_BASE,SHARED_MAILBOX

load_dotenv()

logger = logging.getLogger(__name__)

SAVE_FOLDER     = os.path.join(settings.MEDIA_ROOT, "candidatures_email")        
CV_EXTENSIONS   = re.compile(r'\.(pdf|docx|doc)$', re.IGNORECASE)
SUBJECT_FILTER  = re.compile(r"candidature", re.IGNORECASE)
CV_NAME_RE      = re.compile(r'\bcv\b', re.IGNORECASE)

MAILBOX = SHARED_MAILBOX

def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}

def _url(*parts: str) -> str:
    return f"{GRAPH_BASE}/users/{MAILBOX}/" + "/".join(parts)

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

def _safe_remove(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
            logger.debug("Fichier supprimé : %s", path)
    except Exception as exc:
        logger.warning("Impossible de supprimer %s : %s", path, exc)

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
   
def email_candidature_loader() -> int:
    """
    Lit les emails non lus de la boîte recrutement via Graph API,
    télécharge les pièces jointes CV (pdf/docx/doc) des mails dont
    le sujet contient « candidature », puis marque le mail comme lu.

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

        if not SUBJECT_FILTER.search(subject):
            continue
        logger.info("[EMAIL] Traitement : %s", subject)

        try:
            patch_resp = requests.patch(
                _url("messages", msg_id),
                headers={**_h(token), "Content-Type": "application/json"},
                json={"isRead": True},
            )
            if not patch_resp.ok:
                logger.warning(
                    "[EMAIL] Échec marquage lu pour %s : %s — mail ignoré",
                    msg_id, patch_resp.text,
                )
                continue 
            logger.info("[EMAIL] Mail %s marqué comme lu", msg_id)
        except Exception as exc:
            logger.warning("[EMAIL] Erreur marquage lu mail %s : %s — mail ignoré", msg_id, exc)
            continue

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

        filename = cv_att["name"]
        base, ext = os.path.splitext(filename)
        filepath = os.path.join(SAVE_FOLDER, filename)

        if os.path.exists(filepath):
            logger.info("[EMAIL] CV déjà présent, ignoré : %s", filepath)
            continue

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

        try:
            with open(filepath, "wb") as f:
                f.write(content_bytes)
            logger.info("[EMAIL] CV sauvegardé : %s", filepath)
            cv_count += 1
        except OSError as exc:
            logger.error("[EMAIL] Erreur écriture %s : %s", filename, exc)
            continue

    logger.info("[EMAIL] Total CV téléchargés : %d", cv_count)
    return cv_count