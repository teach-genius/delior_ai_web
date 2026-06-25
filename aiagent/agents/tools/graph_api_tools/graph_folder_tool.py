from __future__ import annotations

import logging
import re
import requests
from langchain_core.tools import tool

from aiagent.auths.graph_api_auth import get_graph_client

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# HELPERS INTERNES
# ══════════════════════════════════════════════════════════════════

def _mailbox() -> str:
    import os
    return os.getenv("GRAPH_SENDER_EMAIL", "")


def _get_folder_id(nom: str) -> str | None:
    client = get_graph_client()
    resp = requests.get(
        f"{client['base_url']}/users/{_mailbox()}/mailFolders?$top=50",
        headers=client["headers"],
    )
    if resp.status_code != 200:
        return None
    for f in resp.json().get("value", []):
        if f["displayName"].lower() == nom.lower():
            return f["id"]
    return None


def _get_or_create_folder_id(nom: str) -> tuple[str, bool]:
    fid = _get_folder_id(nom)
    if fid:
        return fid, False
    client = get_graph_client()
    resp = requests.post(
        f"{client['base_url']}/users/{_mailbox()}/mailFolders",
        headers=client["headers"],
        json={"displayName": nom},
    )
    resp.raise_for_status()
    return resp.json()["id"], True


# ══════════════════════════════════════════════════════════════════
# OUTIL 1 : Lister les dossiers
# ══════════════════════════════════════════════════════════════════

