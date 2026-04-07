"""
channels/base.py — Contrat (interface) que tout canal de notification doit respecter.

Principe Open/Closed (SOLID) :
  - Fermé à la modification : on ne touche jamais ce fichier pour ajouter un canal
  - Ouvert à l'extension  : on ajoute un nouveau fichier (whatsapp.py, email.py...)

Aujourd'hui on a : SMS (AfricasTalking) + InApp (base de données)
Demain on peut ajouter : WhatsApp Business, Email, Telegram...
→ Sans modifier une seule ligne du service ou du router.

ABC (Abstract Base Class) :
  Une classe qui définit un contrat sans l'implémenter.
  Impossible d'instancier BaseNotificationChannel directement.
  Toute sous-classe DOIT implémenter les méthodes @abstractmethod,
  sinon Python lève TypeError au démarrage de l'application.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class NotificationPayload:
    """
    Données nécessaires pour envoyer une notification.

    dataclass : génère automatiquement __init__, __repr__, __eq__
    à partir des attributs déclarés — moins de boilerplate que dict.

    recipient_phone : numéro au format international (+221...)
    recipient_id    : UUID du membre (pour les notifications in-app)
    message         : texte du message à envoyer
    notification_type : type pour catégoriser dans la BDD
    """
    recipient_phone: str
    recipient_id: str
    message: str
    notification_type: str


class BaseNotificationChannel(ABC):
    """
    Interface commune pour tous les canaux de notification.

    Chaque canal hérite de cette classe et implémente :
      - send()         : envoie la notification, retourne True si succès
      - channel_name() : retourne l'identifiant du canal (pour les logs)

    Usage dans le service :
        channels = [SMSChannel(), InAppChannel(db)]
        for channel in channels:
            success = await channel.send(payload)
            # On ne sait pas si c'est SMS ou InApp — on s'en fiche
            # C'est le polymorphisme en action.
    """

    @abstractmethod
    async def send(self, payload: NotificationPayload) -> bool:
        """
        Envoie la notification.

        Returns:
            True  : notification envoyée avec succès
            False : échec (sans lever d'exception)

        Les canaux ne lèvent PAS d'exception en cas d'échec —
        ils retournent False. C'est le service qui décide quoi faire
        (logger l'erreur, réessayer, alerter l'admin...).
        """
        ...

    @abstractmethod
    def channel_name(self) -> str:
        """
        Retourne le nom du canal pour les logs et la BDD.
        Exemples : "sms", "in_app", "whatsapp"
        """
        ...