from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.views.decorators.http import require_POST, require_GET
from django.conf import settings
from django.db import transaction
from django.db.models import Q, Count, Prefetch
from django.db.models.functions import TruncMonth
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank
from utilisateur.models import Notification, Users
from candidat.models import (
    CandidatCV,
    Competence,
    Configuration,
    QueryMatching,
    RapportCv,
)
from candidat.tasks import process_cv_task
import os
import json
import logging

import uuid
import unicodedata
import re
from difflib import get_close_matches
from pathlib import Path
import magic

logger = logging.getLogger(__name__)

FILTER_KEYS = ["ville", "statut", "competence", "langue", "domaine", "secteur"]
PAGE_SIZE = 12


def _extract_city(raw: str) -> str:
    if not raw or not isinstance(raw, str):
        return ""
    city = re.split(r"[,\-/|()]", raw)[0]
    return city.strip()


def _normalize_filter_value(value: str) -> str:
    if not value or not isinstance(value, str):
        return ""
    value = value.strip()
    return unicodedata.normalize("NFC", value.strip().capitalize())


def _normalize_filter_key(value: str) -> str:
    if not value or not isinstance(value, str):
        return ""
    nfd = unicodedata.normalize("NFD", value.strip().lower())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")

def _build_ville_canonical(values) -> dict[str, str]:
    from collections import Counter
    counter = Counter()
    for raw in values:
        city = _extract_city(raw)
        if city and isinstance(city, str):
            counter[city.strip()] += 1

    canonical: dict[str, str] = {}
    for city, _ in counter.most_common():
        key = _normalize_filter_key(city)
        if key not in canonical:
            canonical[key] = city
    return canonical


def _fuzzy_correct_ville(city: str, canonical_keys: list[str], cutoff: float = 0.82) -> str:
    key = _normalize_filter_key(city)
    matches = get_close_matches(key, canonical_keys, n=1, cutoff=cutoff)
    return matches[0] if matches else key


def _deduplicate_villes(values) -> list[str]:
    from collections import Counter

    raw_cities = []
    for raw in values:
        if not raw or not isinstance(raw, str):
            continue
        city = _extract_city(raw)
        if city:
            raw_cities.append(city)

    if not raw_cities:
        return []

    counter = Counter(raw_cities)
    unique_keys: dict[str, str] = {}
    for city, count in counter.most_common():
        key = _normalize_filter_key(city)
        if key not in unique_keys:
            unique_keys[key] = city

    all_keys = list(unique_keys.keys())
    parent: dict[str, str] = {k: k for k in all_keys}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: str, y: str):
        px, py = find(x), find(y)
        if px != py:
            freq_px = counter[unique_keys[px]]
            freq_py = counter[unique_keys[py]]
            if freq_px >= freq_py:
                parent[py] = px
            else:
                parent[px] = py

    for i, k1 in enumerate(all_keys):
        matches = get_close_matches(k1, all_keys, n=5, cutoff=0.82)
        for k2 in matches:
            if k1 != k2:
                union(k1, k2)

    groups: dict[str, str] = {}
    for key in all_keys:
        rep = find(key)
        if rep not in groups:
            groups[rep] = _normalize_filter_value(unique_keys[rep])

    return sorted(groups.values(), key=lambda v: _normalize_filter_key(v))


LANGUE_TRANSLATIONS: dict[str, str] = {
    # Anglais
    "english":    "Anglais",
    "anglais":    "Anglais",
    "ingles":     "Anglais",
    # Français
    "french":     "Français",
    "francais":   "Français",
    "français":   "Français",
    # Arabe
    "arabic":     "Arabe",
    "arabe":      "Arabe",
    # Espagnol
    "spanish":    "Espagnol",
    "espagnol":   "Espagnol",
    "espanol":    "Espagnol",
    # Allemand
    "german":     "Allemand",
    "allemand":   "Allemand",
    "deutsch":    "Allemand",
    # Italien
    "italian":    "Italien",
    "italien":    "Italien",
    # Portugais
    "portuguese": "Portugais",
    "portugais":  "Portugais",
    # Chinois
    "chinese":    "Chinois",
    "chinois":    "Chinois",
    "mandarin":   "Chinois",
    # Japonais
    "japanese":   "Japonais",
    "japonais":   "Japonais",
    # Coréen
    "korean":     "Coréen",
    "coreen":     "Coréen",
    "coréen":     "Coréen",
    # Amazigh / Berbère
    "amazigh":    "Amazigh",
    "tamazight":  "Amazigh",
    "berbere":    "Amazigh",
    "berbère":    "Amazigh",
    # Darija
    "darija":     "Darija",
    "dialecte":   "Darija",
    # Russe
    "russian":    "Russe",
    "russe":      "Russe",
    # Turc
    "turkish":    "Turc",
    "turc":       "Turc",
    "turque":     "Turc",
    # Ourdou
    "urdu":       "urdu",
    "ourdou":     "urdu",
    # Punjabi
    "punjabi":    "Punjabi",
    "panjabi":    "Punjabi",
    # Hindi
    "hindi":      "Hindi",
    # Néerlandais
    "dutch":      "Néerlandais",
    "neerlandais":"Néerlandais",
    # Suédois
    "swedish":    "Suédois",
    "suedois":    "Suédois",
}


