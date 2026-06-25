import logging
import requests
from langchain_core.tools import tool
from delioreApp.aiagent.auths.cornerstone_auth import CSOD_BASE_URL, csod_headers

logger = logging.getLogger(__name__)


@tool
def rechercher_candidat_cornerstone(
    email: str = "",
    nom: str = "",
) -> str:
    """
    Recherche un candidat dans Cornerstone OnDemand.

    Args:
        email : email du candidat
        nom   : nom du candidat
    """
    try:
        params = {}
        if email: params["email"] = email
        if nom:   params["lastName"] = nom

        r = requests.get(
            f"{CSOD_BASE_URL}/services/api/x/users/v1/employees",
            headers=csod_headers(),
            params=params,
        )
        r.raise_for_status()
        data = r.json().get("data", [])

        if not data:
            return "Aucun candidat trouvé dans Cornerstone."

        lignes = []
        for u in data[:10]:
            lignes.append(
                f"👤 {u.get('firstName')} {u.get('lastName')} "
                f"— {u.get('email')} | Statut: {u.get('status')}"
            )
        return "\n".join(lignes)

    except Exception as e:
        logger.error(f"[cornerstone_tool] rechercher_candidat : {e}")
        return f"Erreur : {e}"


@tool
def get_formations_cornerstone(
    statut: str = "Active",
    limite: int = 10,
) -> str:
    """
    Liste les formations disponibles dans Cornerstone.

    Args:
        statut : 'Active', 'Inactive', 'All'
        limite : nombre max de résultats
    """
    try:
        r = requests.get(
            f"{CSOD_BASE_URL}/services/api/x/lms/v1/learning-objects",
            headers=csod_headers(),
            params={"status": statut, "pageSize": limite},
        )
        r.raise_for_status()
        data = r.json().get("data", [])

        if not data:
            return "Aucune formation trouvée."

        lignes = [
            f"{f.get('title')} — {f.get('type')} | Durée: {f.get('duration', 'N/A')}"
            for f in data
        ]
        return f"{len(lignes)} formation(s) :\n" + "\n".join(lignes)

    except Exception as e:
        logger.error(f"[cornerstone_tool] get_formations : {e}")
        return f"Erreur : {e}"


@tool
def inscrire_employe_formation(
    user_id_csod: str,
    formation_id: str,
) -> str:
    """
    Inscrit un employé à une formation dans Cornerstone.

    Args:
        user_id_csod : ID de l'utilisateur dans Cornerstone
        formation_id : ID de la formation
    """
    try:
        r = requests.post(
            f"{CSOD_BASE_URL}/services/api/x/lms/v1/registrations",
            headers=csod_headers(),
            json={"userId": user_id_csod, "loId": formation_id},
        )
        r.raise_for_status()
        return f"Inscription confirmée pour l'employé {user_id_csod}."

    except Exception as e:
        logger.error(f"[cornerstone_tool] inscrire_employe : {e}")
        return f"Erreur : {e}"


def get_cornerstone_tools() -> list:
    return [
        rechercher_candidat_cornerstone,
        get_formations_cornerstone,
        inscrire_employe_formation,
    ]