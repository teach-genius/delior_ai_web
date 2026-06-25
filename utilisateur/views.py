from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.db import IntegrityError
from django.shortcuts import render, redirect
from django.utils.timezone import now
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from .utils import create_user_safe, is_valid_password
from .models import Users, Notification
from datetime import timedelta
import random
from django.utils.dateparse import parse_datetime
import json
from django.http import JsonResponse

# ── helpers OTP ──────────────────────────────────────────────────────────────
def _send_otp(request, user):
    code = str(random.randint(100000, 999999))
    request.session['otp_code']    = code
    request.session['otp_user_id'] = str(user.pk)
    request.session['otp_expiry']  = (now() + timedelta(minutes=10)).isoformat()

    try:
        send_mail(
            subject='Votre code de vérification — DeliorAI',
            message=f'Bonjour {user.first_name},\n\nVotre code de connexion est : {code}\n\nCe code est valable 10 minutes.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
    except Exception as e:
        messages.warning(request, f"Erreur lors de l'envoi du code OTP. Veuillez contacter l'administrateur.")

# ── Login Utilisateur ─────────────────────────────────────────────────────────
def view_login_user(request):
    if request.method == "POST":
        try:
            email    = request.POST.get("email", "").strip().lower()
            password = request.POST.get("password", "").strip()
            
            if not email or not password:
                messages.error(request, "Veuillez remplir tous les champs.")
                return redirect("utilisateur:login")

            user = authenticate(request, username=email, password=password)

            if user:
                if not user.is_active:
                    messages.error(request, "Votre compte est inactif ou en attente d'approbation.")
                    return redirect("utilisateur:login")

                _send_otp(request, user)
                
                messages.success(request, "Code de vérification envoyé par email.")
                return redirect("utilisateur:verify_otp")

            messages.error(request, "Email ou mot de passe incorrect.")
            return redirect("utilisateur:login")

        except Exception as e:
            messages.error(request, "Une erreur est survenue lors de la connexion.")
            return redirect("utilisateur:login")

    return render(request, "utilisateurs/pages/login_page.html")

# ── Vérification OTP ──────────────────────────────────────────────────────────
def verify_otp_view(request):
    if 'otp_user_id' not in request.session:
        messages.warning(request, "Aucune tentative de connexion en cours.")
        return redirect("utilisateur:login")

    if request.method == "POST":
        try:
            code_saisi   = request.POST.get("code", "").strip()
            code_attendu = request.session.get('otp_code')
            user_id      = request.session.get('otp_user_id')
            expiry_str   = request.session.get('otp_expiry')

            expiry = parse_datetime(expiry_str) if expiry_str else None
            
            if not expiry or now() > expiry:
                for key in ('otp_code', 'otp_user_id', 'otp_expiry'):
                    request.session.pop(key, None)
                messages.error(request, "Code expiré. Veuillez réessayer de vous connecter.")
                return redirect("utilisateur:login")

            if code_saisi == code_attendu:
                user = Users.objects.get(pk=user_id)
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')

                for key in ('otp_code', 'otp_user_id', 'otp_expiry'):
                    request.session.pop(key, None)

                messages.success(request, "Connexion réussie. Bienvenue.")
                return redirect("dashboard")
            else:
                messages.error(request, "Code incorrect. Veuillez vérifier votre email.")

        except Users.DoesNotExist:
            messages.error(request, "Utilisateur introuvable.")
            return redirect("utilisateur:login")
        except Exception as e:
            print(f"error verify_otp -> {e}")
            messages.error(request, "Une erreur est survenue lors de la vérification.")

    return render(request, "utilisateurs/pages/verify_otp_page.html")

# ── Inscription Utilisateur ───────────────────────────────────────────────────
def view_register_user(request):
    if request.method == "POST":
        try:
            last_name = request.POST.get("last_name", "").strip()
            first_name = request.POST.get("first_name", "").strip()
            email = request.POST.get("email", "").strip().lower()
            poste = request.POST.get("poste", "").strip()
            password = request.POST.get("password", "")
            password_confirm = request.POST.get("password_confirm", "")

            if not all([last_name, first_name, email, poste, password]):
                messages.error(request, "Tous les champs sont obligatoires.")
                return redirect("utilisateur:register")

            if password != password_confirm:
                messages.error(request, "Les mots de passe ne correspondent pas.")
                return redirect("utilisateur:register")

            if not is_valid_password(password):
                messages.error(request, "Le mot de passe doit contenir au moins 8 caractères, une majuscule et une minuscule.")
                return redirect("utilisateur:register")

            user, created = create_user_safe(
                email=email,          
                password=password,
                first_name=first_name,
                last_name=last_name,
                post=poste
            )
            
            if not created:
                messages.warning(request, "Un compte avec cet email existe déjà.")
                return redirect("utilisateur:register")
            
            user.is_active = False
            user.save()
            
            Notification.objects.create(
                content=f"Nouvelle inscription : {last_name} {first_name} ({email}) en attente d'approbation.",
                tag="Infos",
            )
            
            messages.success(request, "Compte créé avec succès. En attente d'activation par l'administrateur.")
            return redirect("utilisateur:login")

        except IntegrityError:
            messages.warning(request, "Cet email est déjà utilisé.")
            return redirect("utilisateur:register")
        except Exception as e:
            messages.error(request, "Une erreur est survenue lors de la création du compte.")
            return redirect("utilisateur:register")
  
    return render(request, "utilisateurs/pages/register_page.html")

# ── Gestion Profil (Photo) ────────────────────────────────────────────────────
@login_required(login_url="utilisateur:login")
def setprofile(request):
    if request.method == "POST":
        try:
            image = request.FILES.get("image")
            if image:
                request.user.image = image
                request.user.save()
                messages.success(request, "Photo de profil mise à jour avec succès.")
            else:
                messages.warning(request, "Aucune image sélectionnée.")
            return redirect("compte")
        except Exception as e:
            messages.error(request, "Erreur lors du téléchargement de la photo.")
            return redirect("compte")
    return redirect("compte")

# ── Mise à jour Infos Utilisateur ─────────────────────────────────────────────
@login_required(login_url="utilisateur:login")
def updateinfosuser(request):
    if request.method != "POST":
        return redirect("compte")

    try:
        email      = request.POST.get("email", "").strip().lower()
        last_name  = request.POST.get("last_name", "").strip()
        first_name = request.POST.get("first_name", "").strip()
        post       = request.POST.get("post", "").strip()

        if not email or "@" not in email:
            messages.error(request, "Adresse email invalide.")
            return redirect("compte")
        
        if Users.objects.filter(email=email).exclude(pk=request.user.pk).exists():
            messages.error(request, "Cet email est déjà utilisé par un autre compte.")
            return redirect("compte")

        user            = request.user
        user.last_name  = last_name
        user.first_name = first_name
        user.email      = email
        user.username   = email
        user.post       = post
        user.save()
        
        messages.success(request, "Informations personnelles mises à jour avec succès.")
        
    except Exception as e:
        messages.error(request, "Une erreur est survenue lors de la mise à jour.")

    return redirect("compte")

# ── Changement Mot de Passe ───────────────────────────────────────────────────
@login_required(login_url="utilisateur:login")
def setpassword(request):
    if request.method == "POST":
        try:
            user = request.user
            current_pswd = request.POST.get("current_pswd", "").strip()
            new_pswd = request.POST.get("new_pswd", "").strip()
            confirm_pswd = request.POST.get("confirm_pswd", "").strip()

            if not user.check_password(current_pswd):
                messages.error(request, "Mot de passe actuel incorrect.")
                return redirect("compte")
            
            if current_pswd == new_pswd:
                messages.warning(request, "Le nouveau mot de passe doit être différent de l'ancien.")
                return redirect("compte")
            
            if new_pswd != confirm_pswd:
                messages.error(request, "Les nouveaux mots de passe ne correspondent pas.")
                return redirect("compte")
            
            if not is_valid_password(new_pswd):
                messages.error(
                    request,
                    "Le mot de passe doit contenir au moins 8 caractères, une majuscule et une minuscule."
                )
                return redirect("compte")
            
            user.set_password(new_pswd)
            user.save()
            update_session_auth_hash(request, user)
            
            messages.success(request, "Mot de passe modifié avec succès.")
            return redirect("compte")
            
        except Exception as e:
            messages.error(request, "Une erreur est survenue lors du changement de mot de passe.")
            return redirect("compte")
            
    return redirect("compte")

# ── Déconnexion ───────────────────────────────────────────────────────────────
@login_required(login_url="utilisateur:login")
def view_logout(request):
    try:
        logout(request)
        messages.success(request, "Vous avez été déconnecté avec succès.")
        return redirect("utilisateur:login")
    except Exception as e:
        messages.error(request, "Une erreur est survenue lors de la déconnexion.")
        return redirect("utilisateur:login")

# ── Approbation Utilisateur (Admin) ───────────────────────────────────────────
@login_required(login_url="utilisateur:login")
def approb_user(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "message": "Méthode non autorisée"},
            status=405
        )
    try:
        data = json.loads(request.body)
        identifiant_ = data.get("identifiant", "").strip()

        if not identifiant_:
            return JsonResponse({
                "success": False,
                "message": "Identifiant manquant"
            }, status=400)

        user = Users.objects.filter(identifiant=identifiant_).first()

        if not user:
            return JsonResponse({
                "success": False,
                "message": "Utilisateur introuvable"
            }, status=404)

        user.is_active = True
        user.save()

        return JsonResponse({"success": True, "message": "Compte activé avec succès."})
        
    except Exception as e:
        return JsonResponse({
            "success": False,
            "message": "Erreur serveur",
            "error": str(e)
        }, status=500)