def _normalize_langue(raw: str) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    key = _normalize_filter_key(raw)
    return LANGUE_TRANSLATIONS.get(key)


def _deduplicate_langues(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        if not raw or not isinstance(raw, str):
            continue
        canonical = _normalize_langue(raw)
        if canonical and canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return sorted(result)


def _get_param(request, key: str, default: str = "all") -> str:
    return (request.GET.get(key) or default).strip()


def _extract_jsonb_values(qs_values, list_key: str, field: str) -> set:
    result = set()
    for row in qs_values:
        if not isinstance(row, list):
            continue
        for item in row:
            if not isinstance(item, dict):
                continue
            val = (item.get(field) or "").strip()
            if val:
                result.add(val)
    return result


# ─────────────────────────────────────────────
# Notifications
# ─────────────────────────────────────────────

@login_required(login_url="utilisateur:login")
@require_POST
def mark_notification_read(request, notif_id):
    try:
        notif = Notification.objects.get(identifiant=notif_id)
        notif.lue = True
        notif.save(update_fields=["lue"])
        remaining = Notification.objects.filter(lue=False).count()
        return JsonResponse({"success": True, "remaining": remaining})
    except Notification.DoesNotExist:
        return JsonResponse({"success": False, "message": "Notification introuvable"}, status=404)
    except Exception as e:
        logger.exception("mark_notification_read : erreur inattendue — %s", e)
        return JsonResponse({"success": False, "message": str(e)}, status=500)


@login_required(login_url="utilisateur:login")
@require_POST
def mark_all_notifications_read(request):
    try:
        Notification.objects.filter(lue=False).update(lue=True)
        return JsonResponse({"success": True, "remaining": 0})
    except Exception as e:
        logger.exception("mark_all_notifications_read : erreur inattendue — %s", e)
        return JsonResponse({"success": False, "message": str(e)}, status=500)


# ─────────────────────────────────────────────
# Paramètres / Configuration
# ─────────────────────────────────────────────

@login_required(login_url="utilisateur:login")
def settings_view(request):
    try:
        conf = Configuration.get()
    except Exception as e:
        logger.exception("settings_view : impossible de charger la configuration — %s", e)
        return JsonResponse({"status": "error", "message": "Impossible de charger la configuration"}, status=500)

    if request.method == "POST":
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"status": "error", "message": "JSON invalide"}, status=400)

        try:
            conf.email_deliore           = body.get("email_deliore", "").strip()
            conf.password_email_deliore  = body.get("password_email_deliore", "").strip()
            conf.email_loader_status     = bool(body.get("email_loader_status", False))
            conf.email_processing_active = bool(body.get("email_processing_active", False))
            conf.save()

            return JsonResponse({"status": "success", "message": "Configuration sauvegardée"})

        except Exception as e:
            logger.exception("settings_view POST : erreur — %s", e)
            return JsonResponse({"status": "error", "message": str(e)}, status=500)

    # GET
    try:
        return render(request, "pages/parametre_page.html", {"conf": conf})
    except Exception as e:
        logger.exception("settings_view GET : erreur de rendu — %s", e)
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


@login_required(login_url="utilisateur:login")
@require_POST
def test_imap_view(request):
    try:
        import imaplib
        conf = Configuration.get()
        mail = imaplib.IMAP4_SSL(os.getenv("IMAP_SERVER"))
        mail.login(conf.email_deliore, conf.password_email_deliore)
        mail.select("inbox")
        status, msgs = mail.search(None, "UNSEEN")
        count = len(msgs[0].split())
        mail.logout()
        return JsonResponse({"status": "success", "message": f"{count} mail(s) non lu(s)"})
    except Exception as e:
        logger.exception("test_imap_view : erreur — %s", e)
        return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required(login_url="utilisateur:login")
