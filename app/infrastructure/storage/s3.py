import uuid

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
import unicodedata
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

def _sanitize_metadata(value: str) -> str:
    """Encode les caractères non-ASCII pour les métadonnées S3."""
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", errors="ignore").decode("ascii")

def _get_s3_client():
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
    if len(file_content) > MAX_FILE_SIZE_BYTES:
        raise BusinessRuleError(
            f"La photo ne doit pas dépasser {MAX_FILE_SIZE_BYTES // (1024 * 1024)} Mo"
        )

    if content_type not in ALLOWED_CONTENT_TYPES:
        raise BusinessRuleError("Format d'image non autorisé. Formats acceptés : JPG, PNG, WebP")

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
                "original_filename": _sanitize_metadata(filename),
            },
        )
    except NoCredentialsError:
        raise BusinessRuleError("Configuration AWS manquante. Contactez l'administrateur système.")
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        raise BusinessRuleError(f"Erreur lors de l'upload de la photo ({error_code}). Réessayez.")

    # Construction de l'URL publique
    if settings.aws_s3_endpoint_url:
        public_base = (
            settings.aws_s3_public_url
            or f"{settings.aws_s3_endpoint_url}/{settings.aws_s3_bucket_name}"
        )
        url = f"{public_base}/{s3_key}"
    else:
        url = f"https://{settings.aws_s3_bucket_name}.s3.{settings.aws_s3_region}.amazonaws.com/{s3_key}"

    return url


async def delete_profile_picture(url: str) -> None:
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
