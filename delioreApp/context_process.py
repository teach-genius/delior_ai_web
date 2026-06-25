import logging
from utilisateur.models import Notification,Users
from candidat.models import CandidatCV
from django.utils import timezone

logger = logging.getLogger(__name__)


def get_unread_notifications_count(user_id: str) -> int:
    """Retourne le nombre de notifications non lues d'un utilisateur."""
    try:
        user = Users.objects.get(identifiant=user_id)
        return user.notification.filter(lue=False).count()
    except Users.DoesNotExist:
        return 0
    
def get_candidats_today():
    today = timezone.now().date()
    return CandidatCV.objects.filter(
        date_importation__date=today
    ).count()

def notifications(request):
    if request.user.is_authenticated:
        try:
            notifications = Notification.objects.filter(
                lue=False
            ).order_by("-date_creation")
            count = notifications.count()
        except Exception as e:
            logger.exception("context_processor notifications : erreur — %s", e)
            notifications = []
            count = 0
    else:
        notifications = []
        count = 0

    return {
        "notifications": notifications,
        "notifications_count": count,
    }


def domaines_secteurs(request):
    if request.user.is_authenticated:
        try:
            domaines = (
                CandidatCV.objects
                .exclude(domaine="")
                .values_list("domaine", flat=True)
                .distinct()
            )
            secteurs = (
                CandidatCV.objects
                .exclude(secteur="")
                .values_list("secteur", flat=True)
                .distinct()
            )
            notif = get_unread_notifications_count(str(request.user.identifiant))
            domaines = tuple(set(domaines))
            secteurs = tuple(set(secteurs))
            
        except Exception as e:
            logger.exception("context_processor domaines_secteurs : erreur — %s", e)
            domaines = ()
            secteurs = ()
            notif = ()
    else:
        domaines = ()
        secteurs = ()
        notif = ()

    return {
        "domaines": domaines,
        "secteurs": secteurs,
        "candidats_day":get_candidats_today(),
        "notif_non_lue":notif
    }