@require_POST
def reindex_qdrant_view(request):
    try:
        from aiagent.recommender.recommender_sys import get_recommender
        rsys = get_recommender()
        rsys.full_reindex(CandidatCV.objects.all())
        return JsonResponse({"status": "success", "message": f"{CandidatCV.objects.count()} CVs réindexés"})
    except Exception as e:
        logger.exception("reindex_qdrant_view : erreur — %s", e)
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


@login_required(login_url="utilisateur:login")
@require_POST
def force_email_check_view(request):
    try:
        from candidat.email_utils import email_candidature_loader
        count = email_candidature_loader()
        return JsonResponse({"status": "success", "count": count})
    except Exception as e:
        logger.exception("force_email_check_view : erreur — %s", e)
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


# ─────────────────────────────────────────────
# Pages simples
# ─────────────────────────────────────────────

@login_required(login_url="utilisateur:login")
def message_view(request):
    tag_filter = request.GET.get('tag', 'all')
    lue_filter = request.GET.get('lue', 'all')

    qs = Notification.objects.all()

    if tag_filter != 'all':
        qs = qs.filter(tag=tag_filter)
    if lue_filter == 'lues':
        qs = qs.filter(lue=True)
    elif lue_filter == 'non_lues':
        qs = qs.filter(lue=False)

    Notification.objects.filter(lue=False).update(lue=True)

    paginator = Paginator(qs, 20)
    page_obj  = paginator.get_page(request.GET.get('page'))

    total_count   = qs.count()
    unread_count  = Notification.objects.filter(lue=False).count()

    return render(request, "pages/message_page.html", {
        'page_obj'     : page_obj,
        'total_count'  : total_count,
        'unread_count' : unread_count,
        'tag_filter'   : tag_filter,
        'lue_filter'   : lue_filter,
        'tags'         : [('Infos','Infos'), ('Succes','Succès'), ('Error','Erreur'), ('Warning','Avertissement')],
    })


@login_required(login_url="utilisateur:login")
@require_GET
def compte_view(request):
    try:
        return render(request, "pages/compte_page.html")
    except Exception as e:
        logger.exception("compte_view : erreur de rendu — %s", e)
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


# ─────────────────────────────────────────────
# Agents
# ─────────────────────────────────────────────

@login_required(login_url="utilisateur:login")
@require_GET
def view_agents(request):
    try:
        email  = request.GET.get("email",  "tous").strip()
        status = request.GET.get("status", "tous").strip()

        qs = Users.objects.exclude(is_superuser=True)

        if status == "actif":
            qs = qs.filter(is_active=True)
        elif status == "inactif":
            qs = qs.filter(is_active=False)

        if email != "tous" and email:
            qs = qs.filter(email__icontains=email)

        paginator = Paginator(qs, 4)
        page_obj  = paginator.get_page(request.GET.get("page"))

        return render(request, "pages/agent_page.html", {
            "page_obj": page_obj,
            "f_email":  email  if email  != "tous" else "",
            "f_status": status if status != "tous" else "",
        })
    except Exception as e:
        logger.exception("view_agents : erreur — %s", e)
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


@login_required(login_url="utilisateur:login")
@require_POST
def create_agent(request):
    try:
        last_name  = request.POST.get("last_name",  "").strip()
        first_name = request.POST.get("first_name", "").strip()
        email      = request.POST.get("email",      "").strip()
        post       = request.POST.get("post",       "").strip()
        password   = request.POST.get("password",   "").strip()
        image      = request.FILES.get("image")

        if not all([last_name, first_name, email, post, password]):
            return JsonResponse({"success": False, "message": "Tous les champs obligatoires doivent être remplis."})

        if Users.objects.filter(email=email).exists():
            return JsonResponse({"success": False, "message": "Cet email est déjà utilisé."})

        agent = Users(
            username   = email,
            last_name  = last_name,
            first_name = first_name,
            email      = email,
            post       = post,
            is_active  = True,
        )
        agent.password = make_password(password)
        if image:
            agent.image = image
        agent.save()

        return JsonResponse({"success": True, "message": f"Agent {first_name} {last_name} créé avec succès."})

    except Exception as e:
        logger.exception("create_agent : erreur — %s", e)
        return JsonResponse({"success": False, "message": str(e)}, status=500)


# ─────────────────────────────────────────────
# Analyse
# ─────────────────────────────────────────────

