from __future__ import annotations

import logging
import os
import requests
from langchain_core.tools import tool

from aiagent.auths.graph_api_auth import get_graph_client

logger = logging.getLogger(__name__)

COMPANY_NAME = "Delior Groupe"

# ══════════════════════════════════════════════════════════════════
# HELPERS INTERNES
# ══════════════════════════════════════════════════════════════════
def _build_message(
    to_email: str,
    subject: str,
    body_html: str,
    cc_emails: list[str] | None = None,
    importance: str = "normal",
) -> dict:
    """Construit le payload 'message' pour Graph sendMail."""
    msg: dict = {
        "subject":    subject,
        "importance": importance,  # "low" | "normal" | "high"
        "body": {
            "contentType": "HTML",
            "content":     body_html,
        },
        "toRecipients": [{"emailAddress": {"address": to_email}}],
    }
    if cc_emails:
        msg["ccRecipients"] = [
            {"emailAddress": {"address": e}} for e in cc_emails
        ]
    return msg


def _send_email(
    to_email: str,
    subject: str,
    body_html: str,
    cc_emails: list[str] | None = None,
    importance: str = "normal",
) -> str:
    """Envoie un email via Graph API et retourne un message de statut."""
    sender = os.getenv("GRAPH_SENDER_EMAIL")

    try:
        client = get_graph_client()

        payload = {
            "message":        _build_message(to_email, subject, body_html, cc_emails, importance),
            "saveToSentItems": True,
        }

        resp = requests.post(
            f"{client['base_url']}/users/{sender}/sendMail",
            headers=client["headers"],
            json=payload,
            timeout=30,
        )

        if resp.status_code == 202:
            logger.info("[graph_mail] Envoyé à %s — %s", to_email, subject)
            return f"✅ Email envoyé avec succès à {to_email}."

        logger.error("[graph_mail] Erreur %s : %s", resp.status_code, resp.text)
        return f"❌ Erreur Graph API ({resp.status_code}) : {resp.text}"

    except requests.exceptions.RequestException as e:
        logger.error("[graph_mail] Erreur réseau : %s", e)
        return f"❌ Erreur réseau : {e}"
    except Exception as e:
        logger.error("[graph_mail] Erreur inattendue : %s", e)
        return f"❌ Erreur inattendue : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 1 : Convocation entretien
# ══════════════════════════════════════════════════════════════════

@tool
def envoyer_convocation(
    email_candidat: str,
    nom_candidat: str,
    poste: str,
    date_entretien: str,
    heure_entretien: str,
    lieu_ou_lien: str,
    nom_recruteur: str = "L'équipe RH Delior",
    cc_emails: list[str] | None = None,
) -> str:
    """
    Envoie un email de convocation à un entretien à un candidat.

    Args:
        email_candidat  : adresse email du candidat
        nom_candidat    : prénom et nom du candidat
        poste           : intitulé du poste
        date_entretien  : date de l'entretien (ex: '15 mai 2026')
        heure_entretien : heure de l'entretien (ex: '10h00')
        lieu_ou_lien    : adresse physique ou lien Teams/Zoom
        nom_recruteur   : nom du recruteur signataire
        cc_emails       : liste d'adresses en copie (optionnel)
    """
    subject = f"Convocation à un entretien — {poste} | {COMPANY_NAME}"
    body = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;line-height:1.6;">
    <p>Bonjour <strong>{nom_candidat}</strong>,</p>
    <p>Nous avons bien étudié votre candidature pour le poste de <strong>{poste}</strong>
    au sein de <strong>{COMPANY_NAME}</strong> et avons le plaisir de vous convier
    à un entretien.</p>
    <table style="border-collapse:collapse;margin:16px 0;">
      <tr><td style="padding:6px 16px 6px 0;"><strong>📅 Date :</strong></td><td>{date_entretien}</td></tr>
      <tr><td style="padding:6px 16px 6px 0;"><strong>🕐 Heure :</strong></td><td>{heure_entretien}</td></tr>
      <tr><td style="padding:6px 16px 6px 0;"><strong>📍 Lieu / Lien :</strong></td><td>{lieu_ou_lien}</td></tr>
    </table>
    <p>Merci de confirmer votre présence en répondant à cet email.</p>
    <p>Cordialement,<br><strong>{nom_recruteur}</strong><br>
    Équipe Recrutement — {COMPANY_NAME}</p>
    </body></html>"""

    return _send_email(email_candidat, subject, body, cc_emails, importance="high")


# ══════════════════════════════════════════════════════════════════
# OUTIL 2 : Refus candidature
# ══════════════════════════════════════════════════════════════════

@tool
def envoyer_refus(
    email_candidat: str,
    nom_candidat: str,
    poste: str,
    nom_recruteur: str = "L'équipe RH Delior",
    motif_optionnel: str = "",
    cc_emails: list[str] | None = None,
) -> str:
    """
    Envoie un email de refus respectueux à un candidat.

    Args:
        email_candidat  : adresse email du candidat
        nom_candidat    : prénom et nom du candidat
        poste           : intitulé du poste pour lequel il a postulé
        nom_recruteur   : nom du recruteur signataire
        motif_optionnel : motif de refus (optionnel)
        cc_emails       : liste d'adresses en copie (optionnel)
    """
    subject = f"Suite à votre candidature — {poste} | {COMPANY_NAME}"
    motif_html = f"<p>{motif_optionnel}</p>" if motif_optionnel else ""
    body = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;line-height:1.6;">
    <p>Bonjour <strong>{nom_candidat}</strong>,</p>
    <p>Nous vous remercions de l'intérêt que vous portez à <strong>{COMPANY_NAME}</strong>
    et du temps consacré à votre candidature pour le poste de <strong>{poste}</strong>.</p>
    <p>Après examen attentif de votre dossier, nous avons le regret de vous informer
    que nous ne sommes pas en mesure de donner une suite favorable à votre candidature.</p>
    {motif_html}
    <p>Nous conservons votre profil et ne manquerons pas de vous recontacter si une
    opportunité correspondant à vos compétences se présente.</p>
    <p>Cordialement,<br><strong>{nom_recruteur}</strong><br>
    Équipe Recrutement — {COMPANY_NAME}</p>
    </body></html>"""

    return _send_email(email_candidat, subject, body, cc_emails)


