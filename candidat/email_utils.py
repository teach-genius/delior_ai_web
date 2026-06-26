import json
import logging
import os
import re
import time

import requests
from dotenv import load_dotenv
from django.conf import settings

from aiagent.auths.auth import get_token
from aiagent.auths.config import CACHE_FILE
from aiagent.auths.config import GRAPH_BASE, SHARED_MAILBOX

load_dotenv()

logger = logging.getLogger(__name__)

SAVE_FOLDER     = os.path.join(settings.MEDIA_ROOT, "cv_temps")
CV_EXTENSIONS   = re.compile(r'\.(pdf|docx|doc)$', re.IGNORECASE)
SUBJECT_FILTER  = re.compile(r"candidature", re.IGNORECASE)
CV_NAME_RE      = re.compile(r'\bcv\b', re.IGNORECASE)
DELTA_FILE      = CACHE_FILE
MAILBOX         = SHARED_MAILBOX
FIRST_RUN_LIMIT = 24


# ── HTTP ───────────────────────────────────────────────────────────────────────

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


# ── Attachments ────────────────────────────────────────────────────────────────

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


# ── Message processing ─────────────────────────────────────────────────────────

def _process_message(token: str, msg: dict) -> bool:
    msg_id  = msg["id"]
    subject = msg.get("subject") or ""

    if not SUBJECT_FILTER.search(subject):
        return False

    logger.info("[EMAIL] Traitement : %s", subject)

    # Pièces jointes
    try:
        att_data        = _get(token, _url("messages", msg_id, "attachments"),
                               params={"$select": "id,name,contentType,size"})
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
    except Exception as exc:
        logger.error("[EMAIL] Téléchargement échoué %s : %s", filename, exc)
        return False

    try:
        with open(filepath, "wb") as f:
            f.write(dl_resp.content)
        logger.info("[EMAIL] CV sauvegardé : %s", filepath)
    except OSError as exc:
        logger.error("[EMAIL] Erreur écriture %s : %s", filename, exc)
        return False

    return True


# ── Entry point ────────────────────────────────────────────────────────────────

def email_candidature_loader() -> int:
    """
    Télécharge les CVs (pdf/docx/doc) des emails dont le sujet contient "candidature".
    Premier run  : traite les FIRST_RUN_LIMIT derniers emails.
    Runs suivants : traite uniquement les nouveaux emails depuis le dernier run.
    Retourne le nombre de CVs téléchargés.
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
    params      = {"$select": "id,subject,hasAttachments"}

    if delta_token:
        params["$deltatoken"] = delta_token
        url = _url("mailFolders", "inbox", "messages", "delta")
        logger.info("[EMAIL] Reprise depuis le dernier sync")
    else:
        params["$top"]     = FIRST_RUN_LIMIT
        params["$orderby"] = "receivedDateTime desc"
        url = _url("mailFolders", "inbox", "messages")
        logger.info("[EMAIL] Premier run — %d derniers emails", FIRST_RUN_LIMIT)

    cv_count = 0
    page     = 0

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
        elif not delta_token and not next_url:
            # Premier run : initialise le delta token à la position actuelle
            try:
                init_data = _get(token, _url("mailFolders", "inbox", "messages", "delta"),
                                 {"$select": "id", "$deltatoken": "latest"})
                init_link = init_data.get("@odata.deltaLink", "")
                if init_link:
                    _save_delta(init_link.split("$deltatoken=")[-1])
                    logger.info("[EMAIL] Delta token initialisé")
            except Exception as exc:
                logger.warning("[EMAIL] Impossible d'initialiser le delta token : %s", exc)

        url    = next_url
        params = None

    logger.info("[EMAIL] Total CVs téléchargés : %d", cv_count)
    return cv_count