# core/templatetags/core_tags.py
from django import template

register = template.Library()

@register.filter(name='get_item') # Enregistre le filtre sous le nom 'get_item'
def get_item(dictionary, key):
    """
    Permet d'accéder à une clé d'un dictionnaire dans un template Django.
    Usage: {{ my_dictionary|get_item:my_key }}
    Retourne None si la clé n'existe pas.
    """
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None

@register.filter(name='dict_from_list') # Enregistre le filtre sous le nom 'dict_from_list'
def dict_from_list(object_list, key_name):
    """
    Transforme une liste d'objets en dictionnaire, en utilisant la valeur
    d'un attribut spécifique de chaque objet comme clé.
    Usage: {% with my_dict=object_list|dict_from_list:'attribute_name' %}
    """
    if not object_list:
        return {}
    try:
        # Crée un dictionnaire {valeur_attribut: objet_entier}
        return {getattr(obj, key_name): obj for obj in object_list}
    except AttributeError:
        # Si l'attribut n'existe pas sur un des objets
        return {}

# --- NOUVEAU FILTRE AJOUTÉ ---
@register.filter(name='format_collab_name')
def format_collab_name(value):
    """
    Transforme une chaîne comme "Nom Société, Prénom NOM" en "Prénom NOM".
    Si la chaîne ne contient pas de virgule, elle est retournée telle quelle.
    """
    if isinstance(value, str):
        parts = value.split(',', 1) # Sépare à la première virgule seulement
        if len(parts) > 1:
            return parts[1].strip() # Prend la partie après la virgule et enlève les espaces
    return value # Retourne la valeur originale si pas de virgule ou si ce n'est pas une chaîne
# --- FIN NOUVEAU FILTRE ---
