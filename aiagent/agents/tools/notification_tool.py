from __future__ import annotations
import logging
from langchain_core.tools import tool
from utilisateur.models import Notification 

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# OUTIL 1 : Créer une notification
# ══════════════════════════════════════════════════════════════════
@tool
def creer_notification(
    content: str,
    tag: str = "Infos",
) -> str:
    """
    Crée une notification visible dans le centre RH.

    Args:
        content : message de la notification (max 100 caractères)
        tag     : type de notification — 'Infos', 'Succes', 'Error', 'Warning'
    """
    TAGS_VALIDES = {"Infos", "Succes", "Error", "Warning"}

    if tag not in TAGS_VALIDES:
        return f"Tag invalide : '{tag}'. Choisissez parmi : {', '.join(TAGS_VALIDES)}"

    if len(content) > 100:
        content = content[:97] + "..."
        logger.warning("[notification_tool] Contenu tronqué à 100 caractères")

    try:
        notif = Notification.objects.create(content=content, tag=tag)
        logger.info(f"[notification_tool] Notification créée : [{tag}] {content}")
        return f"Notification créée avec succès. ID : {notif.identifiant}"

    except Exception as e:
        logger.error(f"[notification_tool] Erreur lors de la création : {e}")
        return f"Erreur inattendue : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 2 : Lire les notifications
# ══════════════════════════════════════════════════════════════════

@tool
def lire_notifications(
    filtre_tag: str = "",
    non_lues_seulement: bool = False,
    limite: int = 10,
) -> str:
    """
    Récupère les notifications du centre RH.

    Args:
        filtre_tag         : filtre par tag — 'Infos', 'Succes', 'Error', 'Warning' (vide = tous)
        non_lues_seulement : si True, retourne uniquement les notifications non lues
        limite             : nombre max de notifications à retourner (défaut: 10)
    """
    try:
        qs = Notification.objects.all()

        if filtre_tag:
            qs = qs.filter(tag=filtre_tag)

        if non_lues_seulement:
            qs = qs.filter(lue=False)

        qs = qs[:limite]

        if not qs.exists():
            return "Aucune notification trouvée."

        lignes = []
        for notif in qs:
            statut = "🔵" if not notif.lue else "✅"
            date   = notif.date_creation.strftime("%d/%m/%Y %H:%M")
            lignes.append(
                f"{statut} [{notif.tag}] {notif.content} — {date}"
            )

        total_non_lues = Notification.objects.filter(lue=False).count()
        header = f"{total_non_lues} notification(s) non lue(s) au total\n"
        return header + "\n".join(lignes)

    except Exception as e:
        logger.error(f"[notification_tool] Erreur lors de la lecture : {e}")
        return f"Erreur inattendue : {e}"


# ══════════════════════════════════════════════════════════════════
# OUTIL 3 : Marquer comme lue
# ══════════════════════════════════════════════════════════════════

@tool
def marquer_notifications_lues(
    identifiant: str = "",
    tout_marquer: bool = False,
) -> str:
    """
    Marque une ou toutes les notifications comme lues.

    Args:
        identifiant  : UUID de la notification à marquer (ignoré si tout_marquer=True)
        tout_marquer : si True, marque toutes les notifications comme lues
    """
    try:
        if tout_marquer:
            count = Notification.objects.filter(lue=False).update(lue=True)
            logger.info(f"[notification_tool] {count} notification(s) marquées comme lues")
            return f"{count} notification(s) marquée(s) comme lue(s)."

        if not identifiant:
            return "Fournissez un identifiant ou activez 'tout_marquer'."

        notif = Notification.objects.get(identifiant=identifiant)
        notif.lue = True
        notif.save(update_fields=["lue"])
        logger.info(f"[notification_tool] Notification {identifiant} marquée comme lue")
        return f"Notification marquée comme lue : {notif.content}"

    except Notification.DoesNotExist:
        return f"Notification introuvable : {identifiant}"
    except Exception as e:
        logger.error(f"[notification_tool] Erreur : {e}")
        return f"Erreur inattendue : {e}"


# ══════════════════════════════════════════════════════════════════
# EXPORT
# ══════════════════════════════════════════════════════════════════

def get_notification_tools() -> list:
    """Retourne la liste des outils notification à passer au rh_agent."""
    return [
        creer_notification,
        lire_notifications,
        marquer_notifications_lues,
    ]