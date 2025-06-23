# core/views.py
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Max, Q
from .models import IndicateursHistoriques, UserProfile, ClientsOdoo
from collections import defaultdict
import logging

from django.core.management import call_command
from django.contrib import messages
from django.urls import reverse

logger = logging.getLogger(__name__)

# --- DÉFINITION DES CATÉGORIES D'INDICATEURS ---
# Ne contient que les VRAIS noms d'indicateurs stockés en base de données
INDICATOR_CATEGORIES = {
    'Contrôle des clôtures': [
        'date cloture annuelle',
        'derniere cloture fiscale',
        'periodicite tva',
        # 'mois_saisie_derniere_tva', # Si vous le réactivez
    ],
    'Données techniques': [
        'version odoo',
        'nb modeles personnalises (indicatif)',
        'nb actions automatisées',
        'nb utilisateurs actifs',
        'nb utilisateurs lpde',
        'nb modules actifs',
        'date activation base',
    ],
    'Production comptable': [
        'operations à qualifier',
        'achats à traiter',
        'paiements orphelins',
        'virements internes non soldés',
        'solde virements internes',
        'pivot encaissement',
        # 'nb_lignes_ecritures_annee_courante',
        # 'factures_fourn_attente_validation',
    ],
    'Santé financière': [
        'marge brute 30j',
    ],
    # La catégorie "Divers" est spéciale et gérée différemment pour l'affichage des colonnes.
    # Elle peut être vide ici si elle ne contient aucun indicateur stocké en BDD.
    'Divers': []
}


# --- FIN DÉFINITION CATÉGORIES ---


