# core/admin.py
from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.contrib import messages

# Importez vos modèles, y compris ClientOdooStatus
from .models import UserProfile, ConfigurationCabinet, ClientsOdoo, IndicateursHistoriques, ClientOdooStatus
# Importez les fonctions utilitaires
from .utils import encrypt_value, get_odoo_cabinet_collaborators

# --- Gestion User et UserProfile ---
class UserProfileForm(forms.ModelForm):
    odoo_collaborator_id = forms.ChoiceField(
        label="Collaborateur Odoo Cabinet (Partenaire)",
        required=False,
        help_text="Sélectionnez le partenaire correspondant dans l'Odoo du cabinet."
    )
    class Meta:
        model = UserProfile
        fields = ('role', 'odoo_collaborator_id')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        collaborator_choices_from_odoo = get_odoo_cabinet_collaborators()
        choices = [('', '---------')] + collaborator_choices_from_odoo
        if self.instance and self.instance.pk and self.instance.odoo_collaborator_id:
            current_id_value = self.instance.odoo_collaborator_id
            if not any(choice[0] == current_id_value for choice in collaborator_choices_from_odoo):
                choices.append((current_id_value, f"ID Actuel: {current_id_value} (vérifier)"))
        self.fields['odoo_collaborator_id'].choices = choices

class UserProfileInline(admin.StackedInline):
    model = UserProfile
    form = UserProfileForm
    can_delete = False
    verbose_name_plural = 'Profil (Liaison Collaborateur Odoo)'
    fk_name = 'user'

class CustomUserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)
    def get_role(self, instance):
        profile = getattr(instance, 'profile', None)
        if profile: return profile.get_role_display()
        return 'N/A'
    get_role.short_description = 'Rôle OdooDash'
    list_display = BaseUserAdmin.list_display + ('get_role',)
    list_select_related = ('profile',)

admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)


# --- Configuration Cabinet ---
class ConfigurationCabinetForm(forms.ModelForm):
    plain_api_key = forms.CharField(label="Clé API Odoo Cabinet (en clair)", required=False, widget=forms.PasswordInput(render_value=False), help_text="Laissez vide si vous ne souhaitez pas modifier la clé existante.")
    class Meta: model = ConfigurationCabinet; fields = '__all__'
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'firm_odoo_encrypted_api_key' in self.fields:
             self.fields['firm_odoo_encrypted_api_key'].disabled = True; self.fields['firm_odoo_encrypted_api_key'].required = False; self.fields['firm_odoo_encrypted_api_key'].help_text = "Ce champ est géré automatiquement via la saisie en clair."

@admin.register(ConfigurationCabinet)
class ConfigurationCabinetAdmin(admin.ModelAdmin):
    form = ConfigurationCabinetForm; list_display = ('firm_odoo_url', 'firm_odoo_db', 'firm_odoo_api_user', 'display_api_key_status'); fieldsets = ((None, {'fields': ('firm_odoo_url', 'firm_odoo_db', 'firm_odoo_api_user', 'plain_api_key', 'firm_odoo_encrypted_api_key')}),); readonly_fields = ('firm_odoo_encrypted_api_key',)
    def display_api_key_status(self, obj): return "Définie" if obj.firm_odoo_encrypted_api_key else "Non définie"; display_api_key_status.short_description = "Statut Clé API"
    def save_model(self, request, obj, form, change):
        plain_key = form.cleaned_data.get('plain_api_key');
        if plain_key:
            try: obj.firm_odoo_encrypted_api_key = encrypt_value(plain_key); self.message_user(request, "La clé API a été chiffrée et sauvegardée.", messages.SUCCESS)
            except ValueError as e: self.message_user(request, f"Erreur lors du chiffrement : {e}", messages.ERROR); return
        elif not obj.pk and not obj.firm_odoo_encrypted_api_key: self.message_user(request, "Attention: Aucune clé API n'a été fournie ou sauvegardée.", messages.WARNING)
        super().save_model(request, obj, form, change)
    def has_add_permission(self, request): return not ConfigurationCabinet.objects.exists()

