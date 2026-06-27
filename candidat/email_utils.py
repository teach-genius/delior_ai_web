import json
import logging
import os
import re
import time

import requests
from dotenv import load_dotenv
from django.conf import settings

from candidat.utils import save_candidat_from_cv_path
from aiagent.auths.auth import get_token
from aiagent.auths.config import GRAPH_BASE, CACHE_FILE

load_dotenv()

logger = logging.getLogger(__name__)

SAVE_FOLDER     = os.path.join(settings.MEDIA_ROOT, "candidatures_email")
CV_EXTENSIONS   = re.compile(r'\.(pdf|docx|doc)$', re.IGNORECASE)
SUBJECT_FILTER  = re.compile(r"candidature", re.IGNORECASE)
CV_NAME_RE      = re.compile(r'\bcv\b', re.IGNORECASE)
DELTA_FILE      = CACHE_FILE
FIRST_RUN_LIMIT = 24

MAILBOX = "recrutement@deliorgroup.com"


# ── Helpers HTTP ──────────────────────────────────────────────────────────────

def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _url(*parts: str) -> str:
    return f"{GRAPH_BASE}/users/{MAILBOX}/" + "/".join(parts)


def _get(token: str, url: str, params: dict = None) -> dict:
    for attempt in range(5):
        resp = requests.get(url, headers=_h(token), params=params)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 10))
            logger.warning("[HTTP] Throttled — attente %ds (tentative %d)", wait, attempt + 1)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("Trop de tentatives — API Graph non disponible")


# ── Delta token ────────────────────────────────────────────────────────────────

def _load_delta() -> str | None:
    if not os.path.exists(DELTA_FILE):
        return None
    try:
        return json.loads(open(DELTA_FILE).read()).get(MAILBOX)
    except Exception as exc:
        logger.warning("[DELTA] Lecture échouée : %s", exc)
        return None


