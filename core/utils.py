# core/utils.py
import xmlrpc.client
import logging
import base64
from django.conf import settings
from cryptography.fernet import Fernet, InvalidToken

from .models import ConfigurationCabinet

logger = logging.getLogger(__name__)

def connect_odoo(url, db, username, password):
    """
    Tente de se connecter à Odoo.
    Retourne uid, common_proxy, object_proxy, full_version_str, error_message.
    """
    error_message = None
    full_version_str = "Inconnue" # Valeur par défaut
    server_version_api = None # Pour stocker la version de common.version()

    try:
        common_proxy = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
        version_info_dict = common_proxy.version()
        logger.info(f"Odoo Server Info Dict (common.version): {version_info_dict} (URL: {url})")

        # Première tentative avec server_version ou server_serie
        server_version_api = version_info_dict.get('server_version')
        server_serie_api = version_info_dict.get('server_serie')

        if server_version_api:
            full_version_str = server_version_api
            # Vérifier si c'est une version SaaS avec un format comme "17.0+e-saas~17.3+e"
            if "saas~" in server_version_api:
                try:
                    saas_part = server_version_api.split('saas~')[1].split('+')[0]
                    if saas_part: # ex: "17.3"
                         # Vérifier si saas_part est un format X.Y valide
                        if len(saas_part.split('.')) >= 2:
                            full_version_str = saas_part
                            logger.info(f"Version SaaS détectée et formatée: {full_version_str}")
                except IndexError:
                    logger.warning(f"Format saas~ inattendu dans server_version: {server_version_api}")


        elif server_serie_api:
            full_version_str = server_serie_api

        uid = common_proxy.authenticate(db, username, password, {})
        if not uid:
            error_message = f"Échec de l'authentification Odoo pour {username} sur {db}@{url}"
            logger.error(error_message)
            # On retourne la version obtenue via common.version() même si l'auth échoue
            return None, None, None, full_version_str, error_message

        object_proxy = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')
        logger.info(f"Authentification réussie pour {username} (UID: {uid}) sur {url}")

        # Deuxième tentative : affiner la version avec ir.module.module pour 'base'
        try:
            base_module_data = object_proxy.execute_kw(
                db, uid, password,
                'ir.module.module', 'search_read',
                [[('name', '=', 'base')]],
                {'fields': ['latest_version'], 'limit': 1}
            )
            if base_module_data and base_module_data[0].get('latest_version'):
                module_latest_version = base_module_data[0]['latest_version']
                logger.info(f"Version du module 'base' (latest_version): {module_latest_version}")
                # Ex: "17.0.1.2.0" ou "17.0.saas~17.3.1"
                # Si server_version contenait déjà une version SaaS plus précise (ex: 17.3), on la garde.
                # Sinon, on essaie de formater module_latest_version.
                if not ("saas~" in full_version_str and len(full_version_str.split('.')) >= 2 and full_version_str.split('.')[0] == module_latest_version.split('.')[0]):
                    if module_latest_version:
                        # Si module_latest_version contient "saas~", extraire cette partie
                        if "saas~" in module_latest_version:
                            try:
                                saas_part_module = module_latest_version.split('saas~')[1].split('.')[0] + '.' + module_latest_version.split('saas~')[1].split('.')[1]
                                if len(saas_part_module.split('.')) >= 2: # Assure X.Y
                                    full_version_str = saas_part_module
                                    logger.info(f"Version SaaS (module base) formatée: {full_version_str}")
                            except IndexError:
                                full_version_str = module_latest_version # Fallback au latest_version complet
                        else:
                            # Pour les versions comme "17.0.1.0.0", on pourrait prendre "17.0.1"
                            parts = module_latest_version.split('.')
                            if len(parts) >= 2:
                                formatted_module_version = f"{parts[0]}.{parts[1]}"
                                if len(parts) > 2 and parts[2] != '0': # Ajoute le troisième segment s'il n'est pas 0
                                    formatted_module_version += f".{parts[2]}"
                                full_version_str = formatted_module_version
                                logger.info(f"Version module base formatée: {full_version_str}")
                            else:
                                full_version_str = module_latest_version # Fallback

        except Exception as e_mod:
            logger.warning(f"Impossible de récupérer la version depuis ir.module.module: {e_mod}. Utilisation de la version API: {full_version_str}")
            # On garde la version obtenue de common.version() si l'appel au module échoue

        return uid, common_proxy, object_proxy, full_version_str, None

    except xmlrpc.client.Fault as e:
        error_message = f"Erreur XML-RPC Odoo ({url}): {e.faultCode} - {e.faultString}"
        logger.error(error_message)
        return None, None, None, full_version_str, error_message
    except ConnectionRefusedError as e:
        error_message = f"Connexion refusée par le serveur Odoo ({url}): {e}"
        logger.error(error_message)
        return None, None, None, "Inconnue", error_message
    except Exception as e:
        error_message = f"Erreur de connexion Odoo inattendue ({url}): {e}"
        logger.error(error_message, exc_info=True)
        return None, None, None, full_version_str, error_message