@login_required(login_url="utilisateur:login")
@require_GET
def analyse_view(request):
    try:
        datatemp = {
            "poste": {
                "titre_professionnel": "",
                "localisation": "",
                "type_contrat": ""
            },
            "profil_recherche": "",
            "formation_requise": [],
            "experience_requise": [
                {
                    "poste": "",
                    "missions_attendues": []
                }
            ],
            "competences_requises": {
                "savoir_faire": [],
                "savoir_etre": [],
                "langues": [
                    {"langue": "", "niveau": ""},
                ]
            },
            "projets_valorises": [],
            "certifications_valorisees": []
        }
        return render(request, "pages/analyse_page.html", {"datatemp": datatemp})
    except Exception as e:
        logger.exception("analyse_view : erreur — %s", e)
        raise


# ─────────────────────────────────────────────
# Rapports / Candidats
# ─────────────────────────────────────────────
@login_required(login_url="utilisateur:login")
@require_GET
def repport_view(request):
    try:
        q_search      = _get_param(request, "q", "")
        f_ville       = _get_param(request, "ville")
        f_statut      = _get_param(request, "statut")
        f_competence  = _get_param(request, "competence")
        f_langue      = _get_param(request, "langue")
        f_domaine     = _get_param(request, "domaine")
        f_secteur     = _get_param(request, "secteur")
        f_niveau      = _get_param(request, "niveau")
        f_contrat     = _get_param(request, "contrat")
        f_pays        = _get_param(request, "pays")
        page_number   = _get_param(request, "page", "1")

        qs = (
            CandidatCV.objects
            .prefetch_related(
                Prefetch("competences", queryset=Competence.objects.only("nom"))
            )
            .only(
                "candidat_id", "nom_complet", "titre_professionnel",
                "ville", "pays", "email", "telephone",
                "etat_analyse", "donnees_structurees",
                "fichier_pdf_origine", "domaine", "secteur",
                "niveau", "contrat_souhaite",
            )
        )

        # ── Filtres champs directs ────────────────────────────────────────
        if f_niveau != "all":
            qs = qs.filter(niveau__icontains=f_niveau)

        if f_statut != "all":
            qs = qs.filter(etat_analyse=f_statut)

        if f_domaine != "all":
            qs = qs.filter(domaine__icontains=f_domaine)

        if f_secteur != "all":
            qs = qs.filter(secteur__icontains=f_secteur)

        if f_contrat != "all":
            qs = qs.filter(contrat_souhaite__icontains=f_contrat)

        if f_pays != "all":
            qs = qs.filter(pays__icontains=f_pays)

        # ── Filtre ville (fuzzy) ──────────────────────────────────────────
        if f_ville != "all":
            all_villes_raw = (
                CandidatCV.objects
                .exclude(ville="").exclude(ville__isnull=True)
                .values_list("ville", flat=True)
            )
            canonical         = _build_ville_canonical(all_villes_raw)
            canonical_keys    = list(canonical.keys())
            corrected_key     = _fuzzy_correct_ville(f_ville, canonical_keys)
            corrected_display = canonical.get(corrected_key, f_ville)
            qs = qs.filter(ville__istartswith=corrected_display)

        # ── Filtre compétence ─────────────────────────────────────────────
        if f_competence != "all":
            qs = qs.filter(competences__nom=f_competence.lower())

        # ── Filtre langue (JSONB — chemin direct) ─────────────────────────
        if f_langue != "all":
            canonical = _normalize_langue(f_langue) or f_langue
            qs = qs.filter(
                Q(donnees_structurees__langues__contains=[{"langue": canonical}]) |
                Q(donnees_structurees__langues__contains=[{"langue": f_langue}])
            )

        # ── Full-text search ──────────────────────────────────────────────
        if q_search:
            try:
                vector = (
                    SearchVector("nom_complet",         weight="A", config="french") +
                    SearchVector("titre_professionnel", weight="B", config="french") +
                    SearchVector("resume_profil",       weight="C", config="french")
                )
                query = SearchQuery(q_search, config="french")
                qs = (
                    qs.annotate(rank=SearchRank(vector, query))
                      .filter(rank__gte=0.01)
                      .order_by("-rank")
                )
            except Exception as exc:
                logger.warning("Full-text search échoué pour '%s' : %s", q_search, exc)
                qs = qs.filter(nom_complet__icontains=q_search)

        qs = qs.distinct()
        total_count = qs.count()

        # ── Pagination ────────────────────────────────────────────────────
        paginator = Paginator(qs, PAGE_SIZE)
        try:
            page_obj = paginator.page(page_number)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)

        # ── Données pour les filtres ──────────────────────────────────────
        all_villes_raw = (
            CandidatCV.objects
            .exclude(ville="").exclude(ville__isnull=True)
            .values_list("ville", flat=True)
        )

        all_niveaux = (
            CandidatCV.objects
            .exclude(niveau="").exclude(niveau__isnull=True)
            .values_list("niveau", flat=True)
            .distinct().order_by("niveau")
        )

        all_domaines = (
            CandidatCV.objects
            .exclude(domaine="").exclude(domaine__isnull=True)
            .values_list("domaine", flat=True)
            .distinct().order_by("domaine")
        )

        all_secteurs = (
            CandidatCV.objects
            .exclude(secteur="").exclude(secteur__isnull=True)
            .values_list("secteur", flat=True)
            .distinct().order_by("secteur")
        )

        all_contrats = (
            CandidatCV.objects
            .exclude(contrat_souhaite="").exclude(contrat_souhaite__isnull=True)
            .values_list("contrat_souhaite", flat=True)
            .distinct().order_by("contrat_souhaite")
        )

        all_pays = (
            CandidatCV.objects
            .exclude(pays="").exclude(pays__isnull=True)
            .values_list("pays", flat=True)
            .distinct().order_by("pays")
        )

        all_competences = Competence.objects.only("nom").order_by("nom")

        # ── Langues depuis JSONB (chemin direct) ──────────────────────────
        raw_langues = (
            CandidatCV.objects
            .filter(donnees_structurees__langues__isnull=False)
            .values_list("donnees_structurees__langues", flat=True)
        )
        langues_set = _extract_jsonb_values(raw_langues, list_key=None, field="langue")

        FILTER_KEYS = [
            "ville", "statut", "competence", "langue",
            "domaine", "secteur", "niveau", "contrat", "pays"
        ]
        has_filters = (
            any(_get_param(request, k) != "all" for k in FILTER_KEYS)
            or bool(q_search)
        )

        context = {
            "candidats":    page_obj,
            "page_obj":     page_obj,
            "total_count":  total_count,
            "competences":  all_competences,
            "statuts":      CandidatCV._meta.get_field("etat_analyse").choices,
            "all_villes":   _deduplicate_villes(all_villes_raw),
            "all_langues":  _deduplicate_langues(langues_set),
            "all_domaines": list(all_domaines),
            "all_secteurs": list(all_secteurs),
            "all_niveaux":  list(all_niveaux),
            "all_contrats": list(all_contrats),
            "all_pays":     list(all_pays),
            # filtres actifs
            "f_ville":      f_ville,
            "f_statut":     f_statut,
            "f_competence": f_competence,
            "f_langue":     f_langue,
            "f_domaine":    f_domaine,
            "f_secteur":    f_secteur,
            "f_niveau":     f_niveau,
            "f_contrat":    f_contrat,
            "f_pays":       f_pays,
            "q_search":     q_search,
            "has_filters":  has_filters,
        }

        return render(request, "pages/rapports_page.html", context)

    except Exception as exc:
        logger.exception("repport_view : erreur inattendue — %s", exc)
        raise

