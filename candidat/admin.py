from django.contrib import admin
from django.utils.html import format_html
from django.utils.timesince import timesince
from django.utils import timezone
from django.db.models import Count

from .models import Configuration, Competence, CandidatCV, QueryMatching, RapportCv


@admin.register(Configuration)

class ConfigurationAdmin(admin.ModelAdmin):
    list_display  = ('email_deliore', 'email_loader_status', 'email_processing_active')
    fieldsets = (
        ('Email IMAP', {
            'fields': ('email_deliore', 'password_email_deliore')
        }),
        ('Processus automatiques', {
            'fields': ('email_loader_status', 'email_processing_active')
        }),
    )
    def has_add_permission(self, request):
        return not Configuration.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


# ─────────────────────────────────────────────────────────────
# Competence
# ─────────────────────────────────────────────────────────────

@admin.register(Competence)
class CompetenceAdmin(admin.ModelAdmin):
    list_display   = ("nom", "nb_candidats")
    search_fields  = ("nom",)
    ordering       = ("nom",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _nb_candidats=Count("candidats")
        )

    @admin.display(description="Candidats", ordering="_nb_candidats")
    def nb_candidats(self, obj):
        return obj._nb_candidats


# ─────────────────────────────────────────────────────────────
# CandidatCV — inline compétences
# ─────────────────────────────────────────────────────────────

class CompetenceInline(admin.TabularInline):
    model              = CandidatCV.competences.through
    extra              = 0
    verbose_name       = "Compétence"
    verbose_name_plural = "Compétences"
    autocomplete_fields = ("competence",)


# ─────────────────────────────────────────────────────────────
# CandidatCV
# ─────────────────────────────────────────────────────────────

