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
# OUTIL 2 : Créer un dossier
# ══════════════════════════════════════════════════════════════════

@tool
def creer_dossier_email(nom_dossier: str) -> str:
    """
    Crée un nouveau dossier dans la boîte Mail.

    Args:
        nom_dossier : nom du dossier à créer (ex: 'Candidats 2026')
    """
    try:
        token = get_token()
        resp  = requests.post(_url("mailFolders"), headers=_h(token),
                              json={"displayName": nom_dossier})
        if resp.status_code == 409:
            return f"ℹ️ Le dossier **{nom_dossier}** existe déjà."
        resp.raise_for_status()
        return f"✅ Dossier **{nom_dossier}** créé."
    except Exception as e:
        logger.error(f"[creer_dossier_email] {e}")
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
# OUTIL 4 : Déplacer des emails
# ══════════════════════════════════════════════════════════════════

def _resolve_folder_id(token: str, nom: str) -> str | None:
    """Résout un nom de dossier en ID Graph (supporte inbox, sentItems, etc.)."""
    # Dossiers bien connus : pas besoin d'ID
    bien_connus = {"inbox", "sentitems", "drafts", "deleteditems", "junkemail"}
    if nom.lower() in bien_connus:
        return nom
    resp = requests.get(_url("mailFolders"), headers=_h(token),
                        params={"$top": 100, "$select": "id,displayName"})
    resp.raise_for_status()
    for f in resp.json().get("value", []):
        if f["displayName"].lower() == nom.lower():
            return f["id"]
    return None

@tool
def deplacer_emails(
    dossier_source: str,
    dossier_destination: str,
    filtre_sujet: str = "",
    filtre_expediteur: str = "",
    non_lus_seulement: bool = False,
    limite: int = 20,
) -> str:
    """
    Déplace des emails d'un dossier vers un autre.

    Args:
        dossier_source      : dossier d'origine (ex: 'inbox')
        dossier_destination : dossier de destination (ex: 'Candidats 2026')
        filtre_sujet        : mot-clé dans le sujet (optionnel)
        filtre_expediteur   : adresse email de l'expéditeur (optionnel)
        non_lus_seulement   : si True, ne déplace que les non lus
        limite              : nombre max d'emails à déplacer (défaut: 20)
    """
    try:
        token  = get_token()
        limite = min(int(limite), 50)

        dest_id = _resolve_folder_id(token, dossier_destination)
        if not dest_id:
            # Créer le dossier s'il n'existe pas
            r = requests.post(_url("mailFolders"), headers=_h(token),
                              json={"displayName": dossier_destination})
            r.raise_for_status()
            dest_id = r.json()["id"]

        # Construction du filtre OData
        filtres = []
        if non_lus_seulement:
            filtres.append("isRead eq false")
        if filtre_expediteur:
            filtres.append(f"from/emailAddress/address eq '{filtre_expediteur}'")
        if filtre_sujet:
            filtres.append(f"contains(subject, '{filtre_sujet}')")

        params = {
            "$top":    limite,
            "$select": "id,subject,from,isRead",
        }
        if filtres:
            params["$filter"] = " and ".join(filtres)

        resp = requests.get(_url("mailFolders", dossier_source, "messages"),
                            headers=_h(token), params=params)
        resp.raise_for_status()
        msgs = resp.json().get("value", [])

        if not msgs:
            return f"Aucun email trouvé dans **{dossier_source}** avec ces critères."

        deplaces = 0
        for m in msgs:
            r = requests.post(_url("messages", m["id"], "move"),
                              headers=_h(token), json={"destinationId": dest_id})
            if r.ok:
                deplaces += 1

        return (f"✅ {deplaces} email(s) déplacé(s) de **{dossier_source}** "
                f"vers **{dossier_destination}**.")
    except Exception as e:
        logger.error(f"[deplacer_emails] {e}")
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# EXPORT
# ══════════════════════════════════════════════════════════════════

def get_email_folder_tools() -> list:
    return [
        lister_dossiers_email,
        creer_dossier_email,
        lister_emails_dossier,
        deplacer_emails,
    ]
