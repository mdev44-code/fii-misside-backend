"""
email/sender.py — Envoi d'emails transactionnels via SMTP.

Mode dev (SMTP_HOST vide) : le code s'affiche dans les logs Docker.
Mode prod (SMTP_HOST renseigné) : envoi réel via Brevo.
"""

import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from app.config import settings


def _build_password_reset_message(to_email: str, full_name: str, code: str) -> EmailMessage:
    """Construit l'email HTML + texte contenant le code OTP."""
    msg = EmailMessage()
    msg["Subject"] = "Réinitialisation de votre mot de passe — Fii Misside"
    from_email = settings.smtp_from_email or settings.smtp_user
    msg["From"] = f"{settings.smtp_from_name} <{from_email}>"
    msg["To"] = to_email

    # Version texte
    msg.set_content(
        f"Bonjour {full_name},\n\n"
        f"Voici votre code de réinitialisation de mot de passe :\n\n"
        f"    {code}\n\n"
        f"Ce code est valable pendant 5 minutes.\n\n"
        f"Si vous n'êtes pas à l'origine de cette demande, ignorez cet email : "
        f"votre mot de passe restera inchangé.\n\n"
        f"— L'équipe Fii Misside"
    )

    # Version HTML
    msg.add_alternative(
        f"""\
<!DOCTYPE html>
<html lang="fr">
  <body style="margin:0;padding:0;background:#f5f0e8;font-family:'Segoe UI',Arial,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           style="background:#f5f0e8;padding:32px 0;">
      <tr><td align="center">
        <table role="presentation" width="480" cellpadding="0" cellspacing="0"
               style="background:#ffffff;border-radius:16px;overflow:hidden;
                      box-shadow:0 4px 24px rgba(13,26,16,.08);">
          <tr>
            <td style="background:rgb(35,112,68);padding:24px 32px;">
              <h1 style="margin:0;color:#ffffff;font-size:20px;font-weight:700;">
                Fii Misside
              </h1>
            </td>
          </tr>
          <tr>
            <td style="padding:32px;">
              <p style="margin:0 0 16px;color:#0d1a10;font-size:15px;">
                Cher {full_name},
              </p>
              <p style="margin:0 0 24px;color:#0d1a10;font-size:15px;line-height:1.5;">
                Voici votre code pour réinitialiser votre mot de passe :
              </p>
              <div style="text-align:center;margin:0 0 24px;">
                <span style="display:inline-block;background:#f5f0e8;
                             color:rgb(35,112,68);font-size:34px;font-weight:700;
                             letter-spacing:10px;padding:16px 28px;border-radius:12px;">
                  {code}
                </span>
              </div>
              <p style="margin:0 0 8px;color:#6b7d6f;font-size:13px;line-height:1.5;">
                Ce code est valable pendant <strong>5 minutes</strong>.
              </p>
              <p style="margin:0;color:#6b7d6f;font-size:13px;line-height:1.5;">
                Si vous n'êtes pas à l'origine de cette demande, ignorez cet email :
                votre mot de passe restera inchangé.
              </p>
            </td>
          </tr>
          <tr>
            <td style="padding:20px 32px;border-top:1px solid #eee;">
              <p style="margin:0;color:#6b7d6f;font-size:12px;">
                — Fii Misside
              </p>
            </td>
          </tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>""",
        subtype="html",
    )
    return msg


def _send_smtp_blocking(msg: EmailMessage) -> None:
    """Envoi SMTP synchrone. Appelé dans un thread séparé via asyncio.to_thread."""
    context = ssl.create_default_context()

    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, context=context, timeout=15
        ) as server:
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(
            settings.smtp_host, settings.smtp_port, timeout=15
        ) as server:
            if settings.smtp_use_tls:
                server.starttls(context=context)
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)


async def send_password_reset_email(to_email: str, full_name: str, code: str) -> bool:
    """
    Envoie le code OTP par email.
    - Mode dev (SMTP_HOST vide) : affiche le code dans les logs.
    - Mode prod : envoi réel via Brevo SMTP.
    Retourne toujours True ou False sans lever d'exception.
    """
    if not settings.smtp_host:
        print(
            f"\n📧 [MODE DEV — aucun SMTP configuré]\n"
            f"   Destinataire : {to_email}\n"
            f"   Code de réinitialisation : {code}\n"
            f"   (valable 5 minutes)\n"
        )
        return True

    msg = _build_password_reset_message(to_email, full_name, code)
    try:
        await asyncio.to_thread(_send_smtp_blocking, msg)
        return True
    except Exception as e:
        print(f"❌ Échec envoi email à {to_email} : {e}")
        return False