@admin.register(CandidatCV)
class CandidatCVAdmin(admin.ModelAdmin):

    # ── List view ────────────────────────────────────────────
    list_display = (
        "avatar_preview",
        "nom_complet",
        "titre_professionnel",
        "ville",
        "pays",
        "email",
        "telephone",
        "etat_badge",
        "domaine",
        "secteur",
        "niveau",
        "contrat_souhaite",
        "analyse_terminee",
        "actif_toggle",
        "nb_competences",
        "depuis",
    )
    list_display_links = ("avatar_preview", "nom_complet")
    list_filter = (
        "etat_analyse",
        "etat_analyse_termine",
        "actif",
        "ville",
        "pays",
        "domaine",
        "secteur",
        "niveau",
        "contrat_souhaite",
    )
    search_fields = (
        "nom_complet",
        "email",
        "telephone",
        "titre_professionnel",
        "ville",
        "pays",
        "domaine",
        "secteur",
        "niveau",
        "contrat_souhaite",
    )
    ordering = ("-date_importation",)
    list_per_page = 25
    date_hierarchy = "date_importation"
    show_full_result_count = True

    actions = ["marquer_termine", "marquer_en_attente", "activer", "desactiver"]

    # ── Detail view ───────────────────────────────────────────
    readonly_fields = (
        "candidat_id",
        "cv_preview_large",
        "date_importation",
        "competences_liste",
        "niveau",
    )

    fieldsets = (
        ("Identité", {
            "fields": (
                "cv_preview_large",
                ("nom_complet", "email"),
                ("telephone", "ville", "pays"),
                "titre_professionnel",
                "resume_profil",
            )
        }),
        ("Classification", {
            "fields": (
                ("domaine", "secteur"),
                ("niveau", "contrat_souhaite"),
            )
        }),
        ("Fichiers", {
            "fields": (
                "fichier_pdf_origine",
                "preview_image",
            ),
        }),
        ("Matching & Analyse", {
            "fields": (
                ("etat_analyse", "etat_analyse_termine", "actif"),
                "agent_analyse_cv",
                "competences_liste",
            )
        }),
        ("Données structurées", {
            "fields": ("donnees_structurees",),
            "classes": ("collapse",),
        }),
        ("Métadonnées", {
            "fields": ("candidat_id", "date_importation"),
            "classes": ("collapse",),
        }),
    )

    filter_horizontal = ("agent_analyse_cv", "competences")

    # ── Colonnes list ─────────────────────────────────────────

    @admin.display(description="")
    def avatar_preview(self, obj):
        if obj.preview_image:
            return format_html(
                '<img src="{}" style="width:44px;height:56px;object-fit:cover;'
                'border-radius:6px;border:1.5px solid #e4e9f2;'
                'box-shadow:0 2px 6px rgba(0,0,0,.1);" />',
                obj.preview_image.url,
            )
        return format_html(
            '<div style="width:44px;height:56px;border-radius:6px;'
            'background:#f4f6fb;border:1.5px dashed #d0d8ea;'
            'display:flex;align-items:center;justify-content:center;'
            'font-size:18px;color:#c0c8d8;">👤</div>'
        )

    @admin.display(description="État", ordering="etat_analyse")
    def etat_badge(self, obj):
        colors = {
            "en_attente": ("#fef3c7", "#d97706", "⏳"),
            "en_cours":   ("#dbeafe", "#2563eb", "🔄"),
            "termine":    ("#dcfce7", "#16a34a", "✅"),
        }
        bg, fg, icon = colors.get(obj.etat_analyse, ("#f3f4f6", "#6b7280", "❓"))
        label = obj.get_etat_analyse_display()
        return format_html(
            '<span style="background:{};color:{};padding:3px 10px;'
            'border-radius:20px;font-size:11px;font-weight:600;">'
            '{} {}</span>',
            bg, fg, icon, label,
        )

    @admin.display(description="✔ Terminé", boolean=True, ordering="etat_analyse_termine")
    def analyse_terminee(self, obj):
        return obj.etat_analyse_termine

    @admin.display(description="Actif", boolean=True, ordering="actif")
    def actif_toggle(self, obj):
        return obj.actif

    @admin.display(description="Compétences")
    def nb_competences(self, obj):
        n = obj.competences.count()
        if n == 0:
            return format_html('<span style="color:#9ca3af;">—</span>')
        return format_html(
            '<span style="background:#eef2ff;color:#3b5bdb;padding:2px 8px;'
            'border-radius:12px;font-size:11px;font-weight:600;">{}</span>',
            n,
        )

    @admin.display(description="Importé", ordering="date_importation")
    def depuis(self, obj):
        delta = timesince(obj.date_importation, timezone.now())
        return format_html(
            '<span style="font-size:11px;color:#8a94a6;">il y a {}</span>',
            delta.split(",")[0],
        )

    # ── Champs readonly detail ────────────────────────────────

    @admin.display(description="Aperçu du CV")
    def cv_preview_large(self, obj):
        if obj.preview_image:
            return format_html(
                '<div style="margin-bottom:8px;">'
                '<img src="{}" style="max-width:320px;max-height:420px;'
                'object-fit:contain;border-radius:10px;'
                'border:1.5px solid #e4e9f2;'
                'box-shadow:0 4px 16px rgba(0,0,0,.1);" />'
                '</div>'
                '<div style="margin-top:6px;">'
                '<a href="{}" target="_blank" '
                'style="font-size:12px;color:#3b5bdb;font-weight:600;">'
                "Ouvrir l'image en plein écran</a>"
                '</div>',
                obj.preview_image.url,
                obj.preview_image.url,
            )
        return format_html(
            '<div style="width:200px;height:260px;border-radius:10px;'
            'background:#f4f6fb;border:2px dashed #d0d8ea;'
            'display:flex;align-items:center;justify-content:center;'
            "color:#a0aab8;font-size:13px;\">Pas d'aperçu</div>"
        )

    @admin.display(description="Compétences associées")
    def competences_liste(self, obj):
        comps = obj.competences.all()[:30]
        if not comps:
            return format_html('<span style="color:#9ca3af;">Aucune compétence associée</span>')
        tags = "".join(
            f'<span style="display:inline-block;margin:2px;padding:3px 10px;'
            f'background:#eef2ff;color:#3b5bdb;border:1px solid #c5d0f7;'
            f'border-radius:20px;font-size:11px;font-weight:600;">{c.nom}</span>'
            for c in comps
        )
        return format_html('<div style="line-height:2;">{}</div>', tags)

    # ── Actions ───────────────────────────────────────────────

    @admin.action(description="✅ Marquer comme Terminé")
    def marquer_termine(self, request, queryset):
        updated = queryset.update(etat_analyse="termine", etat_analyse_termine=True)
        self.message_user(request, f"{updated} candidat(s) marqué(s) comme terminé.")

    @admin.action(description="⏳ Remettre En attente")
    def marquer_en_attente(self, request, queryset):
        updated = queryset.update(etat_analyse="en_attente", etat_analyse_termine=False)
        self.message_user(request, f"{updated} candidat(s) remis en attente.")

    @admin.action(description="✔ Activer les candidats")
    def activer(self, request, queryset):
        updated = queryset.update(actif=True)
        self.message_user(request, f"{updated} candidat(s) activé(s).")

    @admin.action(description="✖ Désactiver les candidats")
    def desactiver(self, request, queryset):
        updated = queryset.update(actif=False)
        self.message_user(request, f"{updated} candidat(s) désactivé(s).")

    # ── Queryset optimisé ─────────────────────────────────────

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .prefetch_related("competences", "agent_analyse_cv")
        )
