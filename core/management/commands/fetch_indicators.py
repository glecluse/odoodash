# core/management/commands/fetch_indicators.py

import xmlrpc.client
import logging
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.conf import settings  # Pour accéder à FERNET_KEY
from datetime import datetime

# Importer les modèles Django
from core.models import ConfigurationCabinet, ClientsOdoo, IndicateursHistoriques, ClientOdooStatus
# Importer les fonctions de chiffrement/déchiffrement et connect_odoo
from core.utils import decrypt_value, connect_odoo  # connect_odoo est dans utils

# Configuration du logging (optionnel mais recommandé)
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Extrait les indicateurs depuis les instances Odoo configurées et les sauvegarde en base de données.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("--- Début de l'extraction des indicateurs ---"))

        # 1. Récupérer la configuration de l'Odoo Cabinet
        try:
            config_cabinet = ConfigurationCabinet.objects.first()
            if not config_cabinet:
                raise CommandError("Configuration du cabinet non trouvée. Veuillez la configurer via l'admin.")
            self.stdout.write(
                f"Configuration cabinet trouvée: URL={config_cabinet.firm_odoo_url}, DB={config_cabinet.firm_odoo_db}")
        except Exception as e:
            raise CommandError(f"Erreur lors de la lecture de la configuration cabinet: {e}")

        # 2. Déchiffrer la clé API du cabinet
        firm_api_key = decrypt_value(config_cabinet.firm_odoo_encrypted_api_key)
        firm_uid = None
        firm_object_proxy = None
        if not firm_api_key:
            self.stderr.write(self.style.WARNING(
                f"Impossible de déchiffrer la clé API pour la config cabinet. La récupération des collaborateurs échouera."))
        else:
            self.stdout.write(self.style.SUCCESS("Clé API du cabinet déchiffrée avec succès."))
            firm_uid, _, firm_object_proxy, _, firm_conn_error = connect_odoo(
                config_cabinet.firm_odoo_url,
                config_cabinet.firm_odoo_db,
                config_cabinet.firm_odoo_api_user,
                firm_api_key
            )
            if not firm_uid:
                self.stderr.write(self.style.WARNING(
                    f">>> Attention: Échec de la connexion à l'Odoo du cabinet: {firm_conn_error}. La récupération des collaborateurs assignés échouera."))
                firm_object_proxy = None
            else:
                self.stdout.write(self.style.SUCCESS("Connecté avec succès à l'Odoo du cabinet."))

        clients_config = ClientsOdoo.objects.all()
        if not clients_config:
            self.stdout.write(self.style.WARNING("Aucun client Odoo n'est configuré."))
            self.stdout.write(self.style.SUCCESS("--- Fin de l'extraction (aucun client) ---"))
            return

        self.stdout.write(f"Traitement de {clients_config.count()} client(s) Odoo configuré(s)...")
        current_extraction_run_timestamp = timezone.now()
        self.stdout.write(f"Timestamp pour cette exécution : {current_extraction_run_timestamp}")

        for client_conf in clients_config:
            self.stdout.write(self.style.NOTICE(f"\n--- Traitement du client : {client_conf.client_name} ---"))
            client_api_key = decrypt_value(client_conf.client_odoo_encrypted_api_key)
            last_attempt_time = timezone.now()
            client_odoo_version_str = "Inconnue"  # Valeur par défaut

            if not client_api_key:
                error_msg = "Impossible de déchiffrer la clé API."
                self.stderr.write(self.style.ERROR(f"{error_msg} pour {client_conf.client_name}. Skipping..."))
                ClientOdooStatus.objects.update_or_create(
                    client=client_conf,
                    defaults={
                        'last_connection_attempt': last_attempt_time,
                        'connection_successful': False,
                        'last_error_message': error_msg
                    }
                )
                continue
            self.stdout.write(f"Clé API déchiffrée pour {client_conf.client_name}.")

            self.stdout.write(
                f"Tentative de connexion à {client_conf.client_odoo_url} (DB: {client_conf.client_odoo_db})...")

            uid_client, _, object_proxy_client, odoo_server_version_from_util, connection_error_msg = connect_odoo(
                client_conf.client_odoo_url,
                client_conf.client_odoo_db,
                client_conf.client_odoo_api_user,
                client_api_key
            )

            if odoo_server_version_from_util:
                client_odoo_version_str = odoo_server_version_from_util

            status_defaults = {
                'last_connection_attempt': last_attempt_time,
                'connection_successful': bool(uid_client),
                'last_error_message': connection_error_msg if connection_error_msg else None
            }
            status_obj, created = ClientOdooStatus.objects.update_or_create(
                client=client_conf,
                defaults=status_defaults
            )
            if created:
                self.stdout.write(
                    self.style.SUCCESS(f"   - Statut de connexion initialisé pour {client_conf.client_name}."))
            else:
                self.stdout.write(
                    self.style.SUCCESS(f"   - Statut de connexion mis à jour pour {client_conf.client_name}."))

            indicators_data = {}

            # Indicateur : Version Odoo Client
            indicator_name_odoo_version = "version odoo"
            if client_odoo_version_str and client_odoo_version_str != "Inconnue":
                indicators_data[indicator_name_odoo_version] = client_odoo_version_str
                self.stdout.write(
                    self.style.SUCCESS(f"   - {indicator_name_odoo_version}: OK ({client_odoo_version_str})"))
            else:
                self.stdout.write(self.style.WARNING(f"   - {indicator_name_odoo_version}: Version non récupérée."))
                indicators_data[indicator_name_odoo_version] = "Inconnue"

            if not uid_client:  # Si l'authentification a échoué
                self.stderr.write(self.style.ERROR(
                    f">>> Échec authentification Odoo pour {client_conf.client_name}. {connection_error_msg if connection_error_msg else ''} Skipping autres indicateurs..."))
                # Sauvegarder uniquement la version Odoo si elle a été trouvée
                if indicators_data.get(indicator_name_odoo_version) and indicators_data[
                    indicator_name_odoo_version] != "Inconnue":
                    try:
                        IndicateursHistoriques.objects.create(
                            client=client_conf,
                            indicator_name=indicator_name_odoo_version,
                            indicator_value=indicators_data[indicator_name_odoo_version],
                            extraction_timestamp=current_extraction_run_timestamp,
                            assigned_odoo_collaborator_id="0",
                            assigned_collaborator_name="N/A"
                        )
                        self.stdout.write(self.style.SUCCESS(
                            f"   - Indicateur '{indicator_name_odoo_version}' sauvegardé malgré l'échec d'authentification."))
                    except Exception as e_save:
                        self.stderr.write(self.style.ERROR(
                            f"   - Erreur sauvegarde indicateur '{indicator_name_odoo_version}' pour {client_conf.client_name}: {e_save}"))
                continue  # Passe au client suivant

            self.stdout.write(
                self.style.SUCCESS(
                    f"Connecté et authentifié avec succès à Odoo pour {client_conf.client_name} (UID: {uid_client})."))

            assigned_collab_id = None
            collaborator_display_name = "N/A"
            final_assigned_collab_id_str = "0"
            if firm_uid and firm_object_proxy:
                try:
                    self.stdout.write(
                        f"   - Recherche du partenaire client '{client_conf.client_name}' dans l'Odoo cabinet via son URL...")
                    technical_field_name_for_client_url = 'x_odoo_database'
                    partner_domain = [(technical_field_name_for_client_url, '=', client_conf.client_odoo_url)]
                    partner_ids = firm_object_proxy.execute_kw(config_cabinet.firm_odoo_db, firm_uid, firm_api_key,
                                                               'res.partner', 'search', [partner_domain], {'limit': 1})
                    if partner_ids:
                        partner_id = partner_ids[0]
                        self.stdout.write(
                            f"   - Partenaire client trouvé (ID: {partner_id}). Lecture du champ collaborateur...")
                        partner_data = firm_object_proxy.execute_kw(config_cabinet.firm_odoo_db, firm_uid, firm_api_key,
                                                                    'res.partner', 'read', [[partner_id]],
                                                                    {'fields': ['x_collaborateur_1']})
                        collaborator_info = partner_data[0].get('x_collaborateur_1') if partner_data else None
                        if collaborator_info and isinstance(collaborator_info, (list, tuple)) and len(
                                collaborator_info) >= 1:
                            assigned_collab_id = collaborator_info[0];
                            collaborator_display_name = collaborator_info[1]
                            self.stdout.write(self.style.SUCCESS(
                                f"   - Collaborateur (partenaire) lié trouvé (ID: {assigned_collab_id}, Nom: {collaborator_display_name})"))
                        else:
                            self.stdout.write(self.style.WARNING(
                                f"   - Champ collaborateur 'x_collaborateur_1' vide sur la fiche partenaire (ID: {partner_id})."))
                    else:
                        self.stdout.write(self.style.WARNING(
                            f"   - Partenaire client avec URL '{client_conf.client_odoo_url}' (via champ '{technical_field_name_for_client_url}') non trouvé dans l'Odoo cabinet."))
                except Exception as e:
                    self.stderr.write(self.style.ERROR(
                        f"   - Erreur lors de la récupération du collaborateur depuis l'Odoo cabinet: {e}"))
            else:
                self.stdout.write(self.style.WARNING(
                    "   - Connexion à l'Odoo cabinet non disponible, impossible de récupérer le collaborateur assigné."))
            if assigned_collab_id is not None:
                final_assigned_collab_id_str = str(assigned_collab_id)
            else:
                final_assigned_collab_id_str = "0"
            self.stdout.write(
                f"Collaborateur assigné -> ID Partenaire: {final_assigned_collab_id_str}, Nom Affiché: {collaborator_display_name}")

            self.stdout.write(f"Début extraction autres indicateurs pour {client_conf.client_name}...")
            company_id = 1
            try:
                user_info = object_proxy_client.execute_kw(client_conf.client_odoo_db, uid_client, client_api_key,
                                                           'res.users', 'read', [[uid_client]],
                                                           {'fields': ['company_id']})
                if user_info and user_info[0].get('company_id'): company_id = user_info[0]['company_id'][0]
            except Exception:
                logger.warning(
                    f"Impossible de récupérer company_id pour client {client_conf.client_name}, utilisation de l'ID 1 par défaut.")

            # Indicateur : Date de clôture annuelle standard
            indicator_name_fiscal_closing = "date cloture annuelle"
            try:
                self.stdout.write(f"   - Recherche '{indicator_name_fiscal_closing}' depuis res.company...")
                company_data_closing = object_proxy_client.execute_kw(client_conf.client_odoo_db, uid_client,
                                                                      client_api_key, 'res.company', 'read',
                                                                      [[company_id]], {'fields': ['fiscalyear_last_day',
                                                                                                  'fiscalyear_last_month']})
                if company_data_closing and company_data_closing[0].get('fiscalyear_last_day') and company_data_closing[
                    0].get('fiscalyear_last_month'):
                    day_str = company_data_closing[0].get('fiscalyear_last_day');
                    month_str = company_data_closing[0].get('fiscalyear_last_month')
                    if day_str is not None and month_str is not None and day_str is not False and month_str is not False:
                        try:
                            day = int(day_str);
                            month = int(month_str);
                            closing_date_str = f"{day:02d}/{month:02d}";
                            indicators_data[indicator_name_fiscal_closing] = closing_date_str;
                            self.stdout.write(
                                self.style.SUCCESS(f"   - {indicator_name_fiscal_closing}: OK ({closing_date_str})"))
                        except (ValueError, TypeError) as conversion_error:
                            self.stderr.write(self.style.ERROR(
                                f"   - Erreur conversion jour/mois pour '{indicator_name_fiscal_closing}': {conversion_error} (valeurs reçues: jour='{day_str}', mois='{month_str}')"));
                            indicators_data[indicator_name_fiscal_closing] = None
                    else:
                        self.stdout.write(self.style.WARNING(
                            f"   - {indicator_name_fiscal_closing}: Valeurs jour/mois vides ou False reçues de res.company ID {company_id}."));
                        indicators_data[indicator_name_fiscal_closing] = None
                else:
                    self.stdout.write(self.style.WARNING(
                        f"   - {indicator_name_fiscal_closing}: Champs jour/mois ('fiscalyear_last_day'/'month') non trouvés ou vides sur res.company ID {company_id}."));
                    indicators_data[indicator_name_fiscal_closing] = None
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"   - Erreur extraction '{indicator_name_fiscal_closing}': {e}"));
                indicators_data[indicator_name_fiscal_closing] = None

            # Indicateur : Opérations à qualifier
            indicator_name_pending_reconciliation = "operations à qualifier"
            try:
                self.stdout.write(f"   - Recherche '{indicator_name_pending_reconciliation}'...")
                # à modifier pour inclure tous les comptes commencant par 47
                domain_pending_reco = [
                    ('account_id.code', '>=', '47%'),
                    ('account_id.code', '<=', '475%'), # prend aussi le 4755 (opérations bancaires à valider)
                    ('full_reconcile_id', '=', False),
                    ('move_id.state', '=', 'posted')
                ]
                count = object_proxy_client.execute_kw(
                    client_conf.client_odoo_db, uid_client, client_api_key,
                    'account.move.line', 'search_count',
                    [domain_pending_reco]
                )
                indicators_data[indicator_name_pending_reconciliation] = count
                self.stdout.write(self.style.SUCCESS(f"   - {indicator_name_pending_reconciliation}: OK ({count})"))
            except Exception as e:
                self.stderr.write(
                    self.style.ERROR(f"   - Erreur extraction '{indicator_name_pending_reconciliation}': {e}"))
                indicators_data[indicator_name_pending_reconciliation] = None

            # Indicateur : Achats à Traiter
            indicator_name_draft_purchases = "achats à traiter"
            try:
                self.stdout.write(f"   - Recherche '{indicator_name_draft_purchases}'...")
                journal_domain = [('type', '=', 'purchase'), ('company_id', '=', company_id)]
                journal_ids = object_proxy_client.execute_kw(
                    client_conf.client_odoo_db, uid_client, client_api_key,
                    'account.journal', 'search',
                    [journal_domain]
                )
                if not journal_ids:
                    self.stdout.write(
                        self.style.WARNING(f"   - {indicator_name_draft_purchases}: Aucun journal d'achat trouvé."))
                    indicators_data[indicator_name_draft_purchases] = 0
                else:
                    self.stdout.write(f"     - Journaux d'achat trouvés (IDs: {journal_ids}).")
                    draft_moves_domain = [
                        ('journal_id', 'in', journal_ids),
                        ('state', '=', 'draft'),
                        ('move_type', '=', 'in_invoice')
                    ]
                    draft_moves_count = object_proxy_client.execute_kw(
                        client_conf.client_odoo_db, uid_client, client_api_key,
                        'account.move', 'search_count',
                        [draft_moves_domain]
                    )
                    indicators_data[indicator_name_draft_purchases] = draft_moves_count
                    self.stdout.write(
                        self.style.SUCCESS(f"   - {indicator_name_draft_purchases}: OK ({draft_moves_count})"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"   - Erreur extraction '{indicator_name_draft_purchases}': {e}"))
                indicators_data[indicator_name_draft_purchases] = None

            # --- NOUVEL INDICATEUR : Paiements Orphelins ---
            indicator_name_orphan_payments = "paiements orphelins"
            try:
                self.stdout.write(f"   - Recherche '{indicator_name_orphan_payments}'...")
                bank_journal_domain = [('type', '=', 'bank'), ('company_id', '=', company_id)]
                bank_journal_ids = object_proxy_client.execute_kw(
                    client_conf.client_odoo_db, uid_client, client_api_key,
                    'account.journal', 'search',
                    [bank_journal_domain]
                )

                if not bank_journal_ids:
                    self.stdout.write(
                        self.style.WARNING(f"   - {indicator_name_orphan_payments}: Aucun journal de banque trouvé."))
                    indicators_data[indicator_name_orphan_payments] = 0  # Ou None
                else:
                    self.stdout.write(f"     - Journaux de banque trouvés (IDs: {bank_journal_ids}).")
                    # remplacer account_id.code par account_type (asset_receivable - creance / liability_payable -dette)
                    # Pour les comptes commençant par '40' (Fournisseurs)
                    # ne pas inclure les réconciliations partielles
                    supplier_orphan_domain = [
                        ('journal_id', 'in', bank_journal_ids),
                        ('account_id.code', '=like', '40%'),
                        ('full_reconcile_id', '=', False),
                        ('move_id.state', '=', 'posted')
                    ]
                    supplier_orphan_count = object_proxy_client.execute_kw(
                        client_conf.client_odoo_db, uid_client, client_api_key,
                        'account.move.line', 'search_count',
                        [supplier_orphan_domain]
                    )
                    self.stdout.write(f"     - Paiements fournisseurs orphelins: {supplier_orphan_count}")

                    # Pour les comptes commençant par '41' (Clients)
                    client_orphan_domain = [
                        ('journal_id', 'in', bank_journal_ids),
                        ('account_id.code', '=like', '41%'),
                        ('full_reconcile_id', '=', False),
                        ('move_id.state', '=', 'posted')
                    ]
                    client_orphan_count = object_proxy_client.execute_kw(
                        client_conf.client_odoo_db, uid_client, client_api_key,
                        'account.move.line', 'search_count',
                        [client_orphan_domain]
                    )
                    self.stdout.write(f"     - Paiements clients orphelins: {client_orphan_count}")

                    total_orphan_payments = supplier_orphan_count + client_orphan_count
                    indicators_data[indicator_name_orphan_payments] = total_orphan_payments
                    self.stdout.write(
                        self.style.SUCCESS(f"   - {indicator_name_orphan_payments}: OK ({total_orphan_payments})"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"   - Erreur extraction '{indicator_name_orphan_payments}': {e}"))
                indicators_data[indicator_name_orphan_payments] = None
            # --- FIN NOUVEL INDICATEUR ---

                # --- NOUVEL INDICATEUR : Virements internes non soldés ---
            indicator_name_unreconciled_internal_transfers = "virements internes non soldés"  # Nom technique
            try:
                self.stdout.write(f"   - Recherche '{indicator_name_unreconciled_internal_transfers}'...")
                # Compte les lignes d'écriture dans les comptes 58x non lettrées
                # et appartenant à des pièces validées.
                domain_internal_transfers = [
                    ('account_id.code', '=like', '58%'),  # Comptes commençant par 58
                    ('full_reconcile_id', '=', False),  # Non lettrées/rapprochées
                    ('move_id.state', '=', 'posted')  # Uniquement des écritures validées
                    # ('company_id', '=', company_id)     # Filtrer par compagnie si nécessaire
                ]
                count = object_proxy_client.execute_kw(
                    client_conf.client_odoo_db, uid_client, client_api_key,
                    'account.move.line', 'search_count',
                    [domain_internal_transfers]
                )
                indicators_data[indicator_name_unreconciled_internal_transfers] = count
                self.stdout.write(
                    self.style.SUCCESS(f"   - {indicator_name_unreconciled_internal_transfers}: OK ({count})"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(
                    f"   - Erreur extraction '{indicator_name_unreconciled_internal_transfers}': {e}"))
                indicators_data[indicator_name_unreconciled_internal_transfers] = None
            # --- FIN NOUVEL INDICATEUR ---
                # --- NOUVEL INDICATEUR : Pivot Encaissement (Solde des comptes 478*) ---
            indicator_name_pivot_encaissement = "pivot encaissement"
            try:
                self.stdout.write(f"   - Recherche '{indicator_name_pivot_encaissement}'...")
                    # Domaine pour récupérer les lignes des comptes commençant par 478,
                    # et appartenant à des pièces comptables validées.
                domain_pivot_encaissement = [
                        ('account_id.code', '=like', '478%'),  # Comptes commençant par 478
                        ('move_id.state', '=', 'posted')  # Uniquement des écritures validées
                        # ('company_id', '=', company_id)     # Filtrer par compagnie si nécessaire
                    ]

                # Récupérer les lignes avec les champs débit et crédit
                lines = object_proxy_client.execute_kw(
                        client_conf.client_odoo_db, uid_client, client_api_key,
                        'account.move.line', 'search_read',
                        [domain_pivot_encaissement],
                        {'fields': ['debit', 'credit']}
                    )

                solde_pivot = 0.0
                if lines:
                    for line in lines:
                        solde_pivot += line.get('debit', 0.0) - line.get('credit', 0.0)

                    # Formatter le solde pour l'affichage
                formatted_solde_pivot = f"{solde_pivot:,.2f}"
                indicators_data[indicator_name_pivot_encaissement] = formatted_solde_pivot
                self.stdout.write(
                    self.style.SUCCESS(f"   - {indicator_name_pivot_encaissement}: OK ({formatted_solde_pivot})"))

            except Exception as e:
                self.stderr.write(
                    self.style.ERROR(f"   - Erreur extraction '{indicator_name_pivot_encaissement}': {e}"))
                indicators_data[indicator_name_pivot_encaissement] = None
                # --- FIN NOUVEL INDICATEUR ---
                # --- INDICATEUR : Dernière clôture fiscale (basée sur account.change.lock.date - fiscalyear_lock_date) ---
            indicator_name_last_fiscal_lock_date = "derniere cloture fiscale"
            try:
                self.stdout.write(
                    f"   - Recherche '{indicator_name_last_fiscal_lock_date}' depuis account.change.lock.date...")

                # Récupérer la date de clôture globale (fiscalyear_lock_date)
                lock_info_data = object_proxy_client.execute_kw(
                    client_conf.client_odoo_db, uid_client, client_api_key,
                    'account.change.lock.date', 'search_read',
                    [[]],  # Pas de filtre spécifique, on prend le premier (ou le seul) enregistrement
                    {
                        'fields': ['fiscalyear_lock_date'],  # Champ cible
                        'limit': 1
                        # 'order': 'id desc' # Optionnel, pour prendre le plus récent si plusieurs enregistrements existent
                    }
                )

                if lock_info_data and lock_info_data[0].get('fiscalyear_lock_date'):
                    fiscalyear_lock_date_value = lock_info_data[0]['fiscalyear_lock_date']
                    # La date est déjà au format YYYY-MM-DD
                    indicators_data[indicator_name_last_fiscal_lock_date] = fiscalyear_lock_date_value
                    self.stdout.write(self.style.SUCCESS(
                        f"   - {indicator_name_last_fiscal_lock_date}: OK ({fiscalyear_lock_date_value})"))
                else:
                    self.stdout.write(self.style.WARNING(
                        f"   - {indicator_name_last_fiscal_lock_date}: Aucune date de clôture globale (fiscalyear_lock_date) trouvée ou champ vide."))
                    indicators_data[indicator_name_last_fiscal_lock_date] = None  # Ou une chaîne "N/A"
            except Exception as e:
                self.stderr.write(
                    self.style.ERROR(f"   - Erreur extraction '{indicator_name_last_fiscal_lock_date}': {e}"))
                indicators_data[indicator_name_last_fiscal_lock_date] = None

                # --- MODIFICATION : Solde des Virements Internes (Comptes 58*) ---
            indicator_name_internal_transfer_balance = "solde virements internes"
            try:
                self.stdout.write(f"   - Recherche '{indicator_name_internal_transfer_balance}'...")
                # Domaine pour récupérer les lignes d'écriture des comptes commençant par 58
                # et appartenant à des pièces validées.
                domain_internal_transfers_lines = [
                    ('account_id.code', '=like', '58%'),  # Comptes commençant par 58
                    ('move_id.state', '=', 'posted'),  # Uniquement des écritures validées
                    ('company_id', '=', company_id)  # Filtrer par compagnie
                ]

                # Utiliser read_group pour sommer le champ 'balance' de account.move.line
                grouped_data = object_proxy_client.execute_kw(
                    client_conf.client_odoo_db, uid_client, client_api_key,
                    'account.move.line', 'read_group',
                    [domain_internal_transfers_lines, ['balance'], []],
                    # Domaines, champs à agréger, champs de regroupement (aucun ici)
                    {'lazy': False}  # S'assurer que le calcul est fait
                )

                total_balance_58 = 0.0
                if grouped_data and grouped_data[0].get(
                        'balance') is not None:  # read_group retourne une liste de dictionnaires
                    total_balance_58 = grouped_data[0]['balance']

                formatted_balance_58 = f"{total_balance_58:,.2f}"  # Formatter avec 2 décimales
                indicators_data[indicator_name_internal_transfer_balance] = formatted_balance_58
                self.stdout.write(self.style.SUCCESS(
                    f"   - {indicator_name_internal_transfer_balance}: OK ({formatted_balance_58})"))

            except Exception as e:
                self.stderr.write(
                    self.style.ERROR(f"   - Erreur extraction '{indicator_name_internal_transfer_balance}': {e}"))
                indicators_data[indicator_name_internal_transfer_balance] = None

                # --- NOUVEL INDICATEUR : Périodicité TVA ---
            indicator_name_vat_periodicity = "periodicite tva"
            # !!! VÉRIFIEZ ET ADAPTEZ 'account_tax_periodicity' au vrai nom technique du champ sur res.company !!!
            technical_field_vat_periodicity_on_company = 'account_tax_periodicity'  # Hypothèse basée sur res.config.settings
            # -----------------------------------------------------------------------------------------------------
            try:
                self.stdout.write(
                    f"   - Recherche '{indicator_name_vat_periodicity}' depuis res.company (champ: {technical_field_vat_periodicity_on_company})...")
                company_config_data = object_proxy_client.execute_kw(
                    client_conf.client_odoo_db, uid_client, client_api_key,
                    'res.company', 'read',
                    [[company_id]],  # Utilise l'ID de la compagnie active
                    {'fields': [technical_field_vat_periodicity_on_company]}
                )
                if company_config_data and company_config_data[0].get(
                        technical_field_vat_periodicity_on_company) is not False:
                    vat_periodicity_value = company_config_data[0][technical_field_vat_periodicity_on_company]

                    # Odoo retourne souvent la clé pour les champs 'selection'.
                    # Nous allons créer un petit mapping pour afficher la valeur lisible en français.
                    vat_periodicity_map = {
                        'monthly': 'Mensuel',
                        'quarterly': 'Trimestriel',
                        'yearly': 'Annuel',
                        # Ajoutez d'autres clés/valeurs si nécessaire
                    }
                    display_value = vat_periodicity_map.get(vat_periodicity_value,
                                                            vat_periodicity_value)  # Fallback sur la valeur brute

                    if display_value:
                        indicators_data[indicator_name_vat_periodicity] = str(display_value)
                        self.stdout.write(
                            self.style.SUCCESS(f"   - {indicator_name_vat_periodicity}: OK ({display_value})"))
                    else:
                        self.stdout.write(self.style.WARNING(
                            f"   - {indicator_name_vat_periodicity}: Champ '{technical_field_vat_periodicity_on_company}' vide sur res.company ID {company_id}."))
                        indicators_data[indicator_name_vat_periodicity] = "Non définie"
                else:
                    self.stdout.write(self.style.WARNING(
                        f"   - {indicator_name_vat_periodicity}: Champ '{technical_field_vat_periodicity_on_company}' non trouvé ou non défini sur res.company ID {company_id}."))
                    indicators_data[indicator_name_vat_periodicity] = "Non définie"
            except Exception as e:
                self.stderr.write(
                    self.style.ERROR(f"   - Erreur extraction '{indicator_name_vat_periodicity}': {e}"))
                indicators_data[indicator_name_vat_periodicity] = None
                # --- FIN MODIFICATION ---
            # --- NOUVEL INDICATEUR : Nombre de Modèles Personnalisés (Workflows) ---
            indicator_name_custom_models = "nb modeles personnalises (indicatif)"
            try:
                self.stdout.write(f"   - Recherche '{indicator_name_custom_models}'...")
                # Compte les modèles dont le nom technique commence par 'x_'
                domain_custom_models = [
                    ('model', '=like', 'x_%'),
                    # On pourrait ajouter d'autres filtres si besoin, par exemple pour exclure certains types de modèles x_
                ]
                count = object_proxy_client.execute_kw(
                    client_conf.client_odoo_db, uid_client, client_api_key,
                    'ir.model', 'search_count',  # Modèle contenant la liste des modèles de la base
                    [domain_custom_models]
                )
                indicators_data[indicator_name_custom_models] = count
                self.stdout.write(self.style.SUCCESS(f"   - {indicator_name_custom_models}: OK ({count})"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"   - Erreur extraction '{indicator_name_custom_models}': {e}"))
                indicators_data[indicator_name_custom_models] = None
            # --- NOUVEL INDICATEUR : Nombre d'Actions Automatisées ---
            indicator_name_automated_actions = "nb actions automatisées"
            try:
                self.stdout.write(f"   - Recherche '{indicator_name_automated_actions}'...")
                # uniquement du module "règles d'action automatisée : base_automation"
                # Compte tous les enregistrements dans ir.actions.server.
                # On pourrait ajouter des filtres si on veut, par exemple, ne compter que celles actives : [('state', '=', 'code')]
                # ou celles qui ne sont pas des actions de base fournies par Odoo (plus complexe à filtrer).
                domain_automated_actions = []  # Pas de filtre pour l'instant, compte tout.
                count = object_proxy_client.execute_kw(
                    client_conf.client_odoo_db, uid_client, client_api_key,
                    'ir.actions.server', 'search_count',
                    [domain_automated_actions]
                )
                indicators_data[indicator_name_automated_actions] = count
                self.stdout.write(self.style.SUCCESS(f"   - {indicator_name_automated_actions}: OK ({count})"))
            except Exception as e:
                self.stderr.write(
                    self.style.ERROR(f"   - Erreur extraction '{indicator_name_automated_actions}': {e}"))
                indicators_data[indicator_name_automated_actions] = None
                # --- FIN NOUVEL INDICATEUR ---

            # --- NOUVEL INDICATEUR : Nombre d'Utilisateurs Actifs ---
            indicator_name_active_users = "nb utilisateurs actifs"
            try:
                self.stdout.write(f"   - Recherche '{indicator_name_active_users}'...")
                # Compte les utilisateurs actifs et non "partagés" (internes)
                domain_active_users = [
                    ('active', '=', True),
                    ('share', '=', False)  # Exclut les utilisateurs portail/publics
                ]
                count = object_proxy_client.execute_kw(
                    client_conf.client_odoo_db, uid_client, client_api_key,
                    'res.users', 'search_count',
                    [domain_active_users]
                )
                indicators_data[indicator_name_active_users] = count
                self.stdout.write(self.style.SUCCESS(f"   - {indicator_name_active_users}: OK ({count})"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"   - Erreur extraction '{indicator_name_active_users}': {e}"))
                indicators_data[indicator_name_active_users] = None
            # --- FIN NOUVEL INDICATEUR ---
            # --- NOUVEL INDICATEUR : Nombre TOTAL d'Utilisateurs avec email @lpde.pro (peu importe active/share) ---
            indicator_name_total_lpde_email_users = "nb utilisateurs lpde"
            try:
                self.stdout.write(f"   - Recherche '{indicator_name_total_lpde_email_users}'...")
                # Compte tous les utilisateurs (actifs ou non, partagés ou non)
                # dont l'email se termine par @lpde.pro
                domain_total_lpde_email_users = [
                    ('login', '=like', '%@lpde.pro')  # Email se terminant par @lpde.pro
                ]
                count = object_proxy_client.execute_kw(
                    client_conf.client_odoo_db, uid_client, client_api_key,
                    'res.users', 'search_count',
                    [domain_total_lpde_email_users]
                )
                indicators_data[indicator_name_total_lpde_email_users] = count
                self.stdout.write(self.style.SUCCESS(f"   - {indicator_name_total_lpde_email_users}: OK ({count})"))
            except Exception as e:
                self.stderr.write(
                    self.style.ERROR(f"   - Erreur extraction '{indicator_name_total_lpde_email_users}': {e}"))
                indicators_data[indicator_name_total_lpde_email_users] = None
            # --- FIN NOUVEL INDICATEUR ---
            # --- MODIFICATION : Nombre de Modules APPLICATIFS Activés ---
            indicator_name_active_applications = "nb modules actifs"  # Nouveau nom plus spécifique
            try:
                self.stdout.write(f"   - Recherche '{indicator_name_active_applications}'...")
                # Compte les modules installés qui sont des applications principales
                domain_active_applications = [
                    ('state', '=', 'installed'),
                    ('application', '=', True)  # Filtre pour ne compter que les applications
                ]
                count = object_proxy_client.execute_kw(
                    client_conf.client_odoo_db, uid_client, client_api_key,
                    'ir.module.module', 'search_count',
                    [domain_active_applications]
                )
                indicators_data[indicator_name_active_applications] = count
                self.stdout.write(self.style.SUCCESS(f"   - {indicator_name_active_applications}: OK ({count})"))
            except Exception as e:
                self.stderr.write(
                    self.style.ERROR(f"   - Erreur extraction '{indicator_name_active_applications}': {e}"))
                indicators_data[indicator_name_active_applications] = None
                # --- FIN MODIFICATION ---

            indicator_name_db_activation_date = "date activation base"
            try:
                self.stdout.write(
                    f"   - Recherche '{indicator_name_db_activation_date}' via le premier module installé...")
                # Recherche le premier module installé en triant par date de création ascendante
                first_module_data = object_proxy_client.execute_kw(
                    client_conf.client_odoo_db, uid_client, client_api_key,
                    'ir.module.module', 'search_read',
                    [[]],  # Pas de filtre spécifique sur le nom du module
                    {
                        'fields': ['create_date'],  # On s'intéresse à la date de création
                        'limit': 1,
                        'order': 'create_date asc'  # Tri pour obtenir le plus ancien (premier installé)
                    }
                )

                self.stdout.write(f"     - Données brutes pour le premier module: {first_module_data}")

                if first_module_data and first_module_data[0].get('create_date'):
                    activation_date_str = first_module_data[0]['create_date']
                    self.stdout.write(
                        f"     - create_date brute du premier module: {activation_date_str} (type: {type(activation_date_str)})")
                    try:
                        if activation_date_str is False:
                            self.stdout.write(self.style.WARNING(
                                f"   - {indicator_name_db_activation_date}: create_date est False pour le premier module."))
                            indicators_data[indicator_name_db_activation_date] = None
                        elif isinstance(activation_date_str, str):
                            dt_object = datetime.strptime(activation_date_str, '%Y-%m-%d %H:%M:%S')
                            formatted_activation_date = dt_object.strftime('%d/%m/%Y')
                            indicators_data[indicator_name_db_activation_date] = formatted_activation_date
                            self.stdout.write(self.style.SUCCESS(
                                f"   - {indicator_name_db_activation_date}: OK ({formatted_activation_date})"))
                        else:
                            self.stdout.write(self.style.WARNING(
                                f"   - {indicator_name_db_activation_date}: create_date n'est pas une chaîne de caractères attendue ({activation_date_str})."))
                            indicators_data[indicator_name_db_activation_date] = None
                    except ValueError as ve:
                        self.stdout.write(self.style.WARNING(
                            f"   - {indicator_name_db_activation_date}: Format de date inattendu ('{activation_date_str}'), erreur: {ve}. Stockage brut."))
                        indicators_data[indicator_name_db_activation_date] = activation_date_str
                    except Exception as e_format:
                        self.stderr.write(self.style.ERROR(
                            f"   - Erreur formatage date pour '{indicator_name_db_activation_date}': {e_format}"))
                        indicators_data[indicator_name_db_activation_date] = None
                else:
                    self.stdout.write(self.style.WARNING(
                        f"   - {indicator_name_db_activation_date}: Date de création du premier module non trouvée ou champ 'create_date' manquant/vide."))
                    indicators_data[indicator_name_db_activation_date] = None
            except Exception as e:
                self.stderr.write(
                    self.style.ERROR(f"   - Erreur extraction globale '{indicator_name_db_activation_date}': {e}"))
                indicators_data[indicator_name_db_activation_date] = None
            # --- FIN MODIFICATION ---
            # Indicateur : Résultat Provisoire de l'Année en Cours
            indicator_name_provisional_result = "resultat_provisoire_annee_courante"
            try:
                self.stdout.write(
                    f"   - Recherche '{indicator_name_provisional_result}' (Période: {first_day_of_current_year_str} à {today_str})...")
                income_account_prefixes = ['70', '71', '72', '73', '74', '75', '76', '77', '78', '79']
                expense_account_prefixes = ['60', '61', '62', '63', '64', '65', '66', '67', '68', '69']

                total_income_balance = self.get_account_balance_sum_for_period(
                    object_proxy_client, client_conf.client_odoo_db, uid_client, client_api_key,
                    income_account_prefixes, company_id, first_day_of_current_year_str, today_str
                )
                total_income = -total_income_balance
                self.stdout.write(
                    f"     - Total Produits (Classe 7): {total_income:,.2f} (solde brut: {total_income_balance:,.2f})")

                total_expense_balance = self.get_account_balance_sum_for_period(
                    object_proxy_client, client_conf.client_odoo_db, uid_client, client_api_key,
                    expense_account_prefixes, company_id, first_day_of_current_year_str, today_str
                 )
                total_expense = total_expense_balance
                self.stdout.write(
                    f"     - Total Charges (Classe 6): {total_expense:,.2f} (solde brut: {total_expense_balance:,.2f})")

                provisional_result = total_income - total_expense
                formatted_result = f"{provisional_result:,.2f}"

                indicators_data[indicator_name_provisional_result] = formatted_result
                self.stdout.write(
                    self.style.SUCCESS(f"   - {indicator_name_provisional_result}: OK ({formatted_result})"))

            except Exception as e:
                self.stderr.write(
                    self.style.ERROR(f"   - Erreur extraction '{indicator_name_provisional_result}': {e}"))
                indicators_data[indicator_name_provisional_result] = None


            # 5e. Sauvegarder les résultats en BDD
            saved_count = 0;
            error_count = 0
            self.stdout.write(f"Sauvegarde des indicateurs trouvés...")
            for name, value in indicators_data.items():
                if value is not None:
                    try:
                        IndicateursHistoriques.objects.create(
                            client=client_conf, indicator_name=name, indicator_value=str(value),
                            extraction_timestamp=current_extraction_run_timestamp,
                            assigned_odoo_collaborator_id=final_assigned_collab_id_str,
                            assigned_collaborator_name=collaborator_display_name)
                        saved_count += 1
                    except Exception as e:
                        self.stderr.write(self.style.ERROR(
                            f"   - Erreur sauvegarde indicateur '{name}' pour {client_conf.client_name}: {e}"));
                        error_count += 1
            if error_count > 0:
                self.stderr.write(self.style.ERROR(
                    f"{saved_count} indicateur(s) sauvegardé(s), {error_count} erreur(s) de sauvegarde pour {client_conf.client_name}."))
            elif saved_count > 0:
                self.stdout.write(self.style.SUCCESS(
                    f"{saved_count} indicateur(s) sauvegardé(s) avec succès pour {client_conf.client_name}."))
            else:
                self.stdout.write(self.style.WARNING(
                    f"Aucun nouvel indicateur trouvé ou à sauvegarder pour {client_conf.client_name}."))

        self.stdout.write(self.style.SUCCESS("\n--- Fin de l'extraction des indicateurs ---"))