# --- Clients Odoo ---
class ClientsOdooForm(forms.ModelForm):
    plain_api_key = forms.CharField(label="Clé API Odoo Client (en clair)", required=False, widget=forms.PasswordInput(render_value=False), help_text="Laissez vide si vous ne souhaitez pas modifier la clé existante.")
    class Meta: model = ClientsOdoo; fields = '__all__'
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'client_odoo_encrypted_api_key' in self.fields:
             self.fields['client_odoo_encrypted_api_key'].disabled = True; self.fields['client_odoo_encrypted_api_key'].required = False; self.fields['client_odoo_encrypted_api_key'].help_text = "Ce champ est géré automatiquement via la saisie en clair."

@admin.register(ClientsOdoo)
class ClientsOdooAdmin(admin.ModelAdmin):
    form = ClientsOdooForm; list_display = ('client_name', 'client_odoo_url', 'client_odoo_db', 'client_odoo_api_user', 'display_api_key_status'); search_fields = ('client_name', 'client_odoo_url', 'client_odoo_db'); list_per_page = 20; fieldsets = ((None, {'fields': ('client_name', 'client_odoo_url', 'client_odoo_db', 'client_odoo_api_user', 'plain_api_key', 'client_odoo_encrypted_api_key')}),); readonly_fields = ('client_odoo_encrypted_api_key',)
    def display_api_key_status(self, obj): return "Définie" if obj.client_odoo_encrypted_api_key else "Non définie"; display_api_key_status.short_description = "Statut Clé API"
    def save_model(self, request, obj, form, change):
        plain_key = form.cleaned_data.get('plain_api_key');
        if plain_key:
            try: obj.client_odoo_encrypted_api_key = encrypt_value(plain_key); self.message_user(request, "La clé API a été chiffrée et sauvegardée.", messages.SUCCESS)
            except ValueError as e: self.message_user(request, f"Erreur lors du chiffrement : {e}", messages.ERROR); return
        super().save_model(request, obj, form, change)


# --- Indicateurs Historiques ---
@admin.register(IndicateursHistoriques)
class IndicateursHistoriquesAdmin(admin.ModelAdmin):
    list_display = ('client', 'indicator_name', 'indicator_value', 'extraction_timestamp', 'assigned_odoo_collaborator_id', 'assigned_collaborator_name')
    list_filter = ('client__client_name', 'indicator_name', 'indicator_value', 'extraction_timestamp', 'assigned_collaborator_name')
    search_fields = ('indicator_name', 'indicator_value', 'client__client_name', 'assigned_collaborator_name')
    readonly_fields = ('id', 'client', 'indicator_name', 'indicator_value', 'extraction_timestamp', 'assigned_odoo_collaborator_id', 'assigned_collaborator_name')
    list_per_page = 50
    date_hierarchy = 'extraction_timestamp'
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return True
    def has_delete_permission(self, request, obj=None): return True


# --- CLASSE ADMIN POUR ClientOdooStatus ---
@admin.register(ClientOdooStatus)
class ClientOdooStatusAdmin(admin.ModelAdmin):
    list_display = ('get_client_name', 'connection_successful', 'last_connection_attempt', 'last_error_message_summary')
    list_filter = ('connection_successful', 'last_connection_attempt', 'client__client_name')
    search_fields = ('client__client_name', 'last_error_message')
    readonly_fields = ('client', 'connection_successful', 'last_connection_attempt', 'last_error_message')
    list_per_page = 25

    @admin.display(description='Client Odoo', ordering='client__client_name')
    def get_client_name(self, obj):
        return obj.client.client_name

    def last_error_message_summary(self, obj):
        if obj.last_error_message:
            return (obj.last_error_message[:75] + '...') if len(obj.last_error_message) > 75 else obj.last_error_message
        return "-"
    last_error_message_summary.short_description = "Résumé Erreur"

    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return True # Permet de voir le détail
    def has_delete_permission(self, request, obj=None):
        return True # Ou False pour empêcher la suppression
# --- FIN CLASSE ADMIN ---
