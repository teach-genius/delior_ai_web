from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.db.models import Case, When
from django.views.decorators.http import  require_POST
from aiagent.recommender.recommender_sys import recommendation_candidat_cv_impl
from aiagent.processing.chaine_redaction_offre import build_job_offer_from_query
from aiagent.agents.graph import ask_assistant

from .utils import generate_html_report_tool, generate_repport
from .models import CandidatCV, QueryMatching, RapportCv

import json
import logging

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _safe_str(val) -> str:
    return val.strip() if isinstance(val, str) else ""

# ─────────────────────────────────────────────────────────────
# Vue : matching CV
# ─────────────────────────────────────────────────────────────

@login_required(login_url="utilisateur:login")
@require_POST
def cv_math_view(request):
    cvs = CandidatCV.objects.none()

    try:
        # ── Parse body ────────────────────────────────────────
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("cv_math_view : body JSON invalide — %s", exc)
            return render(request, "includes/_top_match.html", {"cvs": cvs})

        if not isinstance(data, dict):
            logger.warning("cv_math_view : body JSON n'est pas un objet")
            return render(request, "includes/_top_match.html", {"cvs": cvs})

        mode  = _safe_str(data.get("mode")) or "query"
        offre = data.get("offre") or {}

        # ── Construction query ────────────────────────────────
        if mode == "offre":
            query = offre
            logger.debug("cv_math_view : query offre =\n%s", query)
        else:
            query = build_job_offer_from_query(data.get("query"))

        if not query:
            logger.info("cv_math_view : query vide, aucun matching effectué")
            return render(request, "includes/_top_match.html", {"cvs": cvs})

        logger.info("cv_math_view [%s] query = %.120s…", mode, query)

        try:
            QueryMatching.objects.create(content=query, agent_deliore=request.user)
        except Exception as exc:
            logger.warning("cv_math_view : QueryMatching non sauvegardé — %s", exc)

        try:
            candidat_hits = recommendation_candidat_cv_impl(query, 5)
        except Exception as exc:
            logger.exception("cv_math_view : erreur matching vectoriel — %s", exc)
            return render(request, "includes/_top_match.html", {"cvs": cvs})

        if not candidat_hits:
            logger.info("cv_math_view : aucun hit vectoriel retourné")
            return render(request, "includes/_top_match.html", {"cvs": cvs})

        hit_ids = []
        for hit in candidat_hits:
            try:
                cid = hit[0].payload.get("candidat_id")
                if cid is not None:
                    hit_ids.append(cid)
            except (IndexError, AttributeError, TypeError) as exc:
                logger.warning("cv_math_view : hit malformé ignoré — %s", exc)

        if not hit_ids:
            logger.info("cv_math_view : aucun candidat_id extrait des hits")
            return render(request, "includes/_top_match.html", {"cvs": cvs})

        ordering = Case(
            *[When(candidat_id=pk, then=pos) for pos, pk in enumerate(hit_ids)]
        )
        cvs = (
            CandidatCV.objects
            .filter(candidat_id__in=hit_ids)
            .exclude(preview_image="")
            .exclude(preview_image__isnull=True)
            .order_by(ordering)
        )

        logger.info("cv_math_view : %d candidat(s) retourné(s)", cvs.count())

    except Exception as exc:
        logger.exception("cv_math_view : erreur inattendue — %s", exc)
        cvs = CandidatCV.objects.none()

    return render(request, "includes/_top_match.html", {"cvs": cvs})


# ─────────────────────────────────────────────────────────────
# Vue : génération rapport
# ─────────────────────────────────────────────────────────────

@login_required(login_url="utilisateur:login")
@require_POST
def gen_repport_view(request):
    try:
        data        = json.loads(request.body)
        identifiant = data.get("identifiant", "").strip()

        last_query_content = (
            QueryMatching.objects
            .filter(agent_deliore=request.user)
            .order_by("-date_importation")
            .values_list("content", flat=True)
            .first()
        )
        print(last_query_content)
        cv_candidat = get_object_or_404(CandidatCV, candidat_id=identifiant)
        cv_data     = cv_candidat.donnees_structurees

        rapport_json_str = generate_repport(cv_data, last_query_content)
        rapport          = rapport_json_str if isinstance(rapport_json_str, dict) else json.loads(rapport_json_str)

        rapport["agent"] = {
            "first_name": request.user.first_name,
            "last_name" : request.user.last_name,
            "post"      : request.user.post,
            "image"     : request.user.image.url if request.user.image else None,
            "email"     : request.user.email,
        }

        candidat_repport, created = RapportCv.objects.update_or_create(
            candidatcv=cv_candidat,
            defaults={"contenu": rapport},
        )

        cv_candidat.etat_analyse = "en_cours"
        cv_candidat.agent_analyse_cv.add(request.user)
        cv_candidat.save()

        return JsonResponse({"status": "success", "created": created})

    except Exception as exc:
        logger.exception("gen_repport_view : erreur — %s", exc)
        return JsonResponse({"status": "error", "message": str(exc)}, status=500)


# ─────────────────────────────────────────────────────────────
# Vue : affichage rapport HTML
# ─────────────────────────────────────────────────────────────

@login_required(login_url="utilisateur:login")
@require_POST
def view_rapport(request):
    try:
        data        = json.loads(request.body)
        identifiant = data.get("identifiant", "").strip()

        rapport = (
            RapportCv.objects
            .filter(candidatcv=identifiant)
            .values_list("contenu", flat=True)
            .first()
        )

        html = generate_html_report_tool(rapport, "objets/rapport_template.html")
        return JsonResponse({"html": html, "status": 200})

    except Exception as exc:
        logger.exception("view_rapport : erreur — %s", exc)
        return JsonResponse({"error": str(exc)}, status=500)


# ─────────────────────────────────────────────────────────────
# Vue : URL du CV original
# ─────────────────────────────────────────────────────────────

@login_required(login_url="utilisateur:login")
@require_POST
def view_cv_view(request):
    try:
        data        = json.loads(request.body)
        identifiant = data.get("identifiant", "").strip()

        cv = get_object_or_404(CandidatCV, candidat_id=identifiant)
        return JsonResponse({"url": cv.fichier_pdf_origine.url, "status": 200})

    except Exception as exc:
        logger.exception("view_cv_view : erreur — %s", exc)
        return JsonResponse({"error": str(exc)}, status=500)


# ─────────────────────────────────────────────────────────────
# Vue : chatbot
# ─────────────────────────────────────────────────────────────
@require_POST
async def chat_bot_view(request):
    try:
        data         = json.loads(request.body)
        user_message = data.get("query", "").strip()

        if not user_message:
            return JsonResponse({"status": "error", "message": "Message vide"}, status=400)

        user_context = {
            "id"   : str(request.user.identifiant)
        }

        bot_response = await ask_assistant(user_context, user_message)

        return JsonResponse({"status": "success", "response": bot_response})

    except Exception as exc:
        logger.exception("chat_bot_view : erreur — %s", exc)
        return JsonResponse({"status": "error", "message": "Erreur serveur"}, status=500)