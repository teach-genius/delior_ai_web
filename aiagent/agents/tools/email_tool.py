from __future__ import annotations
import logging
from datetime import datetime
from langchain_core.tools import tool
import requests
from aiagent.auths.auth import get_token
from aiagent.auths.config import GRAPH_BASE

logger = logging.getLogger(__name__)
MAILBOX      = "recrutement@deliorgroup.com"
COMPANY_NAME = "Delior Group"


def _h(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def _url(*parts):
    return f"{GRAPH_BASE}/users/{MAILBOX}/" + "/".join(parts)


def _send(token: str, to_email: str, subject: str, body_html: str) -> str:
    """Envoie un email via Graph sendMail."""
    payload = {
        "message": {
            "subject": subject,
            "body":    {"contentType": "HTML", "content": body_html},
            "toRecipients": [
                {"emailAddress": {"address": to_email}}
            ],
        },
        "saveToSentItems": True,
    }
    resp = requests.post(_url("sendMail"), headers=_h(token), json=payload)
    if resp.status_code == 202:
        logger.info(f"[graph_send] Email envoyé à {to_email} — {subject}")
        return f"✅ Email envoyé à {to_email}."
    logger.error(f"[graph_send] {resp.status_code} — {resp.text}")
    return f"❌ Échec ({resp.status_code}) : {resp.text}"


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
) -> str:
    """
    Envoie un email de convocation à un entretien.

    Args:
        email_candidat  : adresse email du candidat
        nom_candidat    : prénom et nom du candidat
        poste           : intitulé du poste
        date_entretien  : date (ex: '15 mai 2026')
        heure_entretien : heure (ex: '10h00')
        lieu_ou_lien    : adresse physique ou lien Teams/Zoom
        nom_recruteur   : signataire
    """
    token   = get_token()
    subject = f"Convocation à un entretien — {poste} | {COMPANY_NAME}"
    body    = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;line-height:1.6">
    <p>Bonjour <strong>{nom_candidat}</strong>,</p>
    <p>Nous avons le plaisir de vous convier à un entretien pour le poste de
       <strong>{poste}</strong> au sein de <strong>{COMPANY_NAME}</strong>.</p>
    <table style="border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:6px 16px 6px 0"><strong>📅 Date :</strong></td><td>{date_entretien}</td></tr>
      <tr><td style="padding:6px 16px 6px 0"><strong>🕐 Heure :</strong></td><td>{heure_entretien}</td></tr>
      <tr><td style="padding:6px 16px 6px 0"><strong>📍 Lieu :</strong></td><td>{lieu_ou_lien}</td></tr>
    </table>
    <p>Merci de confirmer votre présence en répondant à cet email.</p>
    <p>Cordialement,<br><strong>{nom_recruteur}</strong><br>
       Équipe Recrutement — {COMPANY_NAME}</p>
    </body></html>"""
    return _send(token, email_candidat, subject, body)


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
) -> str:
    """
    Envoie un email de refus respectueux.

    Args:
        email_candidat  : adresse email du candidat
        nom_candidat    : prénom et nom du candidat
        poste           : poste concerné
        nom_recruteur   : signataire
        motif_optionnel : motif (optionnel)
    """
    token      = get_token()
    subject    = f"Suite à votre candidature — {poste} | {COMPANY_NAME}"
    motif_html = f"<p>{motif_optionnel}</p>" if motif_optionnel else ""
    body       = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;line-height:1.6">
    <p>Bonjour <strong>{nom_candidat}</strong>,</p>
    <p>Nous vous remercions de l'intérêt porté à <strong>{COMPANY_NAME}</strong>
       pour le poste de <strong>{poste}</strong>.</p>
    <p>Après examen attentif, nous avons le regret de ne pouvoir donner
       une suite favorable à votre candidature.</p>
    {motif_html}
    <p>Nous conservons votre profil et ne manquerons pas de vous recontacter
       si une opportunité se présente.</p>
    <p>Cordialement,<br><strong>{nom_recruteur}</strong><br>
       Équipe Recrutement — {COMPANY_NAME}</p>
    </body></html>"""
    return _send(token, email_candidat, subject, body)


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
) -> str:
    """
    Envoie un email de relance à un candidat sans réponse.

    Args:
        email_candidat : adresse email du candidat
        nom_candidat   : prénom et nom du candidat
        poste          : poste concerné
        delai_reponse  : délai souhaité (ex: '5 jours ouvrés')
        nom_recruteur  : signataire
    """
    token   = get_token()
    subject = f"Relance — Votre candidature pour {poste} | {COMPANY_NAME}"
    body    = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;line-height:1.6">
    <p>Bonjour <strong>{nom_candidat}</strong>,</p>
    <p>Nous revenons vers vous concernant votre candidature pour le poste de
       <strong>{poste}</strong> au sein de <strong>{COMPANY_NAME}</strong>.</p>
    <p>N'ayant pas encore reçu de retour, merci de nous confirmer votre disponibilité
       dans un délai de <strong>{delai_reponse}</strong>.</p>
    <p>Sans retour de votre part, nous poursuivrons notre processus avec d'autres candidats.</p>
    <p>Cordialement,<br><strong>{nom_recruteur}</strong><br>
       Équipe Recrutement — {COMPANY_NAME}</p>
    </body></html>"""
    return _send(token, email_candidat, subject, body)


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
) -> str:
    """
    Envoie un email entièrement personnalisé.

    Args:
        email_candidat : adresse email du destinataire
        nom_candidat   : prénom et nom du candidat
        sujet          : objet de l'email
        contenu        : corps du message (texte ou HTML basique)
        nom_recruteur  : signataire
    """
    token = get_token()
    body  = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;line-height:1.6">
    <p>Bonjour <strong>{nom_candidat}</strong>,</p>
    {contenu}
    <p>Cordialement,<br><strong>{nom_recruteur}</strong><br>
       Équipe Recrutement — {COMPANY_NAME}</p>
    </body></html>"""
    return _send(token, email_candidat, sujet, body)


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
