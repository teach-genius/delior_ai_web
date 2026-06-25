from __future__ import annotations

import logging
import os

import requests
from dotenv import load_dotenv
from langchain_core.tools import tool

from aiagent.auths.graph_api_auth import get_graph_client

load_dotenv()
logger = logging.getLogger(__name__)

COMPANY_NAME = "Delior Group"


def _user() -> str:
    return os.getenv("GRAPH_SENDER_EMAIL", "")


# ══════════════════════════════════════════════════════════════════
# HELPERS INTERNES
# ══════════════════════════════════════════════════════════════════

def _get_team_id(nom_team: str) -> str | None:
    client = get_graph_client()
    resp = requests.get(
        f"{client['base_url']}/groups?$filter=displayName eq '{nom_team}' and resourceProvisioningOptions/Any(x:x eq 'Team')",
        headers=client["headers"],
    )
    if resp.status_code == 200:
        items = resp.json().get("value", [])
        if items:
            return items[0]["id"]
    return None


def _get_channel_id(team_id: str, nom_channel: str) -> str | None:
    client = get_graph_client()
    resp = requests.get(
        f"{client['base_url']}/teams/{team_id}/channels",
        headers=client["headers"],
    )
    if resp.status_code == 200:
        for c in resp.json().get("value", []):
            if c["displayName"].lower() == nom_channel.lower():
                return c["id"]
    return None


# ══════════════════════════════════════════════════════════════════
# OUTIL 1 : Envoyer un message dans un canal Teams
# ══════════════════════════════════════════════════════════════════

@tool
def envoyer_message_teams(
    nom_team: str,
    nom_channel: str,
    message: str,
    importance: str = "normal",
) -> str:
    """
    Envoie un message dans un canal Teams.

    Args:
        nom_team    : nom de l'équipe Teams (ex: 'RH Delior')
        nom_channel : nom du canal (ex: 'Général', 'Recrutement')
        message     : contenu du message (HTML basique supporté)
        importance  : 'normal', 'high' ou 'urgent' (défaut: 'normal')
    """
    try:
        client  = get_graph_client()
        team_id = _get_team_id(nom_team)
        if not team_id:
            return f"❌ Équipe introuvable : {nom_team}"

        channel_id = _get_channel_id(team_id, nom_channel)
        if not channel_id:
            return f"❌ Canal introuvable : {nom_channel} dans {nom_team}"

        resp = requests.post(
            f"{client['base_url']}/teams/{team_id}/channels/{channel_id}/messages",
            headers=client["headers"],
            json={
                "body":       {"contentType": "html", "content": message},
                "importance": importance,
            },
        )

        if resp.status_code == 201:
            logger.info("[graph_teams] Message envoyé : %s / %s", nom_team, nom_channel)
            return f"✅ Message envoyé dans **{nom_team}** / **{nom_channel}**."
        return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

    except Exception as e:
        logger.error("[graph_teams] envoyer_message : %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 2 : Lister les canaux d'une équipe
# ══════════════════════════════════════════════════════════════════

@tool
def lister_canaux_teams(nom_team: str) -> str:
    """
    Liste les canaux d'une équipe Teams.

    Args:
        nom_team : nom de l'équipe Teams (ex: 'RH Delior')
    """
    try:
        client  = get_graph_client()
        team_id = _get_team_id(nom_team)
        if not team_id:
            return f"❌ Équipe introuvable : {nom_team}"

        resp = requests.get(
            f"{client['base_url']}/teams/{team_id}/channels",
            headers=client["headers"],
        )
        if resp.status_code != 200:
            return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

        canaux = resp.json().get("value", [])
        if not canaux:
            return f"Aucun canal dans **{nom_team}**."

        lignes = [f"💬 {c['displayName']}" + (f"  — {c.get('description','')}" if c.get('description') else "")
                  for c in canaux]
        return f"Canaux de **{nom_team}** ({len(lignes)}) :\n" + "\n".join(lignes)

    except Exception as e:
        logger.error("[graph_teams] lister_canaux : %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 3 : Créer une réunion Teams (lien direct)
# ══════════════════════════════════════════════════════════════════

@tool
def creer_reunion_teams(
    sujet: str,
    debut: str,
    fin: str,
    participants: list[str],
) -> str:
    """
    Crée une réunion Teams et retourne le lien de jointure.
    (Utilise onlineMeetings — ne nécessite pas d'accès à un canal.)

    Args:
        sujet        : titre de la réunion
        debut        : date/heure de début ISO 8601 (ex: '2026-05-15T10:00:00')
        fin          : date/heure de fin ISO 8601   (ex: '2026-05-15T11:00:00')
        participants : liste d'emails des participants
    """
    try:
        client = get_graph_client()

        participants_payload = [
            {"upn": e, "role": "attendee"} for e in participants
        ]

        resp = requests.post(
            f"{client['base_url']}/users/{_user()}/onlineMeetings",
            headers=client["headers"],
            json={
                "subject": sujet,
                "startDateTime": f"{debut}+01:00",
                "endDateTime":   f"{fin}+01:00",
                "participants": {
                    "attendees": participants_payload,
                },
            },
        )

        if resp.status_code == 201:
            data  = resp.json()
            lien  = data.get("joinWebUrl", "")
            mid   = data.get("id", "")
            logger.info("[graph_teams] Réunion créée : %s", sujet)
            return (
                f"✅ Réunion Teams créée : **{sujet}**\n"
                f"📅 {debut[:16].replace('T',' ')} → {fin[11:16]}\n"
                f"🔗 {lien}"
            )
        return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

    except Exception as e:
        logger.error("[graph_teams] creer_reunion : %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 4 : Lire les derniers messages d'un canal
# ══════════════════════════════════════════════════════════════════

@tool
def lire_messages_canal(
    nom_team: str,
    nom_channel: str,
    limite: int = 10,
) -> str:
    """
    Lit les derniers messages d'un canal Teams.

    Args:
        nom_team    : nom de l'équipe Teams
        nom_channel : nom du canal
        limite      : nombre de messages à récupérer (défaut: 10, max: 50)
    """
    try:
        client  = get_graph_client()
        limite  = min(int(limite), 50)
        team_id = _get_team_id(nom_team)
        if not team_id:
            return f"❌ Équipe introuvable : {nom_team}"

        channel_id = _get_channel_id(team_id, nom_channel)
        if not channel_id:
            return f"❌ Canal introuvable : {nom_channel}"

        resp = requests.get(
            f"{client['base_url']}/teams/{team_id}/channels/{channel_id}/messages",
            headers=client["headers"],
            params={"$top": limite},
        )
        if resp.status_code != 200:
            return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

        messages = resp.json().get("value", [])
        if not messages:
            return f"Aucun message dans **{nom_channel}**."

        lignes = []
        for m in messages:
            auteur = m.get("from", {}).get("user", {}).get("displayName", "?")
            date   = m.get("createdDateTime", "?")[:16].replace("T", " ")
            body   = m.get("body", {}).get("content", "")
            # Nettoyage HTML basique
            import re
            texte = re.sub(r"<[^>]+>", "", body).strip()[:200]
            lignes.append(f"👤 **{auteur}** — {date}\n   {texte}")

        return f"Messages **{nom_team}** / **{nom_channel}** :\n\n" + "\n\n".join(lignes)

    except Exception as e:
        logger.error("[graph_teams] lire_messages : %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# EXPORT
# ══════════════════════════════════════════════════════════════════

def get_graph_teams_tools() -> list:
    return [
        envoyer_message_teams,
        lister_canaux_teams,
        creer_reunion_teams,
        lire_messages_canal,
    ]