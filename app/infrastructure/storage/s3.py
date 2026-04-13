"""
app/infrastructure/storage/s3.py — Service d'upload vers AWS S3.

Utilisé pour stocker les photos de profil des membres.
Les images sont uploadées dans le bucket configuré et une URL publique est retournée.

Configuration nécessaire dans .env :
  AWS_ACCESS_KEY_ID=...
  AWS_SECRET_ACCESS_KEY=...
  AWS_S3_BUCKET_NAME=...
  AWS_S3_REGION=eu-west-3          # région de votre bucket
  AWS_S3_ENDPOINT_URL=             # optionnel : pour MinIO ou S3-compatible
"""

import io
import uuid
from typing import BinaryIO

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from app.config import settings
from app.shared.exceptions import BusinessRuleError


# Extensions d'image autorisées
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
# Taille max : 5 Mo
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024

# Content-types autorisés
ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
}


def _get_s3_client():
    """
    Crée un client boto3 S3.
    Utilise les credentials AWS depuis les variables d'environnement.
    """
    kwargs = {
        "aws_access_key_id": settings.aws_access_key_id,
        "aws_secret_access_key": settings.aws_secret_access_key,
        "region_name": settings.aws_s3_region,
    }
    # Support optionnel d'un endpoint custom (MinIO, LocalStack, etc.)
    if settings.aws_s3_endpoint_url:
        kwargs["endpoint_url"] = settings.aws_s3_endpoint_url

    return boto3.client("s3", **kwargs)


def _validate_image(file_content: bytes, content_type: str, filename: str) -> str:
    """
    Valide que le fichier est bien une image autorisée.
    Retourne l'extension normalisée.
    Lève BusinessRuleError si invalide.
    """
    if len(file_content) > MAX_FILE_SIZE_BYTES:
        raise BusinessRuleError(
            f"La photo ne doit pas dépasser {MAX_FILE_SIZE_BYTES // (1024 * 1024)} Mo"
        )

    if content_type not in ALLOWED_CONTENT_TYPES:
        raise BusinessRuleError(
            f"Format d'image non autorisé. Formats acceptés : JPG, PNG, WebP"
        )

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise BusinessRuleError(
            f"Extension de fichier non autorisée. Extensions acceptées : {', '.join(ALLOWED_EXTENSIONS)}"
        )

    return ext


async def upload_profile_picture(
    member_id: str,
    file_content: bytes,
    content_type: str,
    filename: str,
) -> str:
    """
    Upload une photo de profil vers AWS S3.

    Args:
        member_id    : ID du membre (pour nommer le fichier de façon unique)
        file_content : contenu binaire de l'image
        content_type : MIME type (ex: "image/jpeg")
        filename     : nom original du fichier

    Returns:
        URL publique de l'image dans S3

    Raises:
        BusinessRuleError si le fichier est invalide ou si l'upload échoue
    """
    ext = _validate_image(file_content, content_type, filename)

    # Nom de fichier unique : profiles/member_id/uuid.ext
    # → Évite les conflits et les problèmes de cache navigateur
    s3_key = f"profiles/{member_id}/{uuid.uuid4()}.{ext}"

    try:
        client = _get_s3_client()
        client.put_object(
            Bucket=settings.aws_s3_bucket_name,
            Key=s3_key,
            Body=file_content,
            ContentType=content_type,
            Metadata={
                "member_id": str(member_id),
                "original_filename": filename,
            },
        )
    except NoCredentialsError:
        raise BusinessRuleError(
            "Configuration AWS manquante. Contactez l'administrateur système."
        )
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        raise BusinessRuleError(
            f"Erreur lors de l'upload de la photo ({error_code}). Réessayez."
        )

    # Construction de l'URL publique
    if settings.aws_s3_endpoint_url:
        # Pour MinIO ou endpoint custom
        url = f"{settings.aws_s3_endpoint_url}/{settings.aws_s3_bucket_name}/{s3_key}"
    else:
        # URL S3 standard AWS
        url = f"https://{settings.aws_s3_bucket_name}.s3.{settings.aws_s3_region}.amazonaws.com/{s3_key}"

    return url


async def delete_profile_picture(url: str) -> None:
    """
    Supprime une ancienne photo de profil de S3.
    Utilisé quand un membre remplace sa photo existante.
    Les erreurs sont silencieuses (le fichier a peut-être déjà été supprimé).
    """
    if not url:
        return

    try:
        # Extraire la clé S3 depuis l'URL
        bucket = settings.aws_s3_bucket_name
        if bucket in url:
            # Format : https://bucket.s3.region.amazonaws.com/profiles/...
            key = url.split(f"{bucket}/")[-1] if f"{bucket}/" in url else None
            if key:
                client = _get_s3_client()
                client.delete_object(Bucket=bucket, Key=key)
    except Exception:
        # Silencieux : si la suppression échoue, ce n'est pas bloquant
        pass