def encrypt_value(plain_text_value):
    if not settings.FERNET_KEY:
        logger.error("FERNET_KEY non configurée pour le chiffrement.")
        raise ValueError("Clé de chiffrement (FERNET_KEY) non configurée.")
    if not plain_text_value:
        return ""
    try:
        f = Fernet(settings.FERNET_KEY.encode())
        encrypted_value = f.encrypt(plain_text_value.encode('utf-8'))
        return base64.urlsafe_b64encode(encrypted_value).decode('utf-8')
    except Exception as e:
        logger.error(f"Erreur lors du chiffrement: {e}", exc_info=True)
        raise ValueError(f"Erreur de chiffrement: {e}")

def decrypt_value(encrypted_b64_value):
    if not settings.FERNET_KEY:
        logger.error("FERNET_KEY non configurée pour le déchiffrement.")
        return None
    if not encrypted_b64_value:
        return ""
    try:
        f = Fernet(settings.FERNET_KEY.encode())
        encrypted_value_bytes = base64.urlsafe_b64decode(encrypted_b64_value.encode('utf-8'))
        decrypted_value = f.decrypt(encrypted_value_bytes)
        return decrypted_value.decode('utf-8')
    except (InvalidToken, TypeError, ValueError, base64.binascii.Error) as e:
        logger.warning(f"Impossible de déchiffrer la valeur (utils): {e}. Valeur reçue: '{encrypted_b64_value[:20]}...'")
        return None
    except Exception as e:
        logger.error(f"Erreur inattendue lors du déchiffrement (utils): {e}", exc_info=True)
        return None

def get_odoo_cabinet_collaborators():
    collaborators_choices = []
    try:
        config = ConfigurationCabinet.objects.first()
        if not config:
            logger.error("Configuration Odoo Cabinet non trouvée (get_odoo_cabinet_collaborators).")
            return collaborators_choices

        api_key = decrypt_value(config.firm_odoo_encrypted_api_key)
        if not api_key:
            logger.error("Impossible de déchiffrer la clé API du cabinet (get_odoo_cabinet_collaborators).")
            return collaborators_choices

        uid, _, object_proxy, _, conn_error = connect_odoo(
            config.firm_odoo_url,
            config.firm_odoo_db,
            config.firm_odoo_api_user,
            api_key
        )

        if conn_error:
            logger.error(f"Erreur de connexion lors de la récupération des collaborateurs: {conn_error}")
            return collaborators_choices

        if uid and object_proxy:
            domain = [("partner_share", "=", False)]
            fields = ['name']
            order = 'name'
            collaborator_data = object_proxy.execute_kw(
                config.firm_odoo_db, uid, api_key,
                'res.partner', 'search_read',
                [domain],
                {'fields': fields, 'order': order, 'limit': 200}
            )
            collaborators_choices = [(str(c['id']), c['name']) for c in collaborator_data]
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des collaborateurs Odoo Cabinet: {e}", exc_info=True)
    return collaborators_choices