# ─────────────────────────────────────────────
# Documents CV
# ─────────────────────────────────────────────

@login_required(login_url="utilisateur:login")
@require_GET
def document_view(request):
    try:
        q         = (request.GET.get("q")     or "").strip()
        year_str  = (request.GET.get("year")  or "").strip()
        month_str = (request.GET.get("month") or "").strip()

        qs = (
            CandidatCV.objects
            .only(
                "candidat_id", "nom_complet", "titre_professionnel",
                "preview_image", "fichier_pdf_origine",
                "etat_analyse", "date_importation",
            )
            .exclude(preview_image="")
            .exclude(preview_image__isnull=True)
            .order_by("-date_importation")
        )

        if q:
            qs = qs.filter(
                Q(nom_complet__icontains=q) |
                Q(titre_professionnel__icontains=q)
            )

        year = None
        if year_str:
            try:
                year = int(year_str)
                if year < 2000 or year > 2100:
                    raise ValueError("Année hors plage")
                qs = qs.filter(date_importation__year=year)
            except ValueError:
                year = None

        month = None
        if month_str:
            try:
                month = int(month_str)
                if month < 1 or month > 12:
                    raise ValueError("Mois invalide")
                qs = qs.filter(date_importation__month=month)
            except ValueError:
                month = None

        available_years = (
            CandidatCV.objects
            .exclude(preview_image="")
            .exclude(preview_image__isnull=True)
            .dates("date_importation", "year", order="DESC")
        )
        years_list = [d.year for d in available_years]

        paginator   = Paginator(qs, PAGE_SIZE)
        page_number = (request.GET.get("page") or "1").strip()

        try:
            page_obj = paginator.page(page_number)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)

        return render(request, "pages/documents_cv_page.html", {
            "page_obj":        page_obj,
            "q":               q,
            "selected_year":   year,
            "selected_month":  month,
            "available_years": years_list,
        })

    except Exception as exc:
        logger.exception("document_view : erreur inattendue — %s", exc)
        raise

