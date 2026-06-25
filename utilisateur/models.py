from django.db import models
from uuid import uuid4
from django.contrib.auth.models import AbstractUser
from PIL import Image
from io import BytesIO
from django.core.files.base import ContentFile
from datetime import timedelta
from django.utils import timezone

class Users(AbstractUser):
    identifiant = models.UUIDField(primary_key=True, editable=False, default=uuid4)
    post = models.CharField(max_length=100)
    image = models.ImageField(upload_to="agentDeliore/", blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    notification = models.ManyToManyField("Notification", related_name="users_notif", blank=True)

    @property
    def is_online(self):
        if not self.last_seen:
            return False
        return self.last_seen > timezone.now() - timedelta(seconds=10)

    def save(self, *args, **kwargs):
        if self.pk:
            old = Users.objects.filter(pk=self.pk).first()
            if old and old.image == self.image:
                super().save(*args, **kwargs)
                return

        if self.image:
            img = Image.open(self.image)
            img = img.convert("RGB")
            img_io = BytesIO()
            img.save(img_io, format="JPEG", quality=20)
            self.image = ContentFile(img_io.getvalue(), name=self.image.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.last_name} - {self.post} - {self.email}"
    
    class Meta:
        verbose_name = "User Delior"
        ordering = ["-last_login", "-is_active", "-date_joined"]
        indexes = [
            models.Index(fields=["last_name"]),
            models.Index(fields=["first_name"]),
            models.Index(fields=["email"]),
            models.Index(fields=["post"]),
        ]

class Notification(models.Model):
    identifiant = models.UUIDField(primary_key=True, editable=False, default=uuid4)
    content = models.CharField(max_length=100)
    tag = models.CharField(
        choices=[
        ('Infos','Infos'),
        ('Succes','Succes'),
        ('Error','Error'),
        ('Warning','Warning')                         
        ],max_length=8)
    lue = models.BooleanField(default=False)
    date_creation = models.DateTimeField(auto_now_add=True)
    class Meta:
        verbose_name = "Message Agent"
        ordering = ["-date_creation","-tag"]
        indexes = [
            models.Index(fields=["date_creation"]),
            models.Index(fields=["lue"]),
            models.Index(fields=["tag"]),
        ]