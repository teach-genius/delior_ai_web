from django.contrib import admin
from django.db.models import Count

from .models import Configuration, Competence, CandidatCV, QueryMatching, RapportCv


@admin.register(Configuration)
class ConfigurationAdmin(admin.ModelAdmin):
    list_display = ('email_deliore', 'email_loader_status', 'email_processing_active')

    def has_add_permission(self, request):
        return not Configuration.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Competence)
class CompetenceAdmin(admin.ModelAdmin):
    list_display  = ("nom",)
    search_fields = ("nom",)
    ordering      = ("nom",)


@admin.register(CandidatCV)
class CandidatCVAdmin(admin.ModelAdmin):
    list_display = (
        "nom_complet",
        "titre_professionnel",
        "email",
        "telephone",
        "ville",
        "pays",
        "domaine",
        "secteur",
        "niveau",
        "contrat_souhaite",
        "etat_analyse",
        "etat_analyse_termine",
        "actif",
        "date_importation",
    )
    list_filter = (
        "etat_analyse",
        "etat_analyse_termine",
        "actif",
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
    )
    ordering      = ("-date_importation",)
    list_per_page = 25
    date_hierarchy = "date_importation"

    actions = ["marquer_termine", "marquer_en_attente", "activer", "desactiver"]

    filter_horizontal = ("agent_analyse_cv", "competences")

    readonly_fields = (
        "candidat_id",
        "date_importation",
    )

    @admin.action(description="Marquer comme Terminé")
    def marquer_termine(self, request, queryset):
        updated = queryset.update(etat_analyse="termine", etat_analyse_termine=True)
        self.message_user(request, f"{updated} candidat(s) marqué(s) comme terminé.")

    @admin.action(description="Remettre En attente")
    def marquer_en_attente(self, request, queryset):
        updated = queryset.update(etat_analyse="en_attente", etat_analyse_termine=False)
        self.message_user(request, f"{updated} candidat(s) remis en attente.")

    @admin.action(description="Activer les candidats")
    def activer(self, request, queryset):
        updated = queryset.update(actif=True)
        self.message_user(request, f"{updated} candidat(s) activé(s).")

    @admin.action(description="Désactiver les candidats")
    def desactiver(self, request, queryset):
        updated = queryset.update(actif=False)
        self.message_user(request, f"{updated} candidat(s) désactivé(s).")


@admin.register(QueryMatching)
class QueryMatchingAdmin(admin.ModelAdmin):
    list_display  = ("content", "agent_deliore", "date_importation")
    list_filter   = ("agent_deliore",)
    search_fields = ("content",)
    ordering      = ("-date_importation",)
    list_per_page = 30
    date_hierarchy = "date_importation"
    readonly_fields = ("query_id", "date_importation", "agent_deliore")

    def has_add_permission(self, request):
        return False

@admin.register(RapportCv)
class RapportCvAdmin(admin.ModelAdmin):
    list_display  = ("candidatcv", "actif", "date_creation", "date_modification")
    list_filter   = ("actif",)
    search_fields = ("candidatcv__nom_complet", "candidatcv__email")
    ordering      = ("-date_creation",)
    list_per_page = 25
    date_hierarchy = "date_creation"
    readonly_fields = ("rapport_id", "date_creation", "date_modification", "candidatcv")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("candidatcv")