@login_required
def dashboard_view(request):
    user = request.user
    profile = None
    user_role = None
    user_collab_id_str = None
    try:
        profile = user.profile
        user_role = profile.role
        user_collab_id_str = str(profile.odoo_collaborator_id) if profile.odoo_collaborator_id else None
    except UserProfile.DoesNotExist:
        logger.warning(f"Profil utilisateur non trouvé pour l'utilisateur {user.username}")

    selected_closing_date = request.GET.get('closing_date_filter', '')
    selected_collaborator_name = request.GET.get('collaborator_filter', '')
    selected_category = request.GET.get('category_filter', '')

    base_qs_for_filter_options = IndicateursHistoriques.objects.all()
    if user_role == 'collaborateur' and user_collab_id_str:
        base_qs_for_filter_options = base_qs_for_filter_options.filter(assigned_odoo_collaborator_id=user_collab_id_str)
    elif user_role != 'admin':
        base_qs_for_filter_options = IndicateursHistoriques.objects.none()

    closing_date_choices = base_qs_for_filter_options.filter(indicator_name='date cloture annuelle') \
        .values_list('indicator_value', flat=True) \
        .distinct().order_by('indicator_value')
    collaborator_choices = base_qs_for_filter_options.exclude(assigned_collaborator_name__isnull=True) \
        .exclude(assigned_collaborator_name='N/A') \
        .exclude(assigned_collaborator_name='') \
        .values_list('assigned_collaborator_name', flat=True) \
        .distinct().order_by('assigned_collaborator_name')

    # Les choix de catégorie pour le dropdown incluent "Divers"
    category_choices_display = list(INDICATOR_CATEGORIES.keys())  # "Divers" est déjà une clé

    latest_indicators_qs = IndicateursHistoriques.objects.none()
    latest_run_timestamp = None
    all_indicator_names_for_columns = []  # Noms des indicateurs "données" à afficher
    clients_list = []
    client_indicators_dict = {}

    # Déterminer la visibilité des colonnes "Collaborateur" et "Date Extraction"
    show_collaborator_column = False
    show_extraction_date_column = False

    current_data_qs = base_qs_for_filter_options
    if current_data_qs.exists():
        latest_run_agg = current_data_qs.aggregate(latest_run=Max('extraction_timestamp'))
        latest_run_timestamp = latest_run_agg.get('latest_run')

    if latest_run_timestamp:
        logger.info(
            f"Dernier timestamp trouvé pour l'utilisateur {user.username} ({user_role}): {latest_run_timestamp}")
        latest_indicators_qs = current_data_qs.filter(
            extraction_timestamp=latest_run_timestamp
        )

        if selected_collaborator_name:
            latest_indicators_qs = latest_indicators_qs.filter(
                assigned_collaborator_name=selected_collaborator_name
            )
        if selected_closing_date:
            # Assurez-vous que le nom de l'indicateur ici est normalisé si besoin
            clients_with_closing_date = latest_indicators_qs.filter(
                indicator_name='date cloture annuelle',
                indicator_value=selected_closing_date
            ).values_list('client_id', flat=True).distinct()
            latest_indicators_qs = latest_indicators_qs.filter(client_id__in=clients_with_closing_date)

        # Noms d'indicateurs potentiels après filtres client/date/collaborateur
        potential_indicator_names = sorted(list(set(
            name.strip().lower() for name in latest_indicators_qs.values_list('indicator_name', flat=True) if
            name and name.strip()
        )))

        if selected_category == "Divers":
            all_indicator_names_for_columns = []  # Pas de colonnes d'indicateurs de données
            show_collaborator_column = True
            show_extraction_date_column = True
        elif selected_category and selected_category in INDICATOR_CATEGORIES:
            # Noms d'indicateurs pour la catégorie sélectionnée (normalisés)
            category_indicator_names = [name.strip().lower() for name in INDICATOR_CATEGORIES[selected_category]]
            all_indicator_names_for_columns = [
                name for name in potential_indicator_names if name in category_indicator_names
            ]
            # Pour les catégories spécifiques autres que "Divers", on n'affiche pas les colonnes méta
            show_collaborator_column = False
            show_extraction_date_column = False
        else:  # "Toutes les catégories" (selected_category est vide)
            all_indicator_names_for_columns = potential_indicator_names
            show_collaborator_column = True
            show_extraction_date_column = True

        if all_indicator_names_for_columns or selected_category == "Divers":
            # Si on affiche des colonnes d'indicateurs OU si on est dans la catégorie "Divers" (qui a ses propres colonnes fixes)
            # On ne filtre plus latest_indicators_qs par indicator_name ici,
            # car le template s'en chargera pour les colonnes dynamiques.
            # latest_indicators_qs = latest_indicators_qs.filter(indicator_name__in=all_indicator_names_for_columns) # Supprimé

            latest_indicators_qs = latest_indicators_qs.select_related('client').order_by('client__client_name',
                                                                                          'indicator_name')

            client_indicators_temp = defaultdict(list)
            clients_processed_temp = set()

            for indicator in latest_indicators_qs:
                # On stocke tous les indicateurs du client pour la dernière extraction,
                # le template choisira lesquels afficher dans les colonnes dynamiques.
                client_indicators_temp[indicator.client].append(indicator)
                clients_processed_temp.add(indicator.client)

            client_indicators_dict = dict(client_indicators_temp)
            clients_list = sorted(list(clients_processed_temp), key=lambda c: c.client_name)
        else:
            clients_list = []
            client_indicators_dict = {}
    else:
        logger.info(
            f"Aucun timestamp trouvé pour l'utilisateur {user.username} ({user_role}) ou aucun indicateur après filtrage initial.")

    context = {
        'user_profile': profile,
        'user_role': user_role,
        'client_indicators': client_indicators_dict,
        'clients_list': clients_list,
        'all_indicator_names': all_indicator_names_for_columns,  # Noms pour les colonnes dynamiques
        'latest_run_timestamp': latest_run_timestamp,
        'page_title': 'Tableau de Bord OdooDash',
        'closing_date_choices': closing_date_choices,
        'collaborator_choices': collaborator_choices,
        'category_choices': category_choices_display,  # Utilise les clés du dict + "Divers"
        'selected_closing_date': selected_closing_date,
        'selected_collaborator_name': selected_collaborator_name,
        'selected_category': selected_category,
        'show_collaborator_column': show_collaborator_column,  # Nouvelle variable de contexte
        'show_extraction_date_column': show_extraction_date_column,  # Nouvelle variable de contexte
    }
    return render(request, 'core/dashboard.html', context)


# --- VUE POUR EXÉCUTER LA COMMANDE D'EXTRACTION ---
@user_passes_test(lambda u: u.is_staff and u.is_superuser)
def trigger_fetch_indicators_view(request):
    # ... (code inchangé)
    if request.method == 'POST':
        try:
            logger.info(f"Lancement de fetch_indicators par l'utilisateur: {request.user.username}")
            call_command('fetch_indicators')
            messages.success(request,
                             "L'extraction des indicateurs a été lancée avec succès. Les données seront mises à jour sous peu.")
        except Exception as e:
            logger.error(f"Erreur lors du lancement de fetch_indicators via l'admin par {request.user.username}: {e}",
                         exc_info=True)
            messages.error(request, f"Une erreur est survenue lors du lancement de l'extraction : {e}")
        return redirect(reverse('admin:index'))
    else:
        messages.warning(request, "Cette action doit être déclenchée via un formulaire POST.")
        return redirect(reverse('admin:index'))