# ══════════════════════════════════════════════════════════════════
# OUTIL 3 : Relance candidat
# ══════════════════════════════════════════════════════════════════

@tool
def envoyer_relance(
    email_candidat: str,
    nom_candidat: str,
    poste: str,
    delai_reponse: str = "5 jours ouvrés",
    nom_recruteur: str = "L'équipe RH Delior",
    cc_emails: list[str] | None = None,
) -> str:
    """
    Envoie un email de relance à un candidat qui n'a pas encore répondu.

    Args:
        email_candidat  : adresse email du candidat
        nom_candidat    : prénom et nom du candidat
        poste           : intitulé du poste concerné
        delai_reponse   : délai de réponse souhaité
        nom_recruteur   : nom du recruteur signataire
        cc_emails       : liste d'adresses en copie (optionnel)
    """
    subject = f"Relance — Votre candidature pour le poste de {poste} | {COMPANY_NAME}"
    body = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;line-height:1.6;">
    <p>Bonjour <strong>{nom_candidat}</strong>,</p>
    <p>Nous revenons vers vous concernant votre candidature pour le poste de
    <strong>{poste}</strong> au sein de <strong>{COMPANY_NAME}</strong>.</p>
    <p>Nous n'avons pas encore reçu de retour de votre part et souhaitions savoir
    si vous êtes toujours disponible et intéressé(e) par cette opportunité.</p>
    <p>Merci de nous confirmer votre situation dans un délai de
    <strong>{delai_reponse}</strong>.</p>
    <p>Sans retour de votre part, nous poursuivrons notre processus
    avec d'autres candidats.</p>
    <p>Cordialement,<br><strong>{nom_recruteur}</strong><br>
    Équipe Recrutement — {COMPANY_NAME}</p>
    </body></html>"""

    return _send_email(email_candidat, subject, body, cc_emails)


# ══════════════════════════════════════════════════════════════════
# OUTIL 4 : Email libre
# ══════════════════════════════════════════════════════════════════

@tool
def envoyer_email_libre(
    email_candidat: str,
    nom_candidat: str,
    sujet: str,
    contenu: str,
    nom_recruteur: str = "L'équipe RH Delior",
    cc_emails: list[str] | None = None,
    importance: str = "normal",
) -> str:
    """
    Envoie un email entièrement personnalisé à un candidat.

    Args:
        email_candidat : adresse email du destinataire
        nom_candidat   : prénom et nom du candidat
        sujet          : objet de l'email
        contenu        : corps du message (texte libre ou HTML basique)
        nom_recruteur  : nom du recruteur signataire
        cc_emails      : liste d'adresses en copie (optionnel)
        importance     : priorité de l'email — "low" | "normal" | "high"
    """
    body = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;line-height:1.6;">
    <p>Bonjour <strong>{nom_candidat}</strong>,</p>
    {contenu}
    <p>Cordialement,<br><strong>{nom_recruteur}</strong><br>
    Équipe Recrutement — {COMPANY_NAME}</p>
    </body></html>"""

    return _send_email(email_candidat, sujet, body, cc_emails, importance)


# ══════════════════════════════════════════════════════════════════
# EXPORT
# ══════════════════════════════════════════════════════════════════

def get_email_tools() -> list:
    return [
        envoyer_convocation,
        envoyer_refus,
        envoyer_relance,
        envoyer_email_libre,
    ]