# ── Récupération Infos Utilisateur (Admin) ────────────────────────────────────
@login_required(login_url="utilisateur:login")
def get_info_user(request):
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "message": "Méthode non autorisée"},
            status=405
        )

    try:
        data = json.loads(request.body)
        identifiant_ = data.get("identifiant", "").strip()

        if not identifiant_:
            return JsonResponse({
                "success": False,
                "message": "Identifiant manquant"
            }, status=400)

        user = Users.objects.filter(identifiant=identifiant_).first()

        if not user:
            return JsonResponse({
                "success": False,
                "message": "Utilisateur introuvable"
            }, status=404)

        img_url = user.image.url if user.image else None

        return JsonResponse({
            "success": True,
            "user": {
                "identifiant": str(user.identifiant),
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "poste": user.post,
                "image_url": img_url,
                "is_active": user.is_active,
                "date_joined": user.date_joined.strftime('%d/%m/%Y à %H:%M')
            }
        })

    except Exception as e:
        return JsonResponse({
            "success": False,
            "message": "Erreur serveur",
            "error": str(e)
        }, status=500)

# ── Pages Diverses ────────────────────────────────────────────────────────────
def notfound_view(request,exception):
    try:
        return render(request, 'not_found.html', status=404)
    except Exception as e:
        return redirect("dashboard")

def forgot_password_view(request):
    try:
        return render(request, "utilisateurs/pages/forgot_password_page.html", {
            "success": False 
        })
    except Exception as e:
        messages.error(request, "Une erreur est survenue.")
        return redirect("utilisateur:login")