# ─────────────────────────────────────────────────────────────
# QueryMatching
# ─────────────────────────────────────────────────────────────

@admin.register(QueryMatching)
class QueryMatchingAdmin(admin.ModelAdmin):
    list_display  = ("apercu_query", "agent_deliore", "depuis_query")
    list_filter   = ("agent_deliore",)
    search_fields = ("content", "agent_deliore__email", "agent_deliore__first_name")
    readonly_fields = ("query_id", "date_importation", "agent_deliore")
    ordering      = ("-date_importation",)
    list_per_page = 30
    date_hierarchy = "date_importation"

    def has_add_permission(self, request):
        return False

    @admin.display(description="Requête")
    def apercu_query(self, obj):
        preview = obj.content[:120] + ("…" if len(obj.content) > 120 else "")
        return format_html(
            '<span style="font-size:12px;color:#374151;">{}</span>',
            preview,
        )

    @admin.display(description="Date", ordering="date_importation")
    def depuis_query(self, obj):
        delta = timesince(obj.date_importation, timezone.now())
        return format_html(
            '<span style="font-size:11px;color:#8a94a6;">il y a {}</span>',
            delta.split(",")[0],
        )


# ─────────────────────────────────────────────────────────────
# RapportCv
# ─────────────────────────────────────────────────────────────

@admin.register(RapportCv)
class RapportCvAdmin(admin.ModelAdmin):
    list_display   = ("candidat_nom", "actif", "depuis_rapport", "date_modification")
    list_filter    = ("actif",)
    search_fields  = ("candidatcv__nom_complet", "candidatcv__email")
    readonly_fields = ("rapport_id", "date_creation", "date_modification", "candidatcv")
    ordering       = ("-date_creation",)
    list_per_page  = 25
    date_hierarchy = "date_creation"

    fieldsets = (
        ("Rapport", {
            "fields": ("rapport_id", "candidatcv", "actif"),
        }),
        ("Contenu JSON", {
            "fields": ("contenu",),
            "classes": ("collapse",),
        }),
        ("Dates", {
            "fields": ("date_creation", "date_modification"),
            "classes": ("collapse",),
        }),
    )

    @admin.display(description="Candidat", ordering="candidatcv__nom_complet")
    def candidat_nom(self, obj):
        return format_html(
            '<span style="font-weight:600;color:#0d1117;">{}</span>',
            obj.candidatcv.nom_complet or "—",
        )

    @admin.display(description="Créé", ordering="date_creation")
    def depuis_rapport(self, obj):
        delta = timesince(obj.date_creation, timezone.now())
        return format_html(
            '<span style="font-size:11px;color:#8a94a6;">il y a {}</span>',
            delta.split(",")[0],
        )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("candidatcv")