# ─────────────────────────────────────────────
# Suppression candidat
# ─────────────────────────────────────────────

@login_required(login_url="utilisateur:login")
@require_POST
def delete_candidat_view(request, candidat_id):
    try:
        candidat = CandidatCV.objects.get(candidat_id=candidat_id)

        if candidat.fichier_pdf_origine:
            try:
                if os.path.exists(candidat.fichier_pdf_origine.path):
                    os.remove(candidat.fichier_pdf_origine.path)
            except Exception as e:
                logger.warning("delete_candidat_view : erreur suppression PDF — %s", e)

        if candidat.preview_image:
            try:
                if os.path.exists(candidat.preview_image.path):
                    os.remove(candidat.preview_image.path)
            except Exception as e:
                logger.warning("delete_candidat_view : erreur suppression preview — %s", e)

        candidat.delete()
        return JsonResponse({"status": "success"})

    except CandidatCV.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Candidat introuvable"}, status=404)
    except Exception as e:
        logger.exception("delete_candidat_view : erreur — %s", e)
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


# ─────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────

@login_required(login_url="utilisateur:login")
@require_GET
def dashboard_view(request):
    try:
        users_count            = Users.objects.filter(is_superuser=False).count()
        candidats_count        = CandidatCV.objects.count()
        candidats_termin_count = CandidatCV.objects.filter(etat_analyse="termine").count()
        rapport_count          = RapportCv.objects.count()

        qs_line = (
            CandidatCV.objects
            .annotate(month=TruncMonth("date_importation"))
            .values("month")
            .annotate(count=Count("candidat_id"))
            .order_by("month")
        )
        mois_fr = {
            1: "Jan", 2: "Fév", 3: "Mar", 4: "Avr", 5: "Mai", 6: "Jun",
            7: "Jul", 8: "Aoû", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Déc"
        }
        line_labels, line_values = [], []
        for row in qs_line:
            if row["month"]:
                line_labels.append(f"{mois_fr[row['month'].month]} {row['month'].year}")
                line_values.append(row["count"])

        qs_pie = (
            CandidatCV.objects
            .values("etat_analyse")
            .annotate(count=Count("candidat_id"))
        )
        labels_map = {"en_attente": "En attente", "en_cours": "En cours", "termine": "Terminé"}
        colors_map = {
            "en_attente": "#93c5fd",
            "en_cours":   "#f97316",
            "termine":    "#10b981",
        }
        pie_labels, pie_values, pie_colors = [], [], []
        for row in qs_pie:
            k = row["etat_analyse"]
            pie_labels.append(labels_map.get(k, k))
            pie_values.append(row["count"])
            pie_colors.append(colors_map.get(k, "#93c5fd"))

        qs_secteur = (
            CandidatCV.objects
            .exclude(secteur__isnull=True).exclude(secteur__exact="")
            .values("secteur")
            .annotate(count=Count("candidat_id"))
            .order_by("-count")[:12]
        )
        secteur_labels = [row["secteur"] for row in qs_secteur]
        secteur_values = [row["count"]   for row in qs_secteur]

        qs_domaine = (
            CandidatCV.objects
            .exclude(domaine__isnull=True).exclude(domaine__exact="")
            .values("domaine")
            .annotate(count=Count("candidat_id"))
            .order_by("-count")[:12]
        )
        domaine_labels = [row["domaine"] for row in qs_domaine]
        domaine_values = [row["count"]   for row in qs_domaine]

        return render(request, "pages/dashboard_page.html", {
            "users_count"           : users_count,
            "candidats_count"       : candidats_count,
            "rapport_count"         : rapport_count,
            "candidats_termin_count": candidats_termin_count,
            "line_labels"           : json.dumps(line_labels),
            "line_values"           : json.dumps(line_values),
            "pie_labels"            : json.dumps(pie_labels),
            "pie_values"            : json.dumps(pie_values),
            "pie_colors"            : json.dumps(pie_colors),
            "secteur_labels"        : json.dumps(secteur_labels),
            "secteur_values"        : json.dumps(secteur_values),
            "domaine_labels"        : json.dumps(domaine_labels),
            "domaine_values"        : json.dumps(domaine_values),
        })

    except Exception as e:
        logger.exception("dashboard_view : erreur — %s", e)
        raise


