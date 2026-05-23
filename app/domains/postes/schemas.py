from pydantic import BaseModel, field_validator


# ─────────────────────────────────────────────────────────────────────────────
# REQUESTS
# ─────────────────────────────────────────────────────────────────────────────

class CreatePosteRequest(BaseModel):
    title: str
    member_id: str | None = None  # UUID du membre à assigner (optionnel)

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Le titre du poste doit contenir au moins 2 caractères")
        if len(v) > 100:
            raise ValueError("Le titre du poste ne peut pas dépasser 100 caractères")
        return v


class UpdatePosteRequest(BaseModel):
    title: str | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if len(v) < 2:
                raise ValueError("Le titre du poste doit contenir au moins 2 caractères")
            if len(v) > 30:
                raise ValueError("Le titre du poste ne peut pas dépasser 30 caractères")
        return v


class AssignPosteRequest(BaseModel):
    member_id: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# RESPONSES
# ─────────────────────────────────────────────────────────────────────────────

class PosteMemberResponse(BaseModel):
    id: str
    full_name: str
    phone_number: str
    avatar_url: str | None = None

    @classmethod
    def from_model(cls, member) -> "PosteMemberResponse":
        return cls(
            id=str(member.id),
            full_name=member.full_name,
            phone_number=member.phone_number,
            avatar_url=getattr(member, "avatar_url", None),
        )


class PosteResponse(BaseModel):
    id: str
    title: str
    member: PosteMemberResponse | None = None
    is_vacant: bool
    created_at: str
    updated_at: str

    @classmethod
    def from_model(cls, poste) -> "PosteResponse":
        member = None
        if poste.member:
            member = PosteMemberResponse.from_model(poste.member)

        return cls(
            id=str(poste.id),
            title=poste.title,
            member=member,
            is_vacant=poste.member_id is None,
            created_at=poste.created_at.isoformat(),
            updated_at=poste.updated_at.isoformat(),
        )


class OrgChartPostesResponse(BaseModel):
    postes: list[PosteResponse]
    total: int
    occupied: int
    vacant: int