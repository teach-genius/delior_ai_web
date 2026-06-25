from django.core.management.base import BaseCommand
from candidat.models import CandidatCV
from aiagent.recommender.recommender_sys import get_recommender


class Command(BaseCommand):
    help = "Réindexe tous les CVs dans Qdrant"

    def add_arguments(self, parser):
        parser.add_argument(
            "--recreate",
            action="store_true",
            help="Supprime et recrée la collection Qdrant avant l'indexation (obligatoire après changement de schéma)",
        )

    def handle(self, *args, **kwargs):
        rsys = get_recommender()

        if kwargs["recreate"]:
            self.stdout.write("Suppression de la collection existante...")
            if rsys.client_qdrant.collection_exists(rsys.collection_name):
                rsys.client_qdrant.delete_collection(rsys.collection_name)
                self.stdout.write(self.style.WARNING(f"Collection '{rsys.collection_name}' supprimée."))
            rsys._ensure_collection()
            self.stdout.write(self.style.SUCCESS(f"Collection '{rsys.collection_name}' recrée avec le bon schéma BM25."))

        total = CandidatCV.objects.count()
        self.stdout.write(f"Indexation de {total} CVs...")
        rsys.full_reindex(CandidatCV.objects)
        self.stdout.write(self.style.SUCCESS(f"✓ {total} CVs indexés avec succès"))