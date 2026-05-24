import enum

class RatingEnum(str, enum.Enum):
    safe = "safe"
    questionable = "questionable"
    explicit = "explicit"

class TagCategoryEnum(str, enum.Enum):
    general = "general"
    artist = "artist"
    character = "character"
    copyright = "copyright"
    meta = "meta"

class FileTypeEnum(str, enum.Enum):
    image = "image"
    video = "video"
    gif = "gif"

class ContentClassEnum(str, enum.Enum):
    anime = "anime"
    illustration = "illustration"
    non_anime = "non_anime"
    unknown = "unknown"

class EntityTypeEnum(str, enum.Enum):
    character = "character"
    work = "work"
    artist = "artist"
    circle = "circle"
    source = "source"
    franchise = "franchise"
    unknown = "unknown"

class EntityStatusEnum(str, enum.Enum):
    active = "active"
    deprecated = "deprecated"
    merged = "merged"
    blocked = "blocked"

class EntityAliasTypeEnum(str, enum.Enum):
    original = "original"
    romanized = "romanized"
    translated = "translated"
    common = "common"
    search = "search"
    external = "external"

class EntityMetadataSourceEnum(str, enum.Enum):
    manual = "manual"
    tag = "tag"
    external = "external"
    llm_suggestion = "llm_suggestion"
    system = "system"
    trusted_external = "trusted_external"
    imported = "imported"

class EntityExternalIdentityStatusEnum(str, enum.Enum):
    candidate = "candidate"
    verified = "verified"
    rejected = "rejected"
    stale = "stale"

class EntityEvidenceTypeEnum(str, enum.Enum):
    tag_signal = "tag_signal"
    filename_signal = "filename_signal"
    manual = "manual"
    external_lookup = "external_lookup"
    reverse_search = "reverse_search"
    user_confirmation = "user_confirmation"

class EntityCandidateStatusEnum(str, enum.Enum):
    suggested = "suggested"
    accepted = "accepted"
    rejected = "rejected"
    superseded = "superseded"
    expired = "expired"

class EntityCandidateGeneratorEnum(str, enum.Enum):
    internal_tag = "internal_tag"
    ai_tag = "ai_tag"
    filename = "filename"
    external = "external"
    manual = "manual"

class MediaEntityRoleEnum(str, enum.Enum):
    character = "character"
    work = "work"
    artist = "artist"
    source = "source"
    depicted = "depicted"
    primary = "primary"
    secondary = "secondary"

class EntityReviewStatusEnum(str, enum.Enum):
    confirmed = "confirmed"
    rejected = "rejected"
    needs_review = "needs_review"
    machine_suggested = "machine_suggested"

class EntityTranslationStatusEnum(str, enum.Enum):
    confirmed = "confirmed"
    needs_review = "needs_review"
    rejected = "rejected"