def _save_delta(delta_token: str) -> None:
    data = {}
    if os.path.exists(DELTA_FILE):
        try:
            data = json.loads(open(DELTA_FILE).read())
        except Exception:
            pass
    data[MAILBOX] = delta_token
    try:
        with open(DELTA_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        logger.warning("[DELTA] Sauvegarde échouée : %s", exc)


def _init_delta_token(token: str) -> None:
    """Initialise le delta token à la position actuelle sans lire de messages."""
    try:
        data = _get(token, _url("mailFolders", "inbox", "messages", "delta"),
                    {"$select": "id", "$deltatoken": "latest"})
        link = data.get("@odata.deltaLink", "")
        if link:
            _save_delta(link.split("$deltatoken=")[-1])
            logger.info("[DELTA] Token initialisé à la position actuelle")
    except Exception as exc:
        logger.warning("[DELTA] Impossible d'initialiser le delta token : %s", exc)


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
        logger.warning("[SELECT_CV] Erreur : %s", exc)
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


# ── Traitement d'un message ───────────────────────────────────────────────────

def _process_message(token: str, msg: dict) -> bool:
    """
    Pour un message donné :
    - Filtre sur le sujet
    - Télécharge le CV (lecture seule — pas de marquage ni d'archivage)
    - Sauvegarde sur disque avec déduplication par nom de fichier
    Retourne True si un CV a été sauvegardé.
    """
    msg_id  = msg["id"]
    subject = msg.get("subject") or ""

    if not SUBJECT_FILTER.search(subject):
        return False

    logger.info("[EMAIL] Traitement : %s", subject)

    # Pièces jointes (metadata uniquement)
    try:
        att_data        = _get(token, _url("messages", msg_id, "attachments"),
                               {"$select": "id,name,contentType,size"})
        raw_attachments = att_data.get("value", [])
    except Exception as exc:
        logger.exception("[EMAIL] Erreur pièces jointes msg %s : %s", msg_id, exc)
        return False

    attachments = [a for a in raw_attachments if CV_EXTENSIONS.search(a.get("name", ""))]
    cv_att      = select_cv_attachment(attachments)

    if cv_att is None:
        logger.info("[EMAIL] Mail %s ignoré — aucune pièce jointe valide", msg_id)
        return False

    skipped = [a["name"] for a in attachments if a is not cv_att]
    if skipped:
        logger.info("[EMAIL] Pièce(s) ignorée(s) : %s", skipped)

    # Déduplication : fichier déjà présent sur disque → on ignore
    filename = cv_att["name"]
    filepath = os.path.join(SAVE_FOLDER, filename)

    if os.path.exists(filepath):
        logger.info("[EMAIL] CV déjà présent, ignoré : %s", filepath)
        return False

    # Téléchargement
    try:
        dl_resp = requests.get(
            _url("messages", msg_id, "attachments", cv_att["id"], "$value"),
            headers=_h(token),
        )
        dl_resp.raise_for_status()
        content_bytes: bytes = dl_resp.content
    except Exception as exc:
        logger.error("[EMAIL] Téléchargement échoué %s : %s", filename, exc)
        return False

    # Sauvegarde
    try:
        with open(filepath, "wb") as f:
            f.write(content_bytes)
        logger.info("[EMAIL] CV sauvegardé : %s", filepath)
        return True
    except OSError as exc:
        logger.error("[EMAIL] Erreur écriture %s : %s", filename, exc)
        return False
    except Exception as exc:
        logger.exception("[EMAIL] Erreur inattendue sauvegarde msg %s : %s", msg_id, exc)
        return False


# ── Loader principal ──────────────────────────────────────────────────────────

def email_candidature_loader() -> int:
    """
    Synchronise la boîte recrutement en lecture seule et télécharge les CVs.

    Premier run  : traite les FIRST_RUN_LIMIT derniers emails,
                   puis initialise le delta token à la position actuelle.
    Runs suivants : traite uniquement les nouveaux emails depuis le dernier run.

    Déduplication : un CV déjà présent sur disque est ignoré (pas d'écrasement).
    Aucune écriture sur la boîte mail (pas de marquage lu, pas d'archivage).

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

    delta_token = _load_delta()
    cv_count    = 0

    if delta_token:
        # ── Runs suivants : delta query ───────────────────────────────────────
        logger.info("[EMAIL] Reprise depuis le dernier sync")
        url    = _url("mailFolders", "inbox", "messages", "delta")
        params = {"$select": "id,subject,hasAttachments", "$deltatoken": delta_token}
        page   = 0

        while url:
            page += 1
            try:
                data = _get(token, url, params)
            except Exception as exc:
                logger.exception("[EMAIL] Erreur page %d : %s", page, exc)
                break

            messages   = [m for m in data.get("value", []) if not m.get("@removed")]
            next_url   = data.get("@odata.nextLink")
            delta_link = data.get("@odata.deltaLink", "")

            logger.info("[EMAIL] Page %d — %d message(s)", page, len(messages))

            for msg in messages:
                if msg.get("hasAttachments") and _process_message(token, msg):
                    cv_count += 1

            if delta_link:
                _save_delta(delta_link.split("$deltatoken=")[-1])

            url    = next_url
            params = None

    else:
        # ── Premier run : les FIRST_RUN_LIMIT derniers ────────────────────────
        logger.info("[EMAIL] Premier run — %d derniers emails", FIRST_RUN_LIMIT)
        try:
            data = _get(token, _url("mailFolders", "inbox", "messages"), {
                "$top":     FIRST_RUN_LIMIT,
                "$orderby": "receivedDateTime desc",
                "$select":  "id,subject,hasAttachments",
            })
            messages = data.get("value", [])
        except Exception as exc:
            logger.exception("[EMAIL] Erreur récupération messages : %s", exc)
            return 0

        logger.info("[EMAIL] %d message(s) récupéré(s)", len(messages))

        for msg in messages:
            if msg.get("hasAttachments") and _process_message(token, msg):
                cv_count += 1

        # Initialiser le delta token pour les prochains runs
        _init_delta_token(token)

    logger.info("[EMAIL] Total CV téléchargés : %d", cv_count)
    return cv_count