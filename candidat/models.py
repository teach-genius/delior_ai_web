from django.db import models
from uuid import uuid4
from django.contrib.postgres.indexes import GinIndex
from utilisateur.models import Users

class Configuration(models.Model):
    email_deliore           = models.EmailField(blank=True)
    password_email_deliore  = models.CharField(max_length=255, blank=True)
    email_loader_status     = models.BooleanField(default=False)
    email_processing_active = models.BooleanField(default=False)

    class Meta:
        verbose_name        = "Configuration"
        verbose_name_plural = "Configuration"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return f"Configuration — email: {self.email_deliore or '—'} | loader: {self.email_loader_status} | processing: {self.email_processing_active}"
    
class Competence(models.Model):
    nom = models.CharField(max_length=500, unique=True)
    def save(self, *args, **kwargs):
        self.nom = self.nom.lower().strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nom
    
    class Meta:
        verbose_name = "Competence"
        ordering = ["-nom"]
        indexes = [
            models.Index(fields=["nom"]),
        ]


class CandidatCV(models.Model):
    candidat_id = models.UUIDField(primary_key=True, editable=False, default=uuid4)
    nom_complet = models.CharField(max_length=255,null=True,blank=True)
    titre_professionnel = models.CharField(max_length=255, blank=True,null=True)
    ville = models.CharField(max_length=150, blank=True,null=True)
    pays = models.CharField(max_length=100, blank=True,null=True)
    telephone = models.CharField(max_length=50, blank=True,null=True)
    email = models.EmailField(unique=True,blank=True,null=True)
    domaine = models.CharField(max_length=255, blank=True,null=True)
    secteur = models.CharField(max_length=255, blank=True,null=True)
    niveau = models.CharField(max_length=255, blank=True,null=True)
    contrat_souhaite = models.CharField(max_length=255, blank=True,null=True)
    resume_profil = models.TextField(blank=True,null=True)
    competences = models.ManyToManyField(Competence, related_name="candidats", blank=True)
    donnees_structurees = models.JSONField(default=dict)

    etat_analyse = models.CharField(max_length=50, default="en_attente", choices=[
    ("en_attente", "En attente"),
    ("en_cours", "En cours"),
    ("termine", "Terminé"),])
    agent_analyse_cv = models.ManyToManyField(Users, related_name="candidats_analyse_cv", blank=True)
    etat_analyse_termine = models.BooleanField(default=False)
    actif = models.BooleanField(default=True)
    fichier_pdf_origine = models.FileField(upload_to="cv_pdfs/", null=True, blank=True,max_length=500)
    preview_image = models.ImageField(upload_to="cv_previews/", null=True, blank=True,max_length=500)
    date_importation = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.nom_complet} - {self.email} - {self.telephone}"

    class Meta:
        verbose_name = "CV Candidat"
        ordering = ["-date_importation","-etat_analyse_termine","-etat_analyse","-domaine","-secteur","-niveau"]
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["titre_professionnel"]),
            models.Index(fields=["ville"]),
            models.Index(fields=["pays"]),
            models.Index(fields=["etat_analyse_termine"]),
            models.Index(fields=["etat_analyse"]),
            models.Index(fields=["domaine"]),
            models.Index(fields=["secteur"]),
            models.Index(fields=["niveau"]),
            GinIndex(fields=["donnees_structurees"]),
        ]

class QueryMatching(models.Model):
    query_id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    content = models.JSONField(default=dict)
    date_importation = models.DateTimeField(auto_now_add=True)

    agent_deliore = models.ForeignKey(
        Users,
        on_delete=models.CASCADE,
        related_name="queries"
    )

    def __str__(self):
        return str(self.date_importation)

    class Meta:
        verbose_name = "Query Agent"
        ordering = ["-date_importation"]
        indexes = [
            models.Index(fields=["date_importation"]),
            models.Index(fields=["agent_deliore"]),
        ]

class RapportCv(models.Model):
    rapport_id = models.UUIDField(primary_key=True, editable=False, default=uuid4)
    contenu = models.JSONField()
    candidatcv = models.ForeignKey(CandidatCV, on_delete=models.CASCADE, related_name="candidatcvs")
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Rapport Cv Candidat"
        ordering = ["-date_creation"]
        indexes = [
            models.Index(fields=["actif"])
        ]

