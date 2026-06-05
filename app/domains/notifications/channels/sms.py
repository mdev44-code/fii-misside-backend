"""
channels/sms.py — Envoi de SMS via AfricasTalking.

AfricasTalking est un provider SMS très répandu en Afrique de l'Ouest.
Il fonctionne en mode sandbox (test gratuit) et production.

Mode sandbox :
  - username = "sandbox"
  - Les SMS sont "envoyés" mais ne partent pas vraiment
  - Utile pour développer sans payer
  - On peut voir les SMS dans le dashboard AfricasTalking

Mode production :
  - username = ton vrai username AT
  - api_key  = ta vraie clé API
  - Les SMS partent réellement sur les numéros des membres

La bibliothèque 'africastalking' est synchrone (pas async).
On l'appelle directement — pour un projet de cette taille,
c'est acceptable. Si le volume de SMS devient très grand,
on pourrait utiliser un executor pour ne pas bloquer la boucle async.
"""

import africastalking

from app.config import settings
from app.domains.notifications.channels.base import (
    BaseNotificationChannel,
    NotificationPayload,
)


class SMSChannel(BaseNotificationChannel):
    """
    Canal SMS utilisant l'API AfricasTalking.

    Hérite de BaseNotificationChannel et implémente :
      - send()         : envoie le SMS
      - channel_name() : retourne "sms"
    """

    def __init__(self) -> None:
        """
        Initialise la connexion AfricasTalking au démarrage.
        Appelé une seule fois quand NotificationService est créé.
        """
        africastalking.initialize(
            username=settings.africastalking_username,
            api_key=settings.africastalking_api_key,
        )
        # Récupère le service SMS depuis le SDK
        self._sms = africastalking.SMS

    def channel_name(self) -> str:
        return "sms"

    async def send(self, payload: NotificationPayload) -> bool:
        """
        Envoie un SMS au numéro du membre.

        Le SDK AfricasTalking retourne une réponse structurée :
        {
            "SMSMessageData": {
                "Recipients": [
                    {
                        "number"    : "+221771234567",
                        "status"    : "Success",
                        "messageId" : "ATXid_...",
                        "cost"      : "XOF 10"
                    }
                ]
            }
        }

        On vérifie que le statut du premier destinataire est "Success".
        """
        try:
            response = self._sms.send(
                message=payload.message,
                recipients=[payload.recipient_phone],
                # sender_id : nom affiché sur le SMS (ex: "ASSO")
                # Optionnel — certains pays ne le supportent pas
                sender_id=settings.africastalking_sender_id or None,
            )

            recipients = response.get("SMSMessageData", {}).get("Recipients", [])

            if not recipients:
                print("⚠️ SMS : aucun destinataire dans la réponse AT")
                return False

            status = recipients[0].get("status", "")
            if status == "Success":
                print(f"✅ SMS envoyé à {payload.recipient_phone}")
                return True
            else:
                print(f"❌ SMS échoué pour {payload.recipient_phone} — statut : {status}")
                return False

        except Exception as e:
            # On attrape toutes les exceptions pour ne pas bloquer
            # la transaction financière si le SMS plante
            print(f"❌ Erreur SMS pour {payload.recipient_phone} : {e}")
            return False