@tool
def lister_dossiers_email() -> str:
    """Liste tous les dossiers de la boîte mail Office 365 du RH."""
    try:
        client = get_graph_client()
        resp = requests.get(
            f"{client['base_url']}/users/{_mailbox()}/mailFolders?$top=50",
            headers=client["headers"],
        )
        if resp.status_code != 200:
            return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

        dossiers = resp.json().get("value", [])
        if not dossiers:
            return "Aucun dossier trouvé."

        lignes = []
        for d in sorted(dossiers, key=lambda x: x["displayName"]):
            total  = d.get("totalItemCount", 0)
            unread = d.get("unreadItemCount", 0)
            lignes.append(f"📁 {d['displayName']}  ({total} emails, {unread} non lus)")

        return "Dossiers de la boîte mail :\n" + "\n".join(lignes)

    except Exception as e:
        logger.error("[lister_dossiers_email] %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 2 : Créer un dossier
# ══════════════════════════════════════════════════════════════════

@tool
def creer_dossier_email(nom_dossier: str) -> str:
    """
    Crée un nouveau dossier dans la boîte mail Office 365 du RH.
    Ne fait rien si le dossier existe déjà.

    Args:
        nom_dossier : nom du dossier à créer (ex: 'Convoqués', 'Refus 2026')
    """
    try:
        fid = _get_folder_id(nom_dossier)
        if fid:
            return f"ℹ️ Le dossier **{nom_dossier}** existe déjà."

        client = get_graph_client()
        resp = requests.post(
            f"{client['base_url']}/users/{_mailbox()}/mailFolders",
            headers=client["headers"],
            json={"displayName": nom_dossier},
        )
        if resp.status_code == 201:
            return f"✅ Dossier **{nom_dossier}** créé avec succès."
        return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

    except Exception as e:
        logger.error("[creer_dossier_email] %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 3 : Lister les emails d'un dossier
# ══════════════════════════════════════════════════════════════════

@tool
def lister_emails_dossier(
    dossier: str = "Inbox",
    limite: int = 10,
    filtre: str = "",
) -> str:
    """
    Liste les emails d'un dossier avec expéditeur, sujet et date.

    Args:
        dossier : nom du dossier (défaut: 'Inbox')
        limite  : nombre max d'emails à afficher (défaut: 10, max: 50)
        filtre  : filtre OData optionnel, ex:
                  - "isRead eq false"
                  - "from/emailAddress/address eq 'ahmed@gmail.com'"
                  - "contains(subject,'convocation')"
    """
    try:
        limite = min(int(limite), 50)
        fid = _get_folder_id(dossier)
        if not fid:
            return f"❌ Dossier introuvable : {dossier}"

        client = get_graph_client()
        params: dict = {
            "$top":     limite,
            "$select":  "subject,from,receivedDateTime,isRead",
            "$orderby": "receivedDateTime desc",
        }
        if filtre:
            params["$filter"] = filtre

        resp = requests.get(
            f"{client['base_url']}/users/{_mailbox()}/mailFolders/{fid}/messages",
            headers=client["headers"],
            params=params,
        )
        if resp.status_code != 200:
            return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

        emails = resp.json().get("value", [])
        if not emails:
            return f"Aucun email trouvé dans **{dossier}**."

        lignes = []
        for m in emails:
            sujet = m.get("subject", "(sans sujet)")
          
            exp   = m.get("from", {}).get("emailAddress", {})
            nom   = exp.get("name", "?")
            addr  = exp.get("address", "?")
            date  = m.get("receivedDateTime", "?")[:16].replace("T", " ")
            lu    = "✅" if m.get("isRead") else "🔵"
            lignes.append(f"{lu} **{sujet}**\n   De : {nom} <{addr}>\n   Date : {date}")

        return f"Emails dans **{dossier}** ({len(lignes)}) :\n\n" + "\n\n".join(lignes)

    except Exception as e:
        logger.error("[lister_emails_dossier] %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 4 : Déplacer des emails vers un dossier
# ══════════════════════════════════════════════════════════════════

@tool
def deplacer_emails(
    dossier_source: str,
    dossier_destination: str,
    filtre: str,
    limite: int = 20,
) -> str:
    """
    Déplace des emails d'un dossier source vers un dossier destination.
    Crée le dossier destination s'il n'existe pas.

    Args:
        dossier_source      : dossier d'origine (ex: 'Inbox')
        dossier_destination : dossier de destination (ex: 'Convoqués')
        filtre              : filtre OData pour cibler les emails. Exemples :
                              - "from/emailAddress/address eq 'ahmed@gmail.com'"
                              - "contains(subject,'convocation')"
                              - "isRead eq false"
        limite              : nombre maximum d'emails à déplacer (défaut: 20)
    """
    try:
        limite = min(int(limite), 50)

        src_id = _get_folder_id(dossier_source)
        if not src_id:
            return f"❌ Dossier source introuvable : {dossier_source}"

        dst_id, créé = _get_or_create_folder_id(dossier_destination)
        if créé:
            logger.info("[deplacer_emails] Dossier créé : %s", dossier_destination)

        client = get_graph_client()
        resp = requests.get(
            f"{client['base_url']}/users/{_mailbox()}/mailFolders/{src_id}/messages",
            headers=client["headers"],
            params={"$top": limite, "$select": "id,subject", "$filter": filtre},
        )
        if resp.status_code != 200:
            return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

        emails = resp.json().get("value", [])
        if not emails:
            return f"Aucun email trouvé dans **{dossier_source}** avec le filtre '{filtre}'."

        déplacés = 0
        for m in emails:
            r = requests.post(
                f"{client['base_url']}/users/{_mailbox()}/messages/{m['id']}/move",
                headers=client["headers"],
                json={"destinationId": dst_id},
            )
            if r.status_code == 201:
                déplacés += 1
            else:
                logger.warning("[deplacer_emails] Échec email %s : %s", m["id"], r.text)

        return (
            f"✅ {déplacés} email(s) déplacé(s) de **{dossier_source}** "
            f"vers **{dossier_destination}**."
        )

    except Exception as e:
        logger.error("[deplacer_emails] %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 5 : Trier automatiquement la boîte de réception
# ══════════════════════════════════════════════════════════════════

@tool
def trier_boite_reception(regles: str) -> str:
    """
    Trie automatiquement la boîte de réception selon des règles en langage naturel.
    Crée les dossiers nécessaires et déplace les emails.

    Args:
        regles : description des règles de tri en français. Exemples :
                 - "Déplace les emails de ahmed@gmail.com vers Candidats"
                 - "Mets les emails avec 'convocation' dans le sujet vers Convocations"
                 - "Déplace les emails non lus vers A traiter"
    """
    regles_lower = regles.lower()
    resultats: list[str] = []

    emails_trouves   = re.findall(r"[\w\.-]+@[\w\.-]+\.\w+", regles)
    dossiers_trouves = re.findall(r"vers\s+([^\s,\.]+(?:/[^\s,\.]+)?)", regles, re.IGNORECASE)

    if emails_trouves and dossiers_trouves:
        for exp, dest in zip(emails_trouves, dossiers_trouves):
            res = deplacer_emails.invoke({
                "dossier_source":      "Inbox",
                "dossier_destination": dest,
                "filtre":              f"from/emailAddress/address eq '{exp}'",
                "limite":              50,
            })
            resultats.append(res)

    elif "sujet" in regles_lower and dossiers_trouves:
        mots = re.findall(r"['\"](.+?)['\"]", regles)
        if mots and dossiers_trouves:
            res = deplacer_emails.invoke({
                "dossier_source":      "Inbox",
                "dossier_destination": dossiers_trouves[0],
                "filtre":              f"contains(subject,'{mots[0]}')",
                "limite":              50,
            })
            resultats.append(res)

    elif "non lu" in regles_lower and dossiers_trouves:
        res = deplacer_emails.invoke({
            "dossier_source":      "Inbox",
            "dossier_destination": dossiers_trouves[0],
            "filtre":              "isRead eq false",
            "limite":              50,
        })
        resultats.append(res)

    else:
        return (
            "Je n'ai pas pu interpréter la règle. Essaie par exemple :\n"
            "- 'Déplace les emails de ahmed@gmail.com vers Candidats'\n"
            "- 'Mets les emails avec \"convocation\" dans le sujet vers Convocations'\n"
            "- 'Déplace les emails non lus vers A traiter'"
        )

    return "\n\n".join(resultats) if resultats else "Aucune action effectuée."


# ══════════════════════════════════════════════════════════════════
# EXPORT
# ══════════════════════════════════════════════════════════════════

def get_email_folder_tools() -> list:
    return [
        lister_dossiers_email,
        creer_dossier_email,
        lister_emails_dossier,
        deplacer_emails,
        trier_boite_reception,
    ]