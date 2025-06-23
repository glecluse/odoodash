# core/models.py
import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone # Importer timezone

# Modèle pour étendre le User de Django avec nos champs spécifiques
class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
        verbose_name="Utilisateur Django lié"
    )
    ROLE_CHOICES = [
        ('admin', 'Administrateur'),
        ('collaborateur', 'Collaborateur'),
    ]
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        null=False,
        blank=False,
        verbose_name="Rôle dans OdooDash"
    )
    odoo_collaborator_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        verbose_name="ID Partenaire Odoo du Collaborateur"
    )

    def __str__(self):
        return f"Profil de {self.user.username} ({self.get_role_display()})"

    class Meta:
        verbose_name = "Profil Utilisateur OdooDash"
        verbose_name_plural = "Profils Utilisateurs OdooDash"


# Modèle pour la configuration de l'Odoo du cabinet
class ConfigurationCabinet(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    firm_odoo_url = models.TextField(null=False, blank=False, verbose_name="URL Odoo Cabinet")
    firm_odoo_db = models.CharField(max_length=255, null=False, blank=False, verbose_name="Nom BDD Odoo Cabinet")
    firm_odoo_api_user = models.CharField(max_length=255, null=False, blank=False, verbose_name="Utilisateur API Odoo Cabinet")
    firm_odoo_encrypted_api_key = models.TextField(null=False, blank=False, verbose_name="Clé API Odoo Cabinet (Chiffrée)")

    def __str__(self):
        return f"Configuration Odoo Cabinet ({self.firm_odoo_db})"

    class Meta:
        verbose_name = "Configuration Odoo Cabinet"
        verbose_name_plural = "Configuration Odoo Cabinet"


# Modèle pour les informations des clients Odoo
class ClientsOdoo(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client_name = models.CharField(max_length=255, unique=True, null=False, blank=False, verbose_name="Nom du Client")
    client_odoo_url = models.TextField(null=False, blank=False, verbose_name="URL Odoo Client (depuis Cabinet)")
    client_odoo_db = models.CharField(max_length=255, null=False, blank=False, verbose_name="Nom BDD Odoo Client")
    client_odoo_api_user = models.CharField(max_length=255, null=False, blank=False, verbose_name="Utilisateur API Odoo Client")
    client_odoo_encrypted_api_key = models.TextField(null=False, blank=False, verbose_name="Clé API Odoo Client (Chiffrée)")

    def __str__(self):
        return self.client_name

    class Meta:
        verbose_name = "Client Odoo"
        verbose_name_plural = "Clients Odoo"
        ordering = ['client_name']


# Modèle pour l'historique des indicateurs
class IndicateursHistoriques(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client = models.ForeignKey(
        ClientsOdoo,
        on_delete=models.CASCADE,
        null=False,
        verbose_name="Client Odoo Concerné"
    )
    indicator_name = models.CharField(max_length=255, null=False, blank=False, verbose_name="Nom Indicateur")
    indicator_value = models.TextField(null=True, blank=True, verbose_name="Valeur Indicateur")
    extraction_timestamp = models.DateTimeField(null=False, blank=False, verbose_name="Date/Heure Extraction")
    assigned_odoo_collaborator_id = models.CharField(max_length=255, null=True, blank=True, verbose_name="ID Partenaire Collaborateur (Odoo Cabinet)")
    assigned_collaborator_name = models.CharField(max_length=255, null=True, blank=True, verbose_name="Nom Collaborateur Assigné (Odoo Cabinet)")

    def __str__(self):
        return f"{self.client.client_name} - {self.indicator_name} ({self.extraction_timestamp.strftime('%Y-%m-%d %H:%M')})"

    class Meta:
        verbose_name = "Indicateur Historique"
        verbose_name_plural = "Indicateurs Historiques"
        indexes = [
            models.Index(fields=['client', '-extraction_timestamp']),
            models.Index(fields=['assigned_odoo_collaborator_id']),
            models.Index(fields=['indicator_name']),
        ]
        ordering = ['-extraction_timestamp', 'client__client_name', 'indicator_name']

# --- NOUVEAU MODÈLE AJOUTÉ ---
class ClientOdooStatus(models.Model):
    client = models.OneToOneField(
        ClientsOdoo,
        on_delete=models.CASCADE,
        primary_key=True, # Le client est la clé primaire, assurant une seule entrée par client
        verbose_name="Client Odoo Associé"
    )
    last_connection_attempt = models.DateTimeField(
        default=timezone.now, # Se met à jour à chaque tentative
        verbose_name="Dernière Tentative de Connexion"
    )
    connection_successful = models.BooleanField(
        default=False,
        verbose_name="Connexion Réussie"
    )
    last_error_message = models.TextField(
        null=True,
        blank=True,
        verbose_name="Dernier Message d'Erreur"
    )
    # On pourrait ajouter un last_success_timestamp si besoin

    def __str__(self):
        status = "Réussie" if self.connection_successful else "Échec"
        return f"Statut Connexion {self.client.client_name}: {status} (le {self.last_connection_attempt.strftime('%d/%m/%Y %H:%M')})"

    class Meta:
        verbose_name = "Statut Connexion Client Odoo"
        verbose_name_plural = "Statuts Connexion Clients Odoo"
        ordering = ['-last_connection_attempt']
# --- FIN NOUVEAU MODÈLE ---
