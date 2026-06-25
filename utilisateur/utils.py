import os
from django.core.files import File
from .models import Users
from django.db import transaction, IntegrityError
from django.contrib.auth import get_user_model
import re


User = get_user_model()

def is_valid_password(password: str) -> bool:
    if len(password) < 8:
        return False
    if not re.search(r"[a-z]", password):
        return False
    if not re.search(r"[A-Z]", password):
        return False
    return True

@transaction.atomic
def create_user_safe(email, password, first_name="", last_name="", post=""):
    try:
        email = email.lower().strip()

        user, created = User.objects.get_or_create(
            username=email,
            defaults={
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "post": post,
            }
        )

        if created:
            user.set_password(password)
            user.save()

        return user, created

    except IntegrityError as e:
        print(f"Erreur d'intégrité lors de la création de l'utilisateur: {e}")
        return None, False
    except Exception as e:
        print(f"Erreur inattendue lors de la création de l'utilisateur: {e}")
        return None, False

@transaction.atomic
def update_user(
    user_id,
    username=None,
    first_name=None,
    last_name=None,
    email=None,
    post=None,
    image_path=None,
):
    try:
        user = Users.objects.get(identifiant=user_id)
        if username is not None:
            user.username = username
        if first_name is not None:
            user.first_name = first_name
        if last_name is not None:
            user.last_name = last_name
        if email is not None:
            user.email = email
        if post is not None:
            user.post = post
        if image_path:
            try:
                with open(image_path, "rb") as f:
                    user.image.save(os.path.basename(image_path), File(f), save=False)
            except FileNotFoundError as e:
                print(f"Fichier image introuvable: {e}")
            except Exception as e:
                print(f"Erreur lors de l'enregistrement de l'image: {e}")
        try:
            user.save()
        except IntegrityError as e:
            print(f"Erreur sauvegarde utilisateur: {e}")
            return None
        return user
    except Users.DoesNotExist:
        return None
    except Exception as e:
        print(f"Erreur inattendue lors de la mise à jour de l'utilisateur: {e}")
        return None

@transaction.atomic
def delete_user(user_id):
    try:
        user = Users.objects.get(identifiant=user_id)
        image_path = (
            user.image.path if user.image and hasattr(user.image, "path") else None
        )
        user.delete()
        if image_path and os.path.isfile(image_path):
            try:
                os.remove(image_path)
            except PermissionError as e:
                print(f"Permission refusée pour supprimer le fichier: {e}")
            except Exception as e:
                print(f"Erreur suppression fichier: {e}")
        return True
    except Users.DoesNotExist:
        return False
    except Exception as e:
        print(f"Erreur inattendue lors de la suppression de l'utilisateur: {e}")
        return False
