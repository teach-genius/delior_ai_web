from django import forms
from .models import Configuration

class ConfigurationForm(forms.ModelForm):

    password_email_deliore = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        required=False,
        label="Mot de passe email"
    )

    class Meta:
        model  = Configuration
        fields = [
            'email_deliore',
            'password_email_deliore',
            'email_loader_status',
            'email_processing_active',
        ]
        labels = {
            'email_deliore'          : 'Adresse email',
            'email_loader_status'    : 'Activer le chargement des emails',
            'email_processing_active': 'Activer le traitement automatique des CVs',
        }
        widgets = {
            'email_deliore': forms.EmailInput(attrs={
                'placeholder': 'recrutement@monentreprise.com'
            }),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not self.cleaned_data.get('password_email_deliore'):
            existing = Configuration.objects.filter(pk=1).first()
            if existing:
                instance.password_email_deliore = existing.password_email_deliore
        if commit:
            instance.save()
        return instance