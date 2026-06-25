from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from langchain_core.tools import tool

from aiagent.auths.graph_api_auth import get_graph_client

load_dotenv()
logger = logging.getLogger(__name__)

COMPANY_NAME = "Delior Group"


def _mailbox() -> str:
    return os.getenv("GRAPH_SENDER_EMAIL", "")


# ══════════════════════════════════════════════════════════════════
# OUTIL 1 : Créer un événement / entretien
# ══════════════════════════════════════════════════════════════════

@tool
def creer_evenement(
    sujet: str,
    debut: str,
    fin: str,
    participants: list[str],
    lieu_ou_lien: str = "",
    description: str = "",
    rappel_minutes: int = 30,
    teams: bool = True,
) -> str:
    """
    Crée un événement dans le calendrier Outlook et envoie les invitations.

    Args:
        sujet           : titre de l'événement (ex: 'Entretien RH — Poste Comptable')
        debut           : date/heure de début ISO 8601 (ex: '2026-05-15T10:00:00')
        fin             : date/heure de fin ISO 8601   (ex: '2026-05-15T11:00:00')
        participants    : liste d'emails des participants
        lieu_ou_lien    : salle physique ou URL externe (optionnel si teams=True)
        description     : corps de l'invitation (optionnel)
        rappel_minutes  : rappel avant l'événement en minutes (défaut: 30)
        teams           : créer automatiquement un lien Teams (défaut: True)
    """
    try:
        client = get_graph_client()

        attendees = [
            {"emailAddress": {"address": e}, "type": "required"}
            for e in participants
        ]

        body_content = description or f"Réunion organisée par {COMPANY_NAME}."
        if lieu_ou_lien and not teams:
            body_content += f"\n\n📍 Lieu : {lieu_ou_lien}"

        payload: dict = {
            "subject": sujet,
            "body": {"contentType": "HTML", "content": body_content},
            "start": {"dateTime": debut, "timeZone": "Africa/Casablanca"},
            "end":   {"dateTime": fin,   "timeZone": "Africa/Casablanca"},
            "attendees": attendees,
            "reminderMinutesBeforeStart": rappel_minutes,
            "isReminderOn": True,
            "allowNewTimeProposals": True,
        }

        if teams:
            payload["isOnlineMeeting"]      = True
            payload["onlineMeetingProvider"] = "teamsForBusiness"
        elif lieu_ou_lien:
            payload["location"] = {"displayName": lieu_ou_lien}

        resp = requests.post(
            f"{client['base_url']}/users/{_mailbox()}/calendar/events",
            headers=client["headers"],
            json=payload,
        )

        if resp.status_code == 201:
            event = resp.json()
            lien  = event.get("onlineMeeting", {}).get("joinUrl", "")
            msg   = f"✅ Événement créé : **{sujet}**\n📅 {debut[:16].replace('T',' ')} → {fin[11:16]}"
            if lien:
                msg += f"\n🔗 Lien Teams : {lien}"
            logger.info("[graph_calendar] Événement créé : %s", sujet)
            return msg

        logger.error("[graph_calendar] Erreur %s : %s", resp.status_code, resp.text)
        return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

    except Exception as e:
        logger.error("[graph_calendar] creer_evenement : %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 2 : Lister les événements à venir
# ══════════════════════════════════════════════════════════════════

@tool
def lister_evenements(
    jours: int = 7,
    limite: int = 20,
    mot_cle: str = "",
) -> str:
    """
    Liste les événements à venir du calendrier Outlook.

    Args:
        jours    : nombre de jours à partir d'aujourd'hui (défaut: 7)
        limite   : nombre max d'événements (défaut: 20, max: 50)
        mot_cle  : filtre sur le sujet (optionnel, ex: 'entretien')
    """
    try:
        client = get_graph_client()
        limite = min(int(limite), 50)

        now   = datetime.now(timezone.utc)
        end   = now + timedelta(days=jours)
        start_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str   = end.strftime("%Y-%m-%dT%H:%M:%SZ")

        params: dict = {
            "$top":     limite,
            "$select":  "subject,start,end,location,attendees,onlineMeeting",
            "$orderby": "start/dateTime asc",
            "$filter":  f"start/dateTime ge '{start_str}' and start/dateTime le '{end_str}'",
        }

        resp = requests.get(
            f"{client['base_url']}/users/{_mailbox()}/calendar/events",
            headers=client["headers"],
            params=params,
        )

        if resp.status_code != 200:
            return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

        events = resp.json().get("value", [])

        if mot_cle:
            events = [e for e in events if mot_cle.lower() in e.get("subject", "").lower()]

        if not events:
            return f"Aucun événement dans les {jours} prochains jours."

        lignes = []
        for e in events:
            sujet   = e.get("subject", "(sans titre)")
            debut   = e["start"]["dateTime"][:16].replace("T", " ")
            fin_str = e["end"]["dateTime"][11:16]
            lieu    = e.get("location", {}).get("displayName", "")
            lien    = e.get("onlineMeeting", {}) or {}
            join    = lien.get("joinUrl", "")
            nb_att  = len(e.get("attendees", []))

            ligne = f"📅 **{sujet}**\n   {debut} → {fin_str} | {nb_att} participant(s)"
            if lieu:
                ligne += f"\n   📍 {lieu}"
            if join:
                ligne += f"\n   🔗 Teams disponible"
            lignes.append(ligne)

        return f"Événements ({len(lignes)}) sur les {jours} prochains jours :\n\n" + "\n\n".join(lignes)

    except Exception as e:
        logger.error("[graph_calendar] lister_evenements : %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 3 : Annuler / supprimer un événement
# ══════════════════════════════════════════════════════════════════

@tool
def annuler_evenement(
    sujet_ou_id: str,
    message_annulation: str = "",
) -> str:
    """
    Annule et supprime un événement du calendrier. Notifie les participants.

    Args:
        sujet_ou_id        : sujet exact ou ID Graph de l'événement
        message_annulation : message facultatif envoyé aux participants
    """
    try:
        client = get_graph_client()

        # Cherche l'événement par sujet si ce n'est pas un ID Graph
        event_id = sujet_ou_id
        if not sujet_ou_id.startswith("AAM") and len(sujet_ou_id) < 100:
            resp = requests.get(
                f"{client['base_url']}/users/{_mailbox()}/calendar/events",
                headers=client["headers"],
                params={
                    "$select": "id,subject",
                    "$filter": f"contains(subject,'{sujet_ou_id}')",
                    "$top": 5,
                },
            )
            if resp.status_code != 200 or not resp.json().get("value"):
                return f"❌ Événement introuvable : {sujet_ou_id}"
            event_id = resp.json()["value"][0]["id"]

        # Annulation avec message
        if message_annulation:
            requests.post(
                f"{client['base_url']}/users/{_mailbox()}/calendar/events/{event_id}/cancel",
                headers=client["headers"],
                json={"comment": message_annulation},
            )

        # Suppression
        r = requests.delete(
            f"{client['base_url']}/users/{_mailbox()}/calendar/events/{event_id}",
            headers=client["headers"],
        )

        if r.status_code == 204:
            logger.info("[graph_calendar] Événement annulé : %s", sujet_ou_id)
            return f"✅ Événement **{sujet_ou_id}** annulé et supprimé."
        return f"❌ Erreur Graph ({r.status_code}) : {r.text}"

    except Exception as e:
        logger.error("[graph_calendar] annuler_evenement : %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 4 : Vérifier disponibilité (free/busy)
# ══════════════════════════════════════════════════════════════════

@tool
def verifier_disponibilite(
    emails: list[str],
    debut: str,
    fin: str,
) -> str:
    """
    Vérifie si des personnes sont disponibles sur un créneau donné.

    Args:
        emails : liste d'adresses email à vérifier
        debut  : date/heure de début ISO 8601 (ex: '2026-05-15T09:00:00')
        fin    : date/heure de fin ISO 8601   (ex: '2026-05-15T10:00:00')
    """
    try:
        client = get_graph_client()

        payload = {
            "schedules":           emails,
            "startTime":           {"dateTime": debut, "timeZone": "Africa/Casablanca"},
            "endTime":             {"dateTime": fin,   "timeZone": "Africa/Casablanca"},
            "availabilityViewInterval": 30,
        }

        resp = requests.post(
            f"{client['base_url']}/users/{_mailbox()}/calendar/getSchedule",
            headers=client["headers"],
            json=payload,
        )

        if resp.status_code != 200:
            return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

        resultats = []
        for s in resp.json().get("value", []):
            email  = s.get("scheduleId", "?")
            items  = s.get("scheduleItems", [])
            if not items:
                resultats.append(f"✅ {email} : disponible")
            else:
                conflits = [
                    f"{i['start']['dateTime'][11:16]}–{i['end']['dateTime'][11:16]}"
                    for i in items
                ]
                resultats.append(f"❌ {email} : occupé ({', '.join(conflits)})")

        return f"Disponibilité {debut[11:16]}–{fin[11:16]} :\n" + "\n".join(resultats)

    except Exception as e:
        logger.error("[graph_calendar] verifier_disponibilite : %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# EXPORT
# ══════════════════════════════════════════════════════════════════

def get_graph_calendar_tools() -> list:
    return [
        creer_evenement,
        lister_evenements,
        annuler_evenement,
        verifier_disponibilite,
    ]