# ─────────────────────────────────────────────
# Mes traitements
# ─────────────────────────────────────────────

@login_required(login_url="utilisateur:login")
@require_GET
def mes_traitements_view(request):
    try:
        q      = request.GET.get("q", "").strip()
        statut = request.GET.get("statut", "").strip()

        candidats_qs = (
            CandidatCV.objects
            .filter(
                agent_analyse_cv=request.user,
                etat_analyse__in=["en_cours", "termine"]
            )
            .only(
                "candidat_id", "nom_complet", "email", "telephone",
                "etat_analyse", "fichier_pdf_origine", "date_importation"
            )
            .order_by("-date_importation")
        )

        if q:
            candidats_qs = candidats_qs.filter(
                Q(nom_complet__icontains=q) | Q(email__icontains=q)
            )
        if statut in ["en_cours", "termine"]:
            candidats_qs = candidats_qs.filter(etat_analyse=statut)

        paginator = Paginator(candidats_qs, 10)
        page_obj  = paginator.get_page(request.GET.get("page"))

        rapports_ids = set(
            RapportCv.objects
            .filter(candidatcv__in=candidats_qs)
            .values_list("candidatcv_id", flat=True)
        )

        last_query = (
            QueryMatching.objects
            .filter(agent_deliore=request.user)
            .order_by("-date_importation")
            .values_list("content", flat=True)
            .first()
        )

        return render(request, "pages/mes_traitements_page.html", {
            "page_obj":     page_obj,
            "rapports_ids": rapports_ids,
            "last_query":   last_query,
            "q":            q,
            "f_statut":     statut,
        })

    except Exception as e:
        logger.exception("mes_traitements_view : erreur — %s", e)
        raise


@login_required(login_url="utilisateur:login")
@require_POST
def traitement_terminer_view(request, candidat_id):
    try:
        candidat = CandidatCV.objects.get(candidat_id=candidat_id)
        candidat.etat_analyse          = "termine"
        candidat.etat_analyse_termine  = True
        candidat.save(update_fields=["etat_analyse", "etat_analyse_termine"])
        return JsonResponse({"status": "success"})
    except CandidatCV.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Introuvable"}, status=404)
    except Exception as e:
        logger.exception("traitement_terminer_view : erreur — %s", e)
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


@login_required(login_url="utilisateur:login")
@require_POST
def traitement_annuler_view(request, candidat_id):
    try:
        candidat = CandidatCV.objects.get(candidat_id=candidat_id)
        candidat.etat_analyse = "en_attente"
        candidat.agent_analyse_cv.remove(request.user)
        candidat.save(update_fields=["etat_analyse"])
        return JsonResponse({"status": "success"})
    except CandidatCV.DoesNotExist:
        return JsonResponse({"status": "error", "message": "Introuvable"}, status=404)
    except Exception as e:
        logger.exception("traitement_annuler_view : erreur — %s", e)
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


logger = logging.getLogger(__name__)

CV_EXTENSIONS  = {".pdf", ".docx", ".doc"}
MAX_FILE_SIZE  = 10 * 1024 * 1024   # 10 Mo
ALLOWED_MIMES  = {"application/pdf"}


def _new_session_folder() -> Path:
    """Crée et retourne un dossier session UUID sous cv_temps/."""
    session_id     = uuid.uuid4().hex
    session_folder = Path(settings.MEDIA_ROOT) / "cv_temps" / session_id
    session_folder.mkdir(parents=True, exist_ok=True)
    return session_folder


def _write_file(dest: Path, django_file) -> None:
    """Écrit un fichier Django en chunks avec fsync."""
    with open(dest, "wb") as f:
        for chunk in django_file.chunks():
            f.write(chunk)
        f.flush()
        os.fsync(f.fileno())


# ─────────────────────────────────────────────────────────────────────────────
# Upload fichier unique
# ─────────────────────────────────────────────────────────────────────────────

