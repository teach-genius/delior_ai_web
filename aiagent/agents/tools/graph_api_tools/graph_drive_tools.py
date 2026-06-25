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
# OUTIL 1 : Lister les fichiers d'un dossier OneDrive
# ══════════════════════════════════════════════════════════════════

@tool
def lister_fichiers_drive(
    dossier: str = "",
    limite: int = 20,
) -> str:
    """
    Liste les fichiers et dossiers dans un répertoire OneDrive.

    Args:
        dossier : chemin du dossier (ex: 'RH/Candidats'). Vide = racine.
        limite  : nombre max de résultats (défaut: 20, max: 100)
    """
    try:
        client = get_graph_client()
        limite = min(int(limite), 100)

        if dossier:
            url = f"{client['base_url']}/users/{_user()}/drive/root:/{dossier}:/children"
        else:
            url = f"{client['base_url']}/users/{_user()}/drive/root/children"

        resp = requests.get(
            url,
            headers=client["headers"],
            params={"$top": limite, "$select": "name,size,lastModifiedDateTime,file,folder"},
        )
        if resp.status_code != 200:
            return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

        items = resp.json().get("value", [])
        if not items:
            return f"Dossier vide : {dossier or 'racine'}"

        lignes = []
        for i in items:
            nom  = i["name"]
            date = i.get("lastModifiedDateTime", "?")[:10]
            if "folder" in i:
                nb = i["folder"].get("childCount", 0)
                lignes.append(f"📁 {nom}/  ({nb} éléments) — {date}")
            else:
                size = i.get("size", 0)
                size_str = f"{size // 1024} Ko" if size > 1024 else f"{size} o"
                lignes.append(f"📄 {nom}  ({size_str}) — {date}")

        path_label = dossier or "racine"
        return f"OneDrive / **{path_label}** ({len(lignes)}) :\n\n" + "\n".join(lignes)

    except Exception as e:
        logger.error("[graph_drive] lister_fichiers : %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 2 : Créer un dossier OneDrive
# ══════════════════════════════════════════════════════════════════

@tool
def creer_dossier_drive(
    nom_dossier: str,
    dossier_parent: str = "",
) -> str:
    """
    Crée un dossier dans OneDrive. Ne fait rien s'il existe déjà.

    Args:
        nom_dossier    : nom du dossier à créer (ex: 'Entretiens 2026')
        dossier_parent : chemin parent (ex: 'RH/Candidats'). Vide = racine.
    """
    try:
        client = get_graph_client()

        if dossier_parent:
            url = f"{client['base_url']}/users/{_user()}/drive/root:/{dossier_parent}:/children"
        else:
            url = f"{client['base_url']}/users/{_user()}/drive/root/children"

        resp = requests.post(
            url,
            headers=client["headers"],
            json={
                "name":                              nom_dossier,
                "folder":                            {},
                "@microsoft.graph.conflictBehavior": "fail",
            },
        )

        if resp.status_code == 201:
            logger.info("[graph_drive] Dossier créé : %s", nom_dossier)
            return f"✅ Dossier **{nom_dossier}** créé dans {dossier_parent or 'racine'}."
        if resp.status_code == 409:
            return f"ℹ️ Le dossier **{nom_dossier}** existe déjà."
        return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

    except Exception as e:
        logger.error("[graph_drive] creer_dossier : %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 3 : Obtenir le lien de partage d'un fichier
# ══════════════════════════════════════════════════════════════════

@tool
def partager_fichier_drive(
    chemin_fichier: str,
    type_lien: str = "view",
    scope: str = "organization",
) -> str:
    """
    Génère un lien de partage pour un fichier OneDrive.

    Args:
        chemin_fichier : chemin du fichier (ex: 'RH/Contrats/contrat_ahmed.pdf')
        type_lien      : 'view' (lecture) ou 'edit' (modification) — défaut: 'view'
        scope          : 'organization' (interne) ou 'anonymous' (externe) — défaut: 'organization'
    """
    try:
        client = get_graph_client()

        resp = requests.post(
            f"{client['base_url']}/users/{_user()}/drive/root:/{chemin_fichier}:/createLink",
            headers=client["headers"],
            json={"type": type_lien, "scope": scope},
        )

        if resp.status_code == 201:
            lien = resp.json().get("link", {}).get("webUrl", "")
            logger.info("[graph_drive] Lien créé : %s", chemin_fichier)
            return f"✅ Lien de partage ({type_lien}) :\n🔗 {lien}"
        return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

    except Exception as e:
        logger.error("[graph_drive] partager_fichier : %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 4 : Rechercher un fichier
# ══════════════════════════════════════════════════════════════════

@tool
def rechercher_fichier_drive(
    mot_cle: str,
    limite: int = 10,
) -> str:
    """
    Recherche des fichiers dans tout le OneDrive par mot-clé.

    Args:
        mot_cle : terme de recherche (ex: 'contrat ahmed', 'CV 2026')
        limite  : nombre max de résultats (défaut: 10, max: 25)
    """
    try:
        client = get_graph_client()
        limite = min(int(limite), 25)

        resp = requests.get(
            f"{client['base_url']}/users/{_user()}/drive/root/search(q='{mot_cle}')",
            headers=client["headers"],
            params={"$top": limite, "$select": "name,size,lastModifiedDateTime,parentReference,webUrl"},
        )
        if resp.status_code != 200:
            return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

        items = resp.json().get("value", [])
        if not items:
            return f"Aucun fichier trouvé pour : **{mot_cle}**"

        lignes = []
        for i in items:
            nom    = i["name"]
            date   = i.get("lastModifiedDateTime", "?")[:10]
            parent = i.get("parentReference", {}).get("path", "?").replace("/drive/root:", "")
            url    = i.get("webUrl", "")
            lignes.append(f"📄 **{nom}**\n   📁 {parent}\n   📅 {date}\n   🔗 {url}")

        return f"Résultats pour **{mot_cle}** ({len(lignes)}) :\n\n" + "\n\n".join(lignes)

    except Exception as e:
        logger.error("[graph_drive] rechercher_fichier : %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 5 : Uploader un fichier (contenu texte/bytes)
# ══════════════════════════════════════════════════════════════════

@tool
def uploader_fichier_drive(
    chemin_destination: str,
    contenu_texte: str,
) -> str:
    """
    Crée ou remplace un fichier texte (.txt, .csv, .md) dans OneDrive.

    Args:
        chemin_destination : chemin complet avec nom (ex: 'RH/exports/candidats.csv')
        contenu_texte      : contenu texte du fichier
    """
    try:
        client = get_graph_client()

        resp = requests.put(
            f"{client['base_url']}/users/{_user()}/drive/root:/{chemin_destination}:/content",
            headers={
                "Authorization": client["headers"]["Authorization"],
                "Content-Type":  "text/plain; charset=utf-8",
            },
            data=contenu_texte.encode("utf-8"),
        )

        if resp.status_code in (200, 201):
            logger.info("[graph_drive] Fichier uploadé : %s", chemin_destination)
            url = resp.json().get("webUrl", "")
            return f"✅ Fichier uploadé : **{chemin_destination}**\n🔗 {url}"
        return f"❌ Erreur Graph ({resp.status_code}) : {resp.text}"

    except Exception as e:
        logger.error("[graph_drive] uploader_fichier : %s", e)
        return f"❌ Erreur : {e}"


# ══════════════════════════════════════════════════════════════════
# EXPORT
# ══════════════════════════════════════════════════════════════════

def get_graph_drive_tools() -> list:
    return [
        lister_fichiers_drive,
        creer_dossier_drive,
        partager_fichier_drive,
        rechercher_fichier_drive,
        uploader_fichier_drive,
    ]