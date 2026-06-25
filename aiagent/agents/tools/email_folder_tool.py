from __future__ import annotations
import logging
from langchain_core.tools import tool
import requests
from aiagent.auths.auth import get_token
from aiagent.auths.config import GRAPH_BASE

logger = logging.getLogger(__name__)
MAILBOX = "recrutement@deliorgroup.com"


def _h(token): 
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def _url(*parts): 
    return f"{GRAPH_BASE}/users/{MAILBOX}/" + "/".join(parts)


# ══════════════════════════════════════════════════════════════════
# OUTIL 1 : Lister les dossiers
# ══════════════════════════════════════════════════════════════════
@tool
def lister_dossiers_email() -> str:
    """Liste tous les dossiers de la boîte Mail."""
    try:
        token = get_token()
        resp  = requests.get(_url("mailFolders"), headers=_h(token),
                             params={"$top": 50, "$select": "displayName,totalItemCount,unreadItemCount"})
        resp.raise_for_status()
        folders = resp.json().get("value", [])
        lines = [f"📁 {f['displayName']}  ({f['unreadItemCount']} non lus / {f['totalItemCount']})"
                 for f in folders]
        return "Dossiers :\n" + "\n".join(lines)
    except Exception as e:
        logger.error(f"[lister_dossiers_email] {e}")
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 3 : Lister les emails d'un dossier
# ══════════════════════════════════════════════════════════════════
@tool
def lister_emails_dossier(
    dossier: str = "inbox",
    limite: int = 10,
    non_lus_seulement: bool = False,
) -> str:
    """
    Liste les emails d'un dossier avec expéditeur, sujet et date.

    Args:
        dossier            : nom du dossier (ex: 'inbox', 'sentItems', 'Candidats 2026')
        limite             : nombre max d'emails (défaut: 10, max: 50)
        non_lus_seulement  : si True, ne retourne que les non lus
    """
    try:
        token  = get_token()
        limite = min(int(limite), 50)
        params = {
            "$top":     limite,
            "$select":  "subject,from,receivedDateTime,isRead,bodyPreview",
            "$orderby": "receivedDateTime desc",
        }
        if non_lus_seulement:
            params["$filter"] = "isRead eq false"

        resp = requests.get(_url("mailFolders", dossier, "messages"),
                            headers=_h(token), params=params)
        resp.raise_for_status()
        msgs = resp.json().get("value", [])

        if not msgs:
            return f"Aucun email dans **{dossier}**."

        lines = []
        for m in msgs:
            status = "" if m.get("isRead") else "🔵 "
            sender = m.get("from", {}).get("emailAddress", {}).get("address", "?")
            date   = m.get("receivedDateTime", "")[:10]
            subj   = m.get("subject", "(sans sujet)")
            lines.append(f"{status}📧 **{subj}**\n   De : {sender}  |  {date}")

        return f"Emails dans **{dossier}** ({len(lines)}) :\n\n" + "\n\n".join(lines)
    except Exception as e:
        logger.error(f"[lister_emails_dossier] {e}")
        return f"❌ Erreur : {e}"

# ══════════════════════════════════════════════════════════════════
# EXPORT
# ══════════════════════════════════════════════════════════════════
def get_email_folder_tools() -> list:
    return [
        lister_dossiers_email,
        lister_emails_dossier,
    ]