@login_required(login_url="utilisateur:login")
@require_POST
def view_upload_cv(request):
    """
    Upload d'un CV PDF unique.

    Validations :
        - Présence du fichier
        - MIME réel = application/pdf (magic bytes, pas l'extension)
        - Taille ≤ 10 Mo

    Chaque upload obtient son propre dossier session UUID pour isoler
    les fichiers et permettre un nettoyage propre après traitement.
    """
    fichier = request.FILES.get("path_cv")

    if not fichier:
        return JsonResponse(
            {"status": "error", "message": "Aucun fichier reçu."}, status=400
        )

    # ── Validation MIME ───────────────────────────────────────────────────────
    mime = magic.from_buffer(fichier.read(2048), mime=True)
    fichier.seek(0)

    if mime not in ALLOWED_MIMES:
        return JsonResponse(
            {"status": "error", "message": "Format PDF uniquement."}, status=400
        )

    # ── Validation taille ─────────────────────────────────────────────────────
    if fichier.size > MAX_FILE_SIZE:
        return JsonResponse(
            {"status": "error", "message": "Fichier trop volumineux (10 Mo max)."}, status=400
        )

    # ── Sauvegarde dans une session isolée ────────────────────────────────────
    try:
        session_folder = _new_session_folder()

        # Nom opaque UUID.pdf — on n'expose jamais le nom original sur le FS
        safe_name = f"{uuid.uuid4().hex}.pdf"
        chemin    = session_folder / safe_name

        _write_file(chemin, fichier)

        task = process_cv_task.delay(str(chemin), session_folder=str(session_folder))

        logger.info(
            "[UPLOAD] CV reçu → session=%s task=%s", session_folder.name, task.id
        )
        return JsonResponse(
            {
                "status":   "success",
                "task_id":  task.id,
                "message":  "CV uploadé et en cours de traitement.",
            }
        )

    except Exception as exc:
        logger.exception("[UPLOAD] Erreur inattendue : %s", exc)
        return JsonResponse(
            {"status": "error", "message": str(exc)}, status=500
        )


# ─────────────────────────────────────────────────────────────────────────────
# Import dossier (plusieurs fichiers)
# ─────────────────────────────────────────────────────────────────────────────

@login_required(login_url="utilisateur:login")
@require_POST
def import_folder_view(request):
    """
    Import de plusieurs CVs en une fois.

    Tous les fichiers d'un même import sont placés dans un dossier session UUID
    commun. Chaque fichier est traité indépendamment par process_cv_task.
    Le dossier session est supprimé automatiquement lorsque le dernier fichier
    a été traité avec succès (_cleanup_session_if_empty dans process_cv_task).

    Extensions acceptées : .pdf, .docx, .doc
    """
    uploaded = request.FILES.getlist("files")

    if not uploaded:
        return JsonResponse(
            {"status": "error", "message": "Aucun fichier reçu."}, status=400
        )

    # ── Dossier session partagé pour cet import ───────────────────────────────
    session_folder = _new_session_folder()
    saved_paths    = []
    skipped        = []

    for f in uploaded:
        ext = Path(f.name).suffix.lower()

        if ext not in CV_EXTENSIONS:
            skipped.append(f.name)
            logger.info("[IMPORT] Extension ignorée : %s", f.name)
            continue

        # Nom opaque pour éviter tout path traversal ou collision
        safe_name = f"{uuid.uuid4().hex}{ext}"
        dest      = session_folder / safe_name

        try:
            _write_file(dest, f)
            saved_paths.append(str(dest))
            logger.info("[IMPORT] Sauvegardé : %s (original: %s)", safe_name, f.name)
        except OSError as exc:
            logger.exception("[IMPORT] Erreur écriture %s : %s", f.name, exc)

    # ── Rollback si aucun fichier valide ─────────────────────────────────────
    if not saved_paths:
        import shutil
        shutil.rmtree(str(session_folder), ignore_errors=True)
        return JsonResponse(
            {"status": "error", "message": "Aucun fichier valide reçu."}, status=400
        )

    # ── Dispatch des tâches ───────────────────────────────────────────────────
    task_ids = []
    for path in saved_paths:
        task = process_cv_task.delay(path, session_folder=str(session_folder))
        task_ids.append(task.id)

    logger.info(
        "[IMPORT] Session=%s — %d fichier(s) dispatchés, %d ignoré(s)",
        session_folder.name,
        len(saved_paths),
        len(skipped),
    )

    return JsonResponse(
        {
            "status":     "started",
            "file_count": len(saved_paths),
            "task_ids":   task_ids,
            "skipped":    skipped,
            "message":  "CV uploadé et en cours de traitement.",
        }
    )