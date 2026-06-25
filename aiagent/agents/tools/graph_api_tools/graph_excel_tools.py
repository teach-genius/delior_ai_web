from __future__ import annotations

import logging
import os

import requests
from dotenv import load_dotenv
from langchain_core.tools import tool

from aiagent.auths.graph_api_auth import get_graph_client

load_dotenv()
logger = logging.getLogger(__name__)


def _user() -> str:
    return os.getenv("GRAPH_SENDER_EMAIL", "")


# ══════════════════════════════════════════════════════════════════
# HELPERS INTERNES
# ══════════════════════════════════════════════════════════════════

def _get_file_id(nom_fichier: str, dossier: str = "") -> str | None:
    """Cherche un fichier Excel dans OneDrive par nom."""
    client = get_graph_client()
    query  = nom_fichier if not dossier else f"{dossier}/{nom_fichier}"
    resp   = requests.get(
        f"{client['base_url']}/users/{_user()}/drive/root:/{query}",
        headers=client["headers"],
    )
    if resp.status_code == 200:
        return resp.json().get("id")
    return None


def _wb_url(file_id: str, path: str = "") -> str:
    client = get_graph_client()
    base = f"{client['base_url']}/users/{_user()}/drive/items/{file_id}/workbook"
    return f"{base}/{path}" if path else base


# ══════════════════════════════════════════════════════════════════
# OUTIL 1 : Lire une plage de cellules
# ══════════════════════════════════════════════════════════════════

@tool
def lire_plage_excel(
    nom_fichier: str,
    feuille: str,
    plage: str,
    dossier: str = "",
) -> str:
    """
    Lit une plage de cellules dans un fichier Excel sur OneDrive.

    Args:
        nom_fichier : nom du fichier (ex: 'Candidats.xlsx')
        feuille     : nom de la feuille (ex: 'Feuil1')
        plage       : plage Excel (ex: 'A1:E10')
        dossier     : sous-dossier OneDrive (optionnel, ex: 'RH/2026')
    """
    try:
        file_id = _get_file_id(nom_fichier, dossier)
        if not file_id:
            return f"❌ Fichier introuvable : {nom_fichier}"

        client = get_graph_client()
        resp = requests.get(
            _wb_url(file_id, f"worksheets/{feuille}/range(address='{plage}')"),
            headers=client["headers"],
        )
        if resp.status_code != 200:
            return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

        values = resp.json().get("values", [])
        if not values:
            return f"Plage {plage} vide dans {nom_fichier} / {feuille}."

        lignes = []
        for row in values:
            lignes.append(" | ".join(str(c) for c in row))
        return f"📊 **{nom_fichier}** / {feuille} / {plage} :\n\n" + "\n".join(lignes)

    except Exception as e:
        logger.error("[graph_excel] lire_plage : %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 2 : Écrire dans une plage de cellules
# ══════════════════════════════════════════════════════════════════

@tool
def ecrire_plage_excel(
    nom_fichier: str,
    feuille: str,
    plage: str,
    valeurs: list[list],
    dossier: str = "",
) -> str:
    """
    Écrit des valeurs dans une plage de cellules Excel sur OneDrive.

    Args:
        nom_fichier : nom du fichier (ex: 'Candidats.xlsx')
        feuille     : nom de la feuille (ex: 'Feuil1')
        plage       : plage cible (ex: 'A2:C2')
        valeurs     : tableau 2D de valeurs (ex: [['Ahmed', 'Dev', 'En cours']])
        dossier     : sous-dossier OneDrive (optionnel)
    """
    try:
        file_id = _get_file_id(nom_fichier, dossier)
        if not file_id:
            return f"❌ Fichier introuvable : {nom_fichier}"

        client = get_graph_client()
        resp = requests.patch(
            _wb_url(file_id, f"worksheets/{feuille}/range(address='{plage}')"),
            headers=client["headers"],
            json={"values": valeurs},
        )
        if resp.status_code == 200:
            logger.info("[graph_excel] Écriture OK : %s / %s / %s", nom_fichier, feuille, plage)
            return f"✅ Données écrites dans **{nom_fichier}** / {feuille} / {plage}."
        return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

    except Exception as e:
        logger.error("[graph_excel] ecrire_plage : %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 3 : Ajouter une ligne à la suite d'un tableau
# ══════════════════════════════════════════════════════════════════

@tool
def ajouter_ligne_excel(
    nom_fichier: str,
    feuille: str,
    nom_tableau: str,
    valeurs: list,
    dossier: str = "",
) -> str:
    """
    Ajoute une ligne à la fin d'un tableau Excel nommé (ListObject).

    Args:
        nom_fichier  : nom du fichier (ex: 'Candidats.xlsx')
        feuille      : nom de la feuille
        nom_tableau  : nom du tableau Excel (ex: 'TableauCandidats')
        valeurs      : liste des valeurs à ajouter (ex: ['Ahmed', 'Dev', '2026-05-01'])
        dossier      : sous-dossier OneDrive (optionnel)
    """
    try:
        file_id = _get_file_id(nom_fichier, dossier)
        if not file_id:
            return f"❌ Fichier introuvable : {nom_fichier}"

        client = get_graph_client()
        resp = requests.post(
            _wb_url(file_id, f"worksheets/{feuille}/tables/{nom_tableau}/rows/add"),
            headers=client["headers"],
            json={"values": [valeurs]},
        )
        if resp.status_code == 201:
            logger.info("[graph_excel] Ligne ajoutée : %s / %s", nom_fichier, nom_tableau)
            return f"✅ Ligne ajoutée dans le tableau **{nom_tableau}** de {nom_fichier}."
        return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

    except Exception as e:
        logger.error("[graph_excel] ajouter_ligne : %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 4 : Lister les feuilles d'un fichier
# ══════════════════════════════════════════════════════════════════

@tool
def lister_feuilles_excel(
    nom_fichier: str,
    dossier: str = "",
) -> str:
    """
    Liste les feuilles d'un fichier Excel sur OneDrive.

    Args:
        nom_fichier : nom du fichier (ex: 'Candidats.xlsx')
        dossier     : sous-dossier OneDrive (optionnel)
    """
    try:
        file_id = _get_file_id(nom_fichier, dossier)
        if not file_id:
            return f"❌ Fichier introuvable : {nom_fichier}"

        client = get_graph_client()
        resp = requests.get(
            _wb_url(file_id, "worksheets"),
            headers=client["headers"],
        )
        if resp.status_code != 200:
            return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

        feuilles = [f["name"] for f in resp.json().get("value", [])]
        return f"📋 Feuilles de **{nom_fichier}** : {', '.join(feuilles)}"

    except Exception as e:
        logger.error("[graph_excel] lister_feuilles : %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# EXPORT
# ══════════════════════════════════════════════════════════════════

def get_graph_excel_tools() -> list:
    return [
        lire_plage_excel,
        ecrire_plage_excel,
        ajouter_ligne_excel,
        lister_feuilles_excel,
    ]