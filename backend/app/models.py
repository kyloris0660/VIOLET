import enum
from datetime import datetime, timezone

from sqlalchemy import (JSON, BigInteger, Boolean, Column, DateTime, Enum, Float,
                        ForeignKey, Index, Integer, String, Table, Text,
                        UniqueConstraint)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base
from .enums import (
    ContentClassEnum,
    EntityAliasTypeEnum,
    EntityCandidateGeneratorEnum,
    EntityCandidateStatusEnum,
    EntityEvidenceTypeEnum,
    EntityExternalIdentityStatusEnum,
    EntityMetadataSourceEnum,
    EntityReviewStatusEnum,
    EntityStatusEnum,
    EntityTranslationStatusEnum,
    EntityTypeEnum,
    FileTypeEnum,
    MediaEntityRoleEnum,
    RatingEnum,
    TagCategoryEnum,
)

blombooru_media_tags = Table(
    'blombooru_media_tags',
    Base.metadata,
    Column('media_id', Integer, ForeignKey('blombooru_media.id', ondelete='CASCADE'), primary_key=True),
    Column('tag_id', Integer, ForeignKey('blombooru_tags.id', ondelete='CASCADE'), primary_key=True),
    Column('source', String(50), nullable=False, server_default='manual'),
    Column('confidence', Float, nullable=True),
    Column('is_locked', Boolean, nullable=False, server_default='true'),
    Column('is_suggestion', Boolean, nullable=False, server_default='false'),
    Column('created_at', DateTime(timezone=True), server_default=func.now()),
    Column('updated_at', DateTime(timezone=True), server_default=func.now()),
)

class User(Base):
    __tablename__ = 'blombooru_users'
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Media(Base):
    __tablename__ = 'blombooru_media'
    
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    path = Column(String(500), nullable=False, unique=True)
    thumbnail_path = Column(String(500))
    hash = Column(String(64), unique=True, index=True)
    file_type = Column(Enum(FileTypeEnum), nullable=False)
    mime_type = Column(String(100))
    file_size = Column(Integer)
    width = Column(Integer)
    height = Column(Integer)
    duration = Column(Float, nullable=True)
    rating = Column(Enum(RatingEnum), default=RatingEnum.safe, index=True)
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    is_shared = Column(Boolean, default=False, index=True)
    share_uuid = Column(String(36), unique=True, nullable=True, index=True)
    share_ai_metadata = Column(Boolean, default=False)
    share_language = Column(String(10), nullable=True, default=None)
    source = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    parent_id = Column(Integer, ForeignKey('blombooru_media.id', ondelete='SET NULL'), nullable=True, index=True)

    content_class = Column(Enum(ContentClassEnum), nullable=True, index=True)
    content_class_confidence = Column(Float, nullable=True)
    content_class_source = Column(String(50), nullable=True)
    content_class_model = Column(String(100), nullable=True)
    content_class_locked = Column(Boolean, nullable=False, server_default='false')
    content_class_reviewed = Column(Boolean, nullable=False, server_default='false')
    
    tags = relationship('Tag', secondary=blombooru_media_tags, back_populates='media')
    parent = relationship('Media', remote_side=[id], backref='children')
    entity_candidates = relationship(
        'MediaEntityCandidate',
        back_populates='media',
        cascade='all, delete-orphan',
    )
    entity_assignments = relationship(
        'MediaEntityAssignment',
        back_populates='media',
        cascade='all, delete-orphan',
    )

    @property
    def has_children(self) -> bool:
        return bool(self.children)

class Tag(Base):
    __tablename__ = 'blombooru_tags'
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    category = Column(Enum(TagCategoryEnum), default=TagCategoryEnum.general, index=True)
    post_count = Column(Integer, default=0, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    media = relationship('Media', secondary=blombooru_media_tags, back_populates='tags')
    aliases = relationship('TagAlias', foreign_keys='TagAlias.target_tag_id', back_populates='target_tag', cascade="all, delete-orphan")

class BooruConfig(Base):
    __tablename__ = "blombooru_booru_config"

    domain = Column(String, primary_key=True, index=True)
    username = Column(String, nullable=True)
    api_key = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class TagAlias(Base):
    __tablename__ = 'blombooru_tag_aliases'
    
    id = Column(Integer, primary_key=True, index=True)
    alias_name = Column(String(255), unique=True, nullable=False, index=True)
    target_tag_id = Column(Integer, ForeignKey('blombooru_tags.id', ondelete='CASCADE'), nullable=False)
    
    target_tag = relationship('Tag', foreign_keys=[target_tag_id], back_populates='aliases')

# Tag implication junction tables
blombooru_implication_targets = Table(
    'blombooru_implication_targets',
    Base.metadata,
    Column('implication_id', Integer, ForeignKey('blombooru_tag_implications.id', ondelete='CASCADE'), primary_key=True),
    Column('tag_id', Integer, ForeignKey('blombooru_tags.id', ondelete='CASCADE'), primary_key=True)
)

blombooru_implication_implied = Table(
    'blombooru_implication_implied',
    Base.metadata,
    Column('implication_id', Integer, ForeignKey('blombooru_tag_implications.id', ondelete='CASCADE'), primary_key=True),
    Column('tag_id', Integer, ForeignKey('blombooru_tags.id', ondelete='CASCADE'), primary_key=True)
)

class TagImplication(Base):
    __tablename__ = 'blombooru_tag_implications'

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    target_tags = relationship('Tag', secondary=blombooru_implication_targets)
    implied_tags = relationship('Tag', secondary=blombooru_implication_implied)

# Album-Media association table
blombooru_album_media = Table(
    'blombooru_album_media',
    Base.metadata,
    Column('album_id', Integer, ForeignKey('blombooru_albums.id', ondelete='CASCADE'), primary_key=True),
    Column('media_id', Integer, ForeignKey('blombooru_media.id', ondelete='CASCADE'), primary_key=True),
    Column('added_at', DateTime(timezone=True), server_default=func.now(), index=True)
)

# Album hierarchy (self-referential many-to-many for parent-child relationships)
blombooru_album_hierarchy = Table(
    'blombooru_album_hierarchy',
    Base.metadata,
    Column('parent_album_id', Integer, ForeignKey('blombooru_albums.id', ondelete='CASCADE'), primary_key=True),
    Column('child_album_id', Integer, ForeignKey('blombooru_albums.id', ondelete='CASCADE'), primary_key=True)
)

class Album(Base):
    __tablename__ = 'blombooru_albums'
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_modified = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    
    # Relationships
    media = relationship('Media', secondary=blombooru_album_media, back_populates='albums')
    
    # Self-referential relationships for album hierarchy
    children = relationship(
        'Album',
        secondary=blombooru_album_hierarchy,
        primaryjoin=id == blombooru_album_hierarchy.c.parent_album_id,
        secondaryjoin=id == blombooru_album_hierarchy.c.child_album_id,
        backref='parents'
    )

# Update Media model to include albums relationship
Media.albums = relationship('Album', secondary=blombooru_album_media, back_populates='media')

class ApiKey(Base):
    __tablename__ = 'blombooru_api_keys'
    
    id = Column(Integer, primary_key=True, index=True)
    key_hash = Column(String(64), unique=True, nullable=False, index=True)
    key_prefix = Column(String(12), nullable=False)
    name = Column(String(255), nullable=True)
    user_id = Column(Integer, ForeignKey('blombooru_users.id', ondelete='CASCADE'), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    
    user = relationship('User', backref='api_keys')


class ScanJob(Base):
    __tablename__ = 'blombooru_scan_jobs'

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String(20), nullable=False, default='pending', index=True)
    paths_json = Column(Text, nullable=False)
    dry_run = Column(Boolean, default=False)
    max_files = Column(Integer, nullable=True)

    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    total_seen = Column(Integer, default=0)
    processed = Column(Integer, default=0)
    imported = Column(Integer, default=0)
    skipped_duplicate = Column(Integer, default=0)
    skipped_unsupported = Column(Integer, default=0)
    skipped_limit = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    limit_reached = Column(Boolean, default=False)

    skipped_cloud_placeholder = Column(Integer, default=0)
    skipped_zero_byte = Column(Integer, default=0)
    skipped_timeout = Column(Integer, default=0)
    skipped_unreadable = Column(Integer, default=0)
    skipped_hidden = Column(Integer, default=0)
    skipped_too_large = Column(Integer, default=0)

    hydrated_only = Column(Boolean, default=True)
    is_preflight = Column(Boolean, default=False)

    failed_files_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)


class TagTranslation(Base):
    __tablename__ = 'blombooru_tag_translations'
    __table_args__ = (
        UniqueConstraint('canonical_name', 'language', name='uq_tag_translation_canonical_lang'),
    )

    id = Column(Integer, primary_key=True, index=True)
    tag_id = Column(Integer, ForeignKey('blombooru_tags.id', ondelete='CASCADE'), nullable=True, index=True)
    canonical_name = Column(String(255), nullable=False, index=True)
    language = Column(String(10), nullable=False, default='zh-CN', index=True)
    display_name = Column(String(500), nullable=False)
    aliases_json = Column(Text, nullable=True)
    category = Column(String(50), nullable=True)
    source = Column(String(50), nullable=False, default='static')
    status = Column(String(50), nullable=False, default='translated')
    confidence = Column(Float, nullable=True)
    needs_review = Column(Boolean, nullable=False, default=False)
    provider = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tag = relationship('Tag', foreign_keys=[tag_id], backref='translations')


class Entity(Base):
    __tablename__ = 'blombooru_entities'
    __table_args__ = (
        UniqueConstraint('type', 'normalized_key', name='uq_entity_type_normalized_key'),
        Index('ix_blombooru_entities_type_status', 'type', 'status'),
    )

    id = Column(Integer, primary_key=True, index=True)
    type = Column(Enum(EntityTypeEnum, native_enum=False), nullable=False, index=True)
    canonical_name = Column(String(500), nullable=False)
    normalized_key = Column(String(500), nullable=False, index=True)
    slug = Column(String(500), nullable=True, index=True)
    status = Column(
        Enum(EntityStatusEnum, native_enum=False),
        nullable=False,
        default=EntityStatusEnum.active,
        server_default='active',
        index=True,
    )
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    aliases = relationship(
        'EntityAlias',
        back_populates='entity',
        cascade='all, delete-orphan',
        passive_deletes=True,
    )
    external_identities = relationship(
        'EntityExternalIdentity',
        back_populates='entity',
        cascade='all, delete-orphan',
        passive_deletes=True,
    )
    translations = relationship(
        'EntityTranslation',
        back_populates='entity',
        cascade='all, delete-orphan',
        passive_deletes=True,
    )
    candidates = relationship('MediaEntityCandidate', back_populates='entity', passive_deletes=True)
    assignments = relationship(
        'MediaEntityAssignment',
        back_populates='entity',
        cascade='all, delete',
        passive_deletes=True,
    )
    evidence = relationship('EntityEvidence', back_populates='entity', passive_deletes=True)


class EntityAlias(Base):
    __tablename__ = 'blombooru_entity_aliases'
    __table_args__ = (
        UniqueConstraint('entity_id', 'normalized_alias', name='uq_entity_alias_entity_normalized'),
        Index('ix_blombooru_entity_aliases_normalized_alias', 'normalized_alias'),
    )

    id = Column(Integer, primary_key=True, index=True)
    entity_id = Column(Integer, ForeignKey('blombooru_entities.id', ondelete='CASCADE'), nullable=False, index=True)
    alias = Column(String(500), nullable=False)
    normalized_alias = Column(String(500), nullable=False)
    language = Column(String(20), nullable=True, index=True)
    alias_type = Column(
        Enum(EntityAliasTypeEnum, native_enum=False),
        nullable=False,
        default=EntityAliasTypeEnum.search,
        server_default='search',
    )
    source = Column(
        Enum(EntityMetadataSourceEnum, native_enum=False),
        nullable=False,
        default=EntityMetadataSourceEnum.manual,
        server_default='manual',
        index=True,
    )
    confidence = Column(Float, nullable=True)
    is_primary = Column(Boolean, nullable=False, default=False, server_default='false')
    needs_review = Column(Boolean, nullable=False, default=False, server_default='false', index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    entity = relationship('Entity', back_populates='aliases')


class EntityExternalIdentity(Base):
    __tablename__ = 'blombooru_entity_external_identities'
    __table_args__ = (
        UniqueConstraint('provider', 'external_id', name='uq_entity_external_provider_id'),
        Index('ix_blombooru_entity_external_entity_provider', 'entity_id', 'provider'),
    )

    id = Column(Integer, primary_key=True, index=True)
    entity_id = Column(Integer, ForeignKey('blombooru_entities.id', ondelete='CASCADE'), nullable=False, index=True)
    provider = Column(String(100), nullable=False, index=True)
    external_id = Column(String(255), nullable=False)
    external_url = Column(String(1000), nullable=True)
    identity_status = Column(
        Enum(EntityExternalIdentityStatusEnum, native_enum=False),
        nullable=False,
        default=EntityExternalIdentityStatusEnum.candidate,
        server_default='candidate',
        index=True,
    )
    confidence = Column(Float, nullable=True)
    last_verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    entity = relationship('Entity', back_populates='external_identities')


class EntityEvidence(Base):
    __tablename__ = 'blombooru_entity_evidence'
    __table_args__ = (
        Index('ix_blombooru_entity_evidence_source_evidence_type', 'source_type', 'evidence_type'),
        Index('ix_blombooru_entity_evidence_provider_query', 'provider', 'query_hash'),
        Index('ix_blombooru_entity_evidence_media_type', 'media_id', 'evidence_type'),
    )

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(100), nullable=True, index=True)
    source_type = Column(String(100), nullable=False, default='manual', server_default='manual', index=True)
    evidence_type = Column(
        Enum(EntityEvidenceTypeEnum, native_enum=False),
        nullable=False,
        default=EntityEvidenceTypeEnum.manual,
        server_default='manual',
        index=True,
    )
    media_id = Column(Integer, ForeignKey('blombooru_media.id', ondelete='SET NULL'), nullable=True, index=True)
    tag_id = Column(Integer, ForeignKey('blombooru_tags.id', ondelete='SET NULL'), nullable=True, index=True)
    entity_id = Column(Integer, ForeignKey('blombooru_entities.id', ondelete='SET NULL'), nullable=True, index=True)
    query_hash = Column(String(128), nullable=True, index=True)
    payload_ref = Column(String(500), nullable=True)
    score = Column(Float, nullable=True)
    summary = Column(Text, nullable=True)
    privacy_redacted = Column(Boolean, nullable=False, default=True, server_default='true')
    observed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    media = relationship('Media')
    tag = relationship('Tag')
    entity = relationship('Entity', back_populates='evidence')


class MediaEntityCandidate(Base):
    __tablename__ = 'blombooru_media_entity_candidates'
    __table_args__ = (
        Index('ix_blombooru_media_entity_candidates_media_status', 'media_id', 'status'),
        Index('ix_blombooru_media_entity_candidates_entity_type_status', 'entity_type', 'status'),
    )

    id = Column(Integer, primary_key=True, index=True)
    media_id = Column(Integer, ForeignKey('blombooru_media.id', ondelete='CASCADE'), nullable=False, index=True)
    entity_id = Column(Integer, ForeignKey('blombooru_entities.id', ondelete='SET NULL'), nullable=True, index=True)
    entity_type = Column(Enum(EntityTypeEnum, native_enum=False), nullable=False, index=True)
    label = Column(String(500), nullable=True)
    candidate_name = Column(String(500), nullable=False)
    score = Column(Float, nullable=True)
    status = Column(
        Enum(EntityCandidateStatusEnum, native_enum=False),
        nullable=False,
        default=EntityCandidateStatusEnum.suggested,
        server_default='suggested',
        index=True,
    )
    generator = Column(
        Enum(EntityCandidateGeneratorEnum, native_enum=False),
        nullable=False,
        default=EntityCandidateGeneratorEnum.manual,
        server_default='manual',
        index=True,
    )
    evidence_id = Column(Integer, ForeignKey('blombooru_entity_evidence.id', ondelete='SET NULL'), nullable=True, index=True)
    review_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    media = relationship('Media', back_populates='entity_candidates')
    entity = relationship('Entity', back_populates='candidates')
    evidence = relationship('EntityEvidence')


class MediaEntityAssignment(Base):
    __tablename__ = 'blombooru_media_entity_assignments'
    __table_args__ = (
        UniqueConstraint('media_id', 'entity_id', 'role', name='uq_media_entity_assignment_role'),
        Index('ix_blombooru_media_entity_assignments_media_review', 'media_id', 'review_status'),
        Index('ix_blombooru_media_entity_assignments_entity_role', 'entity_id', 'role'),
    )

    id = Column(Integer, primary_key=True, index=True)
    media_id = Column(Integer, ForeignKey('blombooru_media.id', ondelete='CASCADE'), nullable=False, index=True)
    entity_id = Column(Integer, ForeignKey('blombooru_entities.id', ondelete='CASCADE'), nullable=False, index=True)
    role = Column(Enum(MediaEntityRoleEnum, native_enum=False), nullable=False, index=True)
    confidence = Column(Float, nullable=True)
    review_status = Column(
        Enum(EntityReviewStatusEnum, native_enum=False),
        nullable=False,
        default=EntityReviewStatusEnum.needs_review,
        server_default='needs_review',
        index=True,
    )
    source = Column(
        Enum(EntityMetadataSourceEnum, native_enum=False),
        nullable=False,
        default=EntityMetadataSourceEnum.manual,
        server_default='manual',
        index=True,
    )
    locked = Column(Boolean, nullable=False, default=False, server_default='false')
    created_from_candidate_id = Column(
        Integer,
        ForeignKey('blombooru_media_entity_candidates.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
    )
    evidence_id = Column(Integer, ForeignKey('blombooru_entity_evidence.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    media = relationship('Media', back_populates='entity_assignments')
    entity = relationship('Entity', back_populates='assignments')
    created_from_candidate = relationship('MediaEntityCandidate')
    evidence = relationship('EntityEvidence')


class EntityTranslation(Base):
    __tablename__ = 'blombooru_entity_translations'
    __table_args__ = (
        UniqueConstraint('entity_id', 'language', 'display_name', name='uq_entity_translation_display'),
        Index('ix_blombooru_entity_translations_language_status', 'language', 'status'),
    )

    id = Column(Integer, primary_key=True, index=True)
    entity_id = Column(Integer, ForeignKey('blombooru_entities.id', ondelete='CASCADE'), nullable=False, index=True)
    language = Column(String(20), nullable=False, default='zh-CN', server_default='zh-CN', index=True)
    display_name = Column(String(500), nullable=False)
    source = Column(
        Enum(EntityMetadataSourceEnum, native_enum=False),
        nullable=False,
        default=EntityMetadataSourceEnum.manual,
        server_default='manual',
        index=True,
    )
    status = Column(
        Enum(EntityTranslationStatusEnum, native_enum=False),
        nullable=False,
        default=EntityTranslationStatusEnum.needs_review,
        server_default='needs_review',
        index=True,
    )
    is_primary = Column(Boolean, nullable=False, default=False, server_default='false')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    entity = relationship('Entity', back_populates='translations')


class ExternalSource(Base):
    __tablename__ = 'blombooru_external_sources'
    __table_args__ = (
        UniqueConstraint('provider', name='uq_external_sources_provider'),
        Index('ix_blombooru_external_sources_enabled', 'enabled'),
    )

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(100), nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=False, server_default='false')
    auth_mode = Column(String(50), nullable=False, default='none', server_default='none')
    base_url = Column(String(1000), nullable=True)
    rate_limit_policy = Column(JSON, nullable=True)
    privacy_policy = Column(JSON, nullable=True)
    terms_url = Column(String(1000), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProviderCache(Base):
    __tablename__ = 'blombooru_provider_cache'
    __table_args__ = (
        UniqueConstraint('provider', 'query_hash', 'query_type', name='uq_provider_cache_query'),
        Index('ix_blombooru_provider_cache_provider_fetched', 'provider', 'fetched_at'),
    )

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(100), nullable=False, index=True)
    query_hash = Column(String(128), nullable=False, index=True)
    query_type = Column(String(100), nullable=False, index=True)
    request_shape_redacted = Column(JSON, nullable=True)
    response_status = Column(String(100), nullable=False)
    response_json_redacted = Column(JSON, nullable=True)
    error_class = Column(String(100), nullable=True, index=True)
    fetched_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ExternalTagCategoryLookupCache(Base):
    __tablename__ = 'blombooru_external_tag_category_lookup_cache'
    __table_args__ = (
        UniqueConstraint('lookup_source', 'normalized_tag', name='uq_external_tag_category_lookup_key'),
        UniqueConstraint('lookup_source', 'canonical_lookup_key', name='uq_external_tag_category_canonical_lookup_key'),
        Index('ix_external_tag_category_lookup_source_status', 'lookup_source', 'status'),
        Index('ix_external_tag_category_lookup_source_canonical', 'lookup_source', 'canonical_lookup_key'),
        Index('ix_external_tag_category_lookup_source_tag_id', 'lookup_source', 'source_tag_id'),
        Index('ix_external_tag_category_lookup_namespace', 'mapped_candidate_namespace'),
        Index('ix_external_tag_category_lookup_checked', 'last_checked_at'),
    )

    id = Column(Integer, primary_key=True, index=True)
    raw_tag = Column(String(500), nullable=True)
    normalized_tag = Column(String(500), nullable=False, index=True)
    canonical_lookup_key = Column(String(500), nullable=True, index=True)
    lookup_source = Column(String(100), nullable=False, index=True)
    lookup_source_version = Column(String(100), nullable=True)
    source_tag_id = Column(String(255), nullable=True, index=True)
    source_tag_name = Column(String(500), nullable=True)
    source_category_raw = Column(String(100), nullable=True)
    mapped_candidate_namespace = Column(String(50), nullable=True, index=True)
    confidence = Column(Float, nullable=True)
    provenance_url_or_key = Column(String(1000), nullable=True)
    status = Column(String(50), nullable=False, default='pending', server_default='pending', index=True)
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_checked_at = Column(DateTime(timezone=True), nullable=True, index=True)
    retry_after = Column(DateTime(timezone=True), nullable=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    lookup_error = Column(Text, nullable=True)
    manual_override_status = Column(String(50), nullable=False, default='none', server_default='none')
    manual_override_value = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PixivTagTaxonomyKnowledgeBase(Base):
    __tablename__ = 'blombooru_pixiv_tag_taxonomy_kb'
    __table_args__ = (
        UniqueConstraint('source_scope', 'canonical_key', name='uq_pixiv_tag_taxonomy_scope_key'),
        Index('ix_pixiv_tag_taxonomy_status_namespace', 'status', 'candidate_namespace'),
        Index('ix_pixiv_tag_taxonomy_canonical_key', 'canonical_key'),
        Index('ix_pixiv_tag_taxonomy_updated', 'updated_at'),
    )

    id = Column(Integer, primary_key=True, index=True)
    raw_tag = Column(String(500), nullable=True)
    normalized_tag = Column(String(500), nullable=False, index=True)
    canonical_key = Column(String(500), nullable=False, index=True)
    source_scope = Column(String(100), nullable=False, default='pixiv_raw_tag_v1', server_default='pixiv_raw_tag_v1')
    language_script_hints = Column(JSON, nullable=True)
    candidate_namespace = Column(String(50), nullable=False, default='unknown', server_default='unknown', index=True)
    confidence = Column(Float, nullable=True)
    status = Column(String(50), nullable=False, default='unresolved', server_default='unresolved', index=True)
    source_summary = Column(JSON, nullable=True)
    frequency = Column(Integer, nullable=False, default=0, server_default='0')
    high_value_score = Column(Float, nullable=True)
    unresolved_reason = Column(String(100), nullable=True, index=True)
    next_action = Column(String(255), nullable=True)
    manual_override_status = Column(String(50), nullable=False, default='none', server_default='none')
    manual_override_value = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PixivTagAliasKnowledgeBase(Base):
    __tablename__ = 'blombooru_pixiv_tag_alias_kb'
    __table_args__ = (
        UniqueConstraint(
            'source_canonical_key',
            'target_canonical_key',
            'relation_type',
            'evidence_source',
            name='uq_pixiv_tag_alias_relation_evidence',
        ),
        Index('ix_pixiv_tag_alias_relation_status', 'relation_type', 'status'),
        Index('ix_pixiv_tag_alias_source_key', 'source_canonical_key'),
        Index('ix_pixiv_tag_alias_target_key', 'target_canonical_key'),
    )

    id = Column(Integer, primary_key=True, index=True)
    source_tag = Column(String(500), nullable=False)
    source_canonical_key = Column(String(500), nullable=False, index=True)
    target_tag = Column(String(500), nullable=False)
    target_canonical_key = Column(String(500), nullable=False, index=True)
    relation_type = Column(String(100), nullable=False, index=True)
    evidence_source = Column(String(100), nullable=False, index=True)
    evidence_payload = Column(JSON, nullable=True)
    confidence = Column(Float, nullable=True)
    status = Column(String(50), nullable=False, default='candidate', server_default='candidate', index=True)
    frequency = Column(Integer, nullable=False, default=0, server_default='0')
    manual_override_status = Column(String(50), nullable=False, default='none', server_default='none')
    manual_override_value = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceMetadataRecord(Base):
    __tablename__ = 'blombooru_source_metadata_records'
    __table_args__ = (
        UniqueConstraint('provider', 'provider_record_key', name='uq_source_metadata_provider_record_key'),
        Index('ix_source_metadata_provider_status', 'provider', 'status'),
        Index('ix_source_metadata_media_provider', 'media_id', 'provider'),
        Index('ix_source_metadata_work_page', 'source_work_id', 'source_page_index'),
    )

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(100), nullable=False, index=True)
    provider_run_id = Column(String(255), nullable=True, index=True)
    run_label = Column(String(255), nullable=True)
    provider_record_key = Column(String(500), nullable=False, index=True)
    media_id = Column(Integer, nullable=True, index=True)
    source_work_id = Column(String(255), nullable=True, index=True)
    source_page_index = Column(Integer, nullable=True)
    source_url = Column(String(1000), nullable=True)
    title = Column(String(1000), nullable=True)
    artist_name = Column(String(500), nullable=True)
    artist_id = Column(String(255), nullable=True)
    confidence = Column(Float, nullable=True)
    similarity = Column(Float, nullable=True)
    metadata_kind = Column(String(100), nullable=False, default='provider_metadata', server_default='provider_metadata', index=True)
    data_type_label = Column(String(100), nullable=False, default='fixture_or_mock', server_default='fixture_or_mock', index=True)
    raw_metadata_json = Column(JSON, nullable=True)
    provenance = Column(JSON, nullable=True)
    status = Column(String(50), nullable=False, default='observed', server_default='observed', index=True)
    retrieved_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceTagObservation(Base):
    __tablename__ = 'blombooru_source_tag_observations'
    __table_args__ = (
        UniqueConstraint(
            'source_metadata_record_id',
            'observation_key',
            name='uq_source_tag_observation_record_key',
        ),
        Index('ix_source_tag_observation_provider_kind', 'provider', 'source_tag_kind'),
        Index('ix_source_tag_observation_canonical', 'canonical_tag_key'),
    )

    id = Column(Integer, primary_key=True, index=True)
    source_metadata_record_id = Column(Integer, ForeignKey('blombooru_source_metadata_records.id', ondelete='CASCADE'), nullable=False, index=True)
    provider = Column(String(100), nullable=False, index=True)
    observation_key = Column(String(500), nullable=False, index=True)
    raw_tag = Column(String(500), nullable=False)
    normalized_tag = Column(String(500), nullable=False, index=True)
    canonical_tag_key = Column(String(500), nullable=False, index=True)
    source_tag_kind = Column(String(100), nullable=False, default='provider_tag', server_default='provider_tag', index=True)
    source_category_raw = Column(String(100), nullable=True)
    language_hint = Column(String(50), nullable=True)
    confidence = Column(Float, nullable=True)
    order_index = Column(Integer, nullable=True)
    taxonomy_kb_id = Column(Integer, nullable=True, index=True)
    status = Column(String(50), nullable=False, default='observed', server_default='observed', index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceTagRegistry(Base):
    __tablename__ = 'blombooru_source_tag_registry'
    __table_args__ = (
        UniqueConstraint('provider_scope', 'canonical_tag_key', name='uq_source_tag_registry_scope_key'),
        Index('ix_source_tag_registry_governance', 'governance_status'),
        Index('ix_source_tag_registry_taxonomy', 'taxonomy_status'),
    )

    id = Column(Integer, primary_key=True, index=True)
    provider_scope = Column(String(100), nullable=False, default='global', server_default='global', index=True)
    normalized_tag = Column(String(500), nullable=False)
    canonical_tag_key = Column(String(500), nullable=False, index=True)
    raw_variants_json = Column(JSON, nullable=True)
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), nullable=True, index=True)
    seen_count = Column(Integer, nullable=False, default=0, server_default='0')
    example_source_metadata_id = Column(Integer, nullable=True, index=True)
    taxonomy_status = Column(String(50), nullable=False, default='unclassified', server_default='unclassified', index=True)
    governance_status = Column(String(50), nullable=False, default='candidate', server_default='candidate', index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceNameObservation(Base):
    __tablename__ = 'blombooru_source_name_observations'
    __table_args__ = (
        UniqueConstraint(
            'source_metadata_record_id',
            'observation_key',
            name='uq_source_name_observation_record_key',
        ),
        Index('ix_source_name_observation_provider_role', 'provider', 'name_role'),
        Index('ix_source_name_observation_canonical', 'canonical_name_key'),
        Index('ix_source_name_observation_media_role', 'media_id', 'name_role'),
    )

    id = Column(Integer, primary_key=True, index=True)
    source_metadata_record_id = Column(Integer, ForeignKey('blombooru_source_metadata_records.id', ondelete='CASCADE'), nullable=False, index=True)
    provider = Column(String(100), nullable=False, index=True)
    observation_key = Column(String(500), nullable=False, index=True)
    media_id = Column(Integer, nullable=True, index=True)
    source_work_id = Column(String(255), nullable=True, index=True)
    source_page_index = Column(Integer, nullable=True)
    raw_name = Column(String(500), nullable=False)
    normalized_name = Column(String(500), nullable=False, index=True)
    canonical_name_key = Column(String(500), nullable=False, index=True)
    name_role = Column(String(100), nullable=False, index=True)
    source_field = Column(String(100), nullable=False, index=True)
    language_hint = Column(String(50), nullable=True)
    script_hint = Column(String(50), nullable=True)
    confidence = Column(Float, nullable=True)
    provenance = Column(JSON, nullable=True)
    requires_review = Column(Boolean, nullable=False, default=True, server_default='true', index=True)
    status = Column(String(50), nullable=False, default='observed', server_default='observed', index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceNameRegistry(Base):
    __tablename__ = 'blombooru_source_name_registry'
    __table_args__ = (
        UniqueConstraint('canonical_name_key', name='uq_source_name_registry_key'),
        Index('ix_source_name_registry_governance', 'governance_status'),
        Index('ix_source_name_registry_manual_override', 'manual_override_status'),
    )

    id = Column(Integer, primary_key=True, index=True)
    canonical_name_key = Column(String(500), nullable=False, index=True)
    primary_display_name = Column(String(500), nullable=False)
    normalized_display_name = Column(String(500), nullable=False, index=True)
    raw_variants_json = Column(JSON, nullable=True)
    provider_coverage_json = Column(JSON, nullable=True)
    role_distribution_json = Column(JSON, nullable=True)
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), nullable=True, index=True)
    seen_count = Column(Integer, nullable=False, default=0, server_default='0')
    governance_status = Column(String(50), nullable=False, default='candidate', server_default='candidate', index=True)
    manual_override_status = Column(String(50), nullable=False, default='none', server_default='none', index=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceNameAliasCandidate(Base):
    __tablename__ = 'blombooru_source_name_alias_candidates'
    __table_args__ = (
        UniqueConstraint(
            'source_name_key',
            'target_name_key',
            'relation_type',
            'evidence_source',
            name='uq_source_name_alias_relation_evidence',
        ),
        Index('ix_source_name_alias_source_key', 'source_name_key'),
        Index('ix_source_name_alias_target_key', 'target_name_key'),
        Index('ix_source_name_alias_relation_status', 'relation_type', 'status'),
    )

    id = Column(Integer, primary_key=True, index=True)
    source_name_key = Column(String(500), nullable=False, index=True)
    target_name_key = Column(String(500), nullable=False, index=True)
    source_display_name = Column(String(500), nullable=False)
    target_display_name = Column(String(500), nullable=False)
    relation_type = Column(String(100), nullable=False, index=True)
    evidence_source = Column(String(100), nullable=False, index=True)
    evidence_payload = Column(JSON, nullable=True)
    confidence = Column(Float, nullable=True)
    status = Column(String(50), nullable=False, default='candidate', server_default='candidate', index=True)
    requires_review = Column(Boolean, nullable=False, default=True, server_default='true', index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceMetadataEvidence(Base):
    __tablename__ = 'blombooru_source_metadata_evidence'
    __table_args__ = (
        UniqueConstraint('source_metadata_record_id', 'evidence_key', name='uq_source_metadata_evidence_record_key'),
        Index('ix_source_metadata_evidence_kind_status', 'evidence_kind', 'status'),
        Index('ix_source_metadata_evidence_observation', 'observation_type', 'observation_id'),
    )

    id = Column(Integer, primary_key=True, index=True)
    source_metadata_record_id = Column(Integer, ForeignKey('blombooru_source_metadata_records.id', ondelete='CASCADE'), nullable=False, index=True)
    evidence_key = Column(String(500), nullable=False, index=True)
    observation_type = Column(String(100), nullable=False, index=True)
    observation_id = Column(Integer, nullable=True, index=True)
    evidence_kind = Column(String(100), nullable=False, index=True)
    evidence_strength = Column(String(50), nullable=False, default='unknown', server_default='unknown', index=True)
    provenance = Column(JSON, nullable=True)
    status = Column(String(50), nullable=False, default='staged', server_default='staged', index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceSearchableNameAssertion(Base):
    __tablename__ = 'blombooru_source_searchable_name_assertions'
    __table_args__ = (
        UniqueConstraint('assertion_key', name='uq_source_searchable_name_assertion_key'),
        Index('ix_source_searchable_name_assertion_provider_status', 'provider', 'status'),
        Index('ix_source_searchable_name_assertion_canonical_status', 'canonical_name_key', 'status'),
        Index('ix_source_searchable_name_assertion_role_status', 'asserted_role', 'status'),
        Index('ix_source_searchable_name_assertion_tag_observation', 'source_tag_observation_id'),
        Index('ix_source_searchable_name_assertion_name_observation', 'source_name_observation_id'),
    )

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(100), nullable=False, index=True)
    source_metadata_record_id = Column(Integer, ForeignKey('blombooru_source_metadata_records.id', ondelete='CASCADE'), nullable=True, index=True)
    source_tag_observation_id = Column(Integer, ForeignKey('blombooru_source_tag_observations.id', ondelete='SET NULL'), nullable=True, index=True)
    source_name_observation_id = Column(Integer, ForeignKey('blombooru_source_name_observations.id', ondelete='SET NULL'), nullable=True, index=True)
    assertion_key = Column(String(700), nullable=False, index=True)
    raw_input = Column(String(500), nullable=False)
    normalized_input = Column(String(500), nullable=False, index=True)
    canonical_name_key = Column(String(500), nullable=False, index=True)
    asserted_name = Column(String(500), nullable=True)
    asserted_role = Column(String(100), nullable=False, index=True)
    status = Column(String(50), nullable=False, default='needs_review', server_default='needs_review', index=True)
    confidence = Column(String(50), nullable=False, default='low', server_default='low', index=True)
    confidence_score = Column(Float, nullable=True)
    evidence_sources_json = Column(JSON, nullable=True)
    model_name = Column(String(255), nullable=True)
    prompt_version = Column(String(100), nullable=True)
    structured_output_schema_version = Column(String(100), nullable=False)
    reasoning_summary_private = Column(Text, nullable=True)
    provenance_summary = Column(JSON, nullable=True)
    requires_review = Column(Boolean, nullable=False, default=True, server_default='true', index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceNameCandidateExtractionRun(Base):
    __tablename__ = 'blombooru_source_name_candidate_extraction_runs'
    __table_args__ = (
        UniqueConstraint('run_id', name='uq_source_name_candidate_run_id'),
        Index('ix_source_name_candidate_extraction_run_status', 'status'),
        Index('ix_source_name_candidate_extraction_run_mode', 'mode'),
    )

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(255), nullable=False, index=True)
    run_label = Column(String(255), nullable=True)
    extractor_version = Column(String(100), nullable=False)
    prompt_version = Column(String(100), nullable=True)
    structured_output_schema_version = Column(String(100), nullable=False)
    mode = Column(String(100), nullable=False, default='dry_run', server_default='dry_run', index=True)
    status = Column(String(50), nullable=False, default='running', server_default='running', index=True)
    input_scope_json = Column(JSON, nullable=True)
    summary_json = Column(JSON, nullable=True)
    provider_summary_json = Column(JSON, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True, index=True)
    finished_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceNameCandidateRecordVerdict(Base):
    __tablename__ = 'blombooru_source_name_candidate_record_verdicts'
    __table_args__ = (
        UniqueConstraint(
            'extraction_run_id',
            'group_key',
            name='uq_source_name_candidate_record_verdict_run_group',
        ),
        Index('ix_source_name_candidate_record_verdict_provider', 'provider'),
        Index('ix_source_name_candidate_record_verdict_verdict', 'extraction_verdict'),
        Index('ix_source_name_candidate_record_verdict_source_record', 'source_metadata_record_id'),
        Index('ix_source_name_candidate_record_verdict_media', 'media_id'),
    )

    id = Column(Integer, primary_key=True, index=True)
    extraction_run_id = Column(
        Integer,
        ForeignKey('blombooru_source_name_candidate_extraction_runs.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    source_metadata_record_id = Column(Integer, ForeignKey('blombooru_source_metadata_records.id', ondelete='SET NULL'), nullable=True, index=True)
    media_id = Column(Integer, ForeignKey('blombooru_media.id', ondelete='SET NULL'), nullable=True, index=True)
    provider = Column(String(100), nullable=False, index=True)
    group_key = Column(String(700), nullable=False, index=True)
    extraction_verdict = Column(String(100), nullable=False, index=True)
    verdict_reason = Column(Text, nullable=True)
    no_name_reason = Column(String(255), nullable=True, index=True)
    candidate_count = Column(Integer, nullable=False, default=0, server_default='0')
    rejected_count = Column(Integer, nullable=False, default=0, server_default='0')
    meta_count = Column(Integer, nullable=False, default=0, server_default='0')
    ambiguous_count = Column(Integer, nullable=False, default=0, server_default='0')
    confidence_summary = Column(JSON, nullable=True)
    extraction_warnings_json = Column(JSON, nullable=True)
    evidence_payload = Column(JSON, nullable=True)
    status = Column(String(50), nullable=False, default='observed', server_default='observed', index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceNameCandidate(Base):
    __tablename__ = 'blombooru_source_name_candidates'
    __table_args__ = (
        UniqueConstraint('candidate_key', name='uq_source_name_candidate_key'),
        Index('ix_source_name_candidate_run_candidate_status', 'extraction_run_id', 'candidate_status'),
        Index('ix_source_name_candidate_provider_role', 'provider', 'candidate_role'),
        Index('ix_source_name_candidate_canonical_status', 'canonical_key', 'candidate_status'),
        Index('ix_source_name_candidate_origin', 'origin_type', 'origin_id'),
        Index('ix_source_name_candidate_source_record', 'source_metadata_record_id'),
        Index('ix_source_name_candidate_media', 'media_id'),
    )

    id = Column(Integer, primary_key=True, index=True)
    extraction_run_id = Column(
        Integer,
        ForeignKey('blombooru_source_name_candidate_extraction_runs.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
    )
    record_verdict_id = Column(
        Integer,
        ForeignKey('blombooru_source_name_candidate_record_verdicts.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
    )
    source_metadata_record_id = Column(Integer, ForeignKey('blombooru_source_metadata_records.id', ondelete='SET NULL'), nullable=True, index=True)
    media_id = Column(Integer, ForeignKey('blombooru_media.id', ondelete='SET NULL'), nullable=True, index=True)
    provider = Column(String(100), nullable=False, index=True)
    group_key = Column(String(700), nullable=False, index=True)
    candidate_key = Column(String(900), nullable=False, index=True)
    origin_type = Column(String(100), nullable=False, index=True)
    origin_id = Column(String(500), nullable=True, index=True)
    raw_value = Column(String(500), nullable=False)
    display_name = Column(String(500), nullable=False)
    normalized_value = Column(String(500), nullable=False, index=True)
    canonical_key = Column(String(500), nullable=False, index=True)
    candidate_role = Column(String(100), nullable=False, index=True)
    candidate_status = Column(String(50), nullable=False, default='active_candidate', server_default='active_candidate', index=True)
    extraction_verdict = Column(String(100), nullable=False, index=True)
    language_hint = Column(String(50), nullable=True, index=True)
    script_hint = Column(String(50), nullable=True, index=True)
    work_context = Column(String(500), nullable=True)
    work_context_key = Column(String(500), nullable=True, index=True)
    parenthetical_base = Column(String(500), nullable=True)
    parenthetical_context = Column(String(500), nullable=True)
    extraction_action = Column(String(100), nullable=False, index=True)
    confidence = Column(Float, nullable=True)
    reason = Column(Text, nullable=True)
    rejection_reason = Column(String(255), nullable=True, index=True)
    no_name_reason = Column(String(255), nullable=True, index=True)
    evidence_payload = Column(JSON, nullable=True)
    extractor_version = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False, default='active', server_default='active', index=True)
    superseded_by_candidate_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceConceptResolutionRun(Base):
    __tablename__ = 'blombooru_source_concept_resolution_runs'
    __table_args__ = (
        UniqueConstraint('run_id', name='uq_source_concept_resolution_run_id'),
        Index('ix_source_concept_resolution_run_status', 'status'),
        Index('ix_source_concept_resolution_run_scope', 'scope'),
    )

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(255), nullable=False, index=True)
    run_label = Column(String(255), nullable=True)
    scope = Column(String(100), nullable=False, default='source_concept_core', server_default='source_concept_core', index=True)
    resolver_version = Column(String(100), nullable=False)
    mode = Column(String(100), nullable=False, default='dry_run', server_default='dry_run', index=True)
    status = Column(String(50), nullable=False, default='running', server_default='running', index=True)
    input_signal_counts_json = Column(JSON, nullable=True)
    linked_counts_json = Column(JSON, nullable=True)
    concept_counts_json = Column(JSON, nullable=True)
    review_counts_json = Column(JSON, nullable=True)
    no_truth_write_proof_json = Column(JSON, nullable=True)
    summary_json = Column(JSON, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True, index=True)
    finished_at = Column(DateTime(timezone=True), nullable=True, index=True)
    runtime_seconds = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceConceptSignal(Base):
    __tablename__ = 'blombooru_source_concept_signals'
    __table_args__ = (
        UniqueConstraint('signal_key', name='uq_source_concept_signal_key'),
        Index('ix_source_concept_signal_origin', 'origin_type', 'origin_id'),
        Index('ix_source_concept_signal_provider_role', 'provider', 'role_hint'),
        Index('ix_source_concept_signal_status_trust', 'status', 'trust_tier'),
        Index('ix_source_concept_signal_canonical', 'canonical_key'),
        Index('ix_source_concept_signal_work_context', 'work_context_key'),
        Index('ix_source_concept_signal_media', 'media_id'),
        Index('ix_source_concept_signal_source_record', 'source_metadata_record_id'),
    )

    id = Column(Integer, primary_key=True, index=True)
    resolution_run_id = Column(
        Integer,
        ForeignKey('blombooru_source_concept_resolution_runs.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
    )
    signal_key = Column(String(900), nullable=False, index=True)
    origin_type = Column(String(100), nullable=False, index=True)
    origin_table = Column(String(255), nullable=True)
    origin_id = Column(String(500), nullable=True, index=True)
    provider = Column(String(100), nullable=True, index=True)
    media_id = Column(Integer, ForeignKey('blombooru_media.id', ondelete='SET NULL'), nullable=True, index=True)
    source_metadata_record_id = Column(Integer, ForeignKey('blombooru_source_metadata_records.id', ondelete='SET NULL'), nullable=True, index=True)
    source_record_id = Column(String(500), nullable=True, index=True)
    raw_value = Column(String(1000), nullable=False)
    display_value = Column(String(1000), nullable=False)
    normalized_key = Column(String(500), nullable=False, index=True)
    canonical_key = Column(String(500), nullable=True, index=True)
    role_hint = Column(String(100), nullable=False, default='unknown', server_default='unknown', index=True)
    work_context_key = Column(String(500), nullable=True, index=True)
    parenthetical_base = Column(String(500), nullable=True)
    parenthetical_context = Column(String(500), nullable=True)
    source_kind = Column(String(100), nullable=True, index=True)
    trust_tier = Column(String(50), nullable=False, default='weak', server_default='weak', index=True)
    confidence = Column(Float, nullable=True)
    status = Column(String(50), nullable=False, default='needs_review', server_default='needs_review', index=True)
    evidence_payload = Column(JSON, nullable=True)
    source_run_id = Column(String(255), nullable=True, index=True)
    created_by_run_id = Column(String(255), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceConcept(Base):
    __tablename__ = 'blombooru_source_concepts'
    __table_args__ = (
        UniqueConstraint('concept_key', name='uq_source_concept_key'),
        Index('ix_source_concept_type_status', 'concept_type_hint', 'status'),
        Index('ix_source_concept_status_confidence', 'status', 'confidence_score'),
        Index('ix_source_concept_superseded_by', 'superseded_by_concept_id'),
    )

    id = Column(Integer, primary_key=True, index=True)
    concept_key = Column(String(900), nullable=False, index=True)
    primary_display_name = Column(String(1000), nullable=False)
    concept_type_hint = Column(String(100), nullable=False, default='unknown', server_default='unknown', index=True)
    status = Column(String(50), nullable=False, default='needs_review', server_default='needs_review', index=True)
    confidence_score = Column(Float, nullable=True)
    evidence_score = Column(Float, nullable=True)
    media_count = Column(Integer, nullable=False, default=0, server_default='0')
    source_count = Column(Integer, nullable=False, default=0, server_default='0')
    created_by_run_id = Column(String(255), nullable=True, index=True)
    superseded_by_concept_id = Column(Integer, ForeignKey('blombooru_source_concepts.id', ondelete='SET NULL'), nullable=True, index=True)
    evidence_summary_json = Column(JSON, nullable=True)
    lifecycle_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceConceptAlias(Base):
    __tablename__ = 'blombooru_source_concept_aliases'
    __table_args__ = (
        UniqueConstraint('concept_id', 'alias_key', 'alias_role', name='uq_source_concept_alias_concept_key_role'),
        Index('ix_source_concept_alias_lookup', 'alias_key', 'status'),
        Index('ix_source_concept_alias_role_status', 'alias_role', 'status'),
        Index('ix_source_concept_alias_signal', 'source_signal_id'),
    )

    id = Column(Integer, primary_key=True, index=True)
    concept_id = Column(Integer, ForeignKey('blombooru_source_concepts.id', ondelete='CASCADE'), nullable=False, index=True)
    alias_value = Column(String(1000), nullable=False)
    alias_key = Column(String(500), nullable=False, index=True)
    display_name = Column(String(1000), nullable=False)
    language_hint = Column(String(50), nullable=True, index=True)
    script_hint = Column(String(50), nullable=True, index=True)
    alias_role = Column(String(100), nullable=False, index=True)
    status = Column(String(50), nullable=False, default='needs_review', server_default='needs_review', index=True)
    confidence = Column(Float, nullable=True)
    source_signal_id = Column(Integer, ForeignKey('blombooru_source_concept_signals.id', ondelete='SET NULL'), nullable=True, index=True)
    evidence_payload = Column(JSON, nullable=True)
    created_by_run_id = Column(String(255), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceConceptEvidence(Base):
    __tablename__ = 'blombooru_source_concept_evidence'
    __table_args__ = (
        UniqueConstraint('concept_id', 'signal_id', 'evidence_type', name='uq_source_concept_evidence_concept_signal_type'),
        Index('ix_source_concept_evidence_provider_type', 'provider', 'evidence_type'),
        Index('ix_source_concept_evidence_status_strength', 'status', 'evidence_strength'),
        Index('ix_source_concept_evidence_media', 'media_id'),
        Index('ix_source_concept_evidence_source_record', 'source_metadata_record_id'),
    )

    id = Column(Integer, primary_key=True, index=True)
    concept_id = Column(Integer, ForeignKey('blombooru_source_concepts.id', ondelete='CASCADE'), nullable=False, index=True)
    signal_id = Column(Integer, ForeignKey('blombooru_source_concept_signals.id', ondelete='SET NULL'), nullable=True, index=True)
    media_id = Column(Integer, ForeignKey('blombooru_media.id', ondelete='SET NULL'), nullable=True, index=True)
    source_metadata_record_id = Column(Integer, ForeignKey('blombooru_source_metadata_records.id', ondelete='SET NULL'), nullable=True, index=True)
    provider = Column(String(100), nullable=True, index=True)
    evidence_type = Column(String(100), nullable=False, index=True)
    evidence_strength = Column(String(50), nullable=False, default='weak', server_default='weak', index=True)
    payload = Column(JSON, nullable=True)
    run_id = Column(String(255), nullable=True, index=True)
    status = Column(String(50), nullable=False, default='needs_review', server_default='needs_review', index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceConceptSignalLink(Base):
    __tablename__ = 'blombooru_source_concept_signal_links'
    __table_args__ = (
        UniqueConstraint('signal_id', 'concept_id', 'run_id', name='uq_source_concept_signal_link_run'),
        Index('ix_source_concept_signal_link_status', 'link_status'),
        Index('ix_source_concept_signal_link_reason', 'resolution_reason_code'),
    )

    id = Column(Integer, primary_key=True, index=True)
    signal_id = Column(Integer, ForeignKey('blombooru_source_concept_signals.id', ondelete='CASCADE'), nullable=False, index=True)
    concept_id = Column(Integer, ForeignKey('blombooru_source_concepts.id', ondelete='CASCADE'), nullable=False, index=True)
    link_status = Column(String(50), nullable=False, default='needs_review', server_default='needs_review', index=True)
    confidence = Column(Float, nullable=True)
    resolution_reason_code = Column(String(100), nullable=True, index=True)
    negative_reason_code = Column(String(100), nullable=True, index=True)
    resolver_version = Column(String(100), nullable=False)
    run_id = Column(String(255), nullable=False, index=True)
    evidence_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceConceptSearchIndex(Base):
    __tablename__ = 'blombooru_source_concept_search_index'
    __table_args__ = (
        UniqueConstraint('concept_id', 'search_key', 'alias_role', name='uq_source_concept_search_index_key_role'),
        Index('ix_source_concept_search_lookup', 'search_key', 'status'),
        Index('ix_source_concept_search_weight', 'weight'),
    )

    id = Column(Integer, primary_key=True, index=True)
    concept_id = Column(Integer, ForeignKey('blombooru_source_concepts.id', ondelete='CASCADE'), nullable=False, index=True)
    search_key = Column(String(500), nullable=False, index=True)
    display_name = Column(String(1000), nullable=False)
    alias_role = Column(String(100), nullable=False, index=True)
    weight = Column(Float, nullable=False, default=0.0, server_default='0')
    status = Column(String(50), nullable=False, default='needs_review', server_default='needs_review', index=True)
    evidence_refs_json = Column(JSON, nullable=True)
    run_id = Column(String(255), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SourceConceptFallbackSearchIndex(Base):
    """Versioned source-layer lookup for non-materialized evidence fallback.

    Rows are retrieval evidence only. They never authorize an identity union or
    write Entity truth.
    """

    __tablename__ = 'blombooru_source_concept_fallback_search_index'
    __table_args__ = (
        UniqueConstraint(
            'alias_key',
            'media_id',
            'source_signal_id',
            'neighbor_signal_id',
            'pair_id',
            'overlay_version',
            name='uq_source_concept_fallback_search_row',
        ),
        Index(
            'ix_source_concept_fallback_search_lookup',
            'alias_key',
            'status',
            'overlay_version',
        ),
        Index('ix_source_concept_fallback_search_pair', 'pair_id', 'relation'),
        Index('ix_source_concept_fallback_search_media', 'media_id'),
    )

    id = Column(Integer, primary_key=True, index=True)
    alias_key = Column(String(500), nullable=False, index=True)
    media_id = Column(Integer, ForeignKey('blombooru_media.id', ondelete='CASCADE'), nullable=True, index=True)
    source_signal_id = Column(
        Integer,
        ForeignKey('blombooru_source_concept_signals.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    neighbor_signal_id = Column(
        Integer,
        ForeignKey('blombooru_source_concept_signals.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    pair_id = Column(String(64), nullable=False, index=True)
    relation = Column(String(50), nullable=False, index=True)
    overlay_version = Column(String(100), nullable=False, index=True)
    disposition_version = Column(String(100), nullable=False)
    role_hint = Column(String(100), nullable=True)
    work_context_key = Column(String(500), nullable=True)
    provenance_payload = Column(JSON, nullable=True)
    status = Column(String(50), nullable=False, default='active', server_default='active', index=True)
    run_id = Column(String(255), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class NegativeLookupCache(Base):
    __tablename__ = 'blombooru_negative_lookup_cache'
    __table_args__ = (
        UniqueConstraint('provider', 'query_hash', 'query_type', name='uq_negative_lookup_cache_query'),
    )

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(100), nullable=False, index=True)
    query_hash = Column(String(128), nullable=False, index=True)
    query_type = Column(String(100), nullable=False, index=True)
    reason = Column(String(255), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DynamicSourceRoot(Base):
    __tablename__ = 'blombooru_dynamic_source_roots'
    __table_args__ = (
        UniqueConstraint('root_path_hash', name='uq_dynamic_source_root_path_hash'),
        Index('ix_dynamic_source_roots_active', 'is_active'),
    )

    id = Column(Integer, primary_key=True, index=True)
    label = Column(String(255), nullable=False)
    root_path = Column(String(2000), nullable=False)
    root_path_hash = Column(String(128), nullable=False, index=True)
    source_type = Column(String(50), nullable=False, default='local_path', server_default='local_path')
    is_active = Column(Boolean, nullable=False, default=True, server_default='true')
    auto_sync_enabled = Column(Boolean, nullable=False, default=False, server_default='false')
    sync_threshold = Column(Integer, nullable=False, default=100, server_default='100')
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_checked_at = Column(DateTime(timezone=True), nullable=True)


class DynamicSyncRun(Base):
    __tablename__ = 'blombooru_dynamic_sync_runs'
    __table_args__ = (
        Index('ix_dynamic_sync_runs_status_created', 'status', 'created_at'),
        Index('ix_dynamic_sync_runs_mode_status', 'mode', 'status'),
    )

    id = Column(Integer, primary_key=True, index=True)
    run_type = Column(String(50), nullable=False, default='check', server_default='check')
    mode = Column(String(50), nullable=False, default='dry_run', server_default='dry_run')
    status = Column(String(50), nullable=False, default='running', server_default='running', index=True)
    dry_run = Column(Boolean, nullable=False, default=True, server_default='true')
    threshold = Column(Integer, nullable=False, default=100, server_default='100')
    threshold_reached = Column(Boolean, nullable=False, default=False, server_default='false')
    roots_checked = Column(Integer, nullable=False, default=0, server_default='0')
    total_seen = Column(Integer, nullable=False, default=0, server_default='0')
    new_items = Column(Integer, nullable=False, default=0, server_default='0')
    changed_items = Column(Integer, nullable=False, default=0, server_default='0')
    unchanged_items = Column(Integer, nullable=False, default=0, server_default='0')
    deferred_items = Column(Integer, nullable=False, default=0, server_default='0')
    failed_items = Column(Integer, nullable=False, default=0, server_default='0')
    missing_items = Column(Integer, nullable=False, default=0, server_default='0')
    pending_import_items = Column(Integer, nullable=False, default=0, server_default='0')
    summary_json = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)


class DynamicSourceItem(Base):
    __tablename__ = 'blombooru_dynamic_source_items'
    __table_args__ = (
        UniqueConstraint('source_root_id', 'relative_path_hash', name='uq_dynamic_source_item_root_relhash'),
        Index('ix_dynamic_source_items_root_relhash', 'source_root_id', 'relative_path_hash'),
        Index('ix_dynamic_source_items_content_hash', 'content_hash'),
        Index('ix_dynamic_source_items_import_status', 'import_status'),
        Index('ix_dynamic_source_items_classification_status', 'classification_status'),
        Index('ix_dynamic_source_items_ai_tagging_status', 'ai_tagging_status'),
        Index('ix_dynamic_source_items_localization_status', 'localization_status'),
        Index('ix_dynamic_source_items_last_seen_run', 'last_seen_run_id'),
        Index('ix_dynamic_source_items_media_id', 'media_id'),
    )

    id = Column(Integer, primary_key=True, index=True)
    source_root_id = Column(Integer, ForeignKey('blombooru_dynamic_source_roots.id', ondelete='CASCADE'), nullable=False, index=True)
    relative_path = Column(String(2000), nullable=False)
    relative_path_hash = Column(String(128), nullable=False, index=True)
    file_size = Column(BigInteger, nullable=True)
    mtime = Column(Float, nullable=True)
    mtime_ns = Column(BigInteger, nullable=True)
    content_hash = Column(String(128), nullable=True, index=True)
    media_id = Column(Integer, ForeignKey('blombooru_media.id', ondelete='SET NULL'), nullable=True, index=True)
    source_status = Column(String(50), nullable=False, default='available', server_default='available', index=True)
    sync_state = Column(String(50), nullable=False, default='new', server_default='new', index=True)
    import_status = Column(String(50), nullable=False, default='pending', server_default='pending', index=True)
    classification_status = Column(String(50), nullable=False, default='waiting_import', server_default='waiting_import', index=True)
    ai_tagging_status = Column(String(50), nullable=False, default='waiting_import', server_default='waiting_import', index=True)
    localization_status = Column(String(50), nullable=False, default='waiting_ai_tags', server_default='waiting_ai_tags', index=True)
    failure_reason = Column(String(255), nullable=True, index=True)
    deferred_reason = Column(String(255), nullable=True, index=True)
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_checked_at = Column(DateTime(timezone=True), server_default=func.now())
    last_imported_at = Column(DateTime(timezone=True), nullable=True)
    last_sync_run_id = Column(Integer, ForeignKey('blombooru_dynamic_sync_runs.id', ondelete='SET NULL'), nullable=True, index=True)
    last_seen_run_id = Column(Integer, ForeignKey('blombooru_dynamic_sync_runs.id', ondelete='SET NULL'), nullable=True, index=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    source_root = relationship('DynamicSourceRoot', backref='source_items')
    media = relationship('Media')
    last_sync_run = relationship('DynamicSyncRun', foreign_keys=[last_sync_run_id])
    last_seen_run = relationship('DynamicSyncRun', foreign_keys=[last_seen_run_id])


class DynamicSyncRunItem(Base):
    __tablename__ = 'blombooru_dynamic_sync_run_items'
    __table_args__ = (
        UniqueConstraint('sync_run_id', 'source_item_id', name='uq_dynamic_sync_run_item'),
        Index('ix_dynamic_sync_run_items_run_state', 'sync_run_id', 'item_state'),
        Index('ix_dynamic_sync_run_items_item', 'source_item_id'),
        Index('ix_dynamic_sync_run_items_import_eligible', 'eligible_for_db_import'),
    )

    id = Column(Integer, primary_key=True, index=True)
    sync_run_id = Column(Integer, ForeignKey('blombooru_dynamic_sync_runs.id', ondelete='CASCADE'), nullable=False, index=True)
    source_item_id = Column(Integer, ForeignKey('blombooru_dynamic_source_items.id', ondelete='CASCADE'), nullable=False, index=True)
    item_state = Column(String(50), nullable=False, index=True)
    action = Column(String(50), nullable=False, default='record_only', server_default='record_only')
    reason = Column(String(255), nullable=True)
    eligible_for_db_import = Column(Boolean, nullable=False, default=False, server_default='false', index=True)
    bytes_copied = Column(BigInteger, nullable=False, default=0, server_default='0')
    media_id = Column(Integer, ForeignKey('blombooru_media.id', ondelete='SET NULL'), nullable=True, index=True)
    previous_metadata_json = Column(JSON, nullable=True)
    current_metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    sync_run = relationship('DynamicSyncRun', backref='run_items')
    source_item = relationship('DynamicSourceItem', backref='run_items')
    media = relationship('Media')


class ScanJobMedia(Base):
    __tablename__ = 'blombooru_scan_job_media'

    id = Column(Integer, primary_key=True, index=True)
    scan_job_id = Column(Integer, ForeignKey('blombooru_scan_jobs.id', ondelete='CASCADE'), nullable=False, index=True)
    media_id = Column(Integer, ForeignKey('blombooru_media.id', ondelete='CASCADE'), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    scan_job = relationship('ScanJob', backref='imported_media')
    media = relationship('Media')


class AITagJob(Base):
    __tablename__ = 'blombooru_ai_tag_jobs'

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String(20), nullable=False, default='pending', index=True)
    trigger_source = Column(String(20), nullable=False, default='manual')
    scan_job_id = Column(Integer, ForeignKey('blombooru_scan_jobs.id', ondelete='SET NULL'), nullable=True, index=True)
    media_ids_json = Column(Text, nullable=True)
    max_items = Column(Integer, default=10)
    dry_run = Column(Boolean, default=False)
    only_without_ai_tags = Column(Boolean, default=True)
    force_suggestions = Column(Boolean, default=False)

    processed = Column(Integer, default=0)
    tags_added = Column(Integer, default=0)
    suggestions_added = Column(Integer, default=0)
    skipped_locked = Column(Integer, default=0)
    ignored_low_confidence = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    failed_items_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    localization_status = Column(String(50), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    scan_job = relationship('ScanJob', backref='ai_tag_jobs')


class ClassificationJob(Base):
    __tablename__ = 'blombooru_classification_jobs'

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String(20), nullable=False, default='pending', index=True)
    trigger_source = Column(String(20), nullable=False, default='manual')
    scan_job_id = Column(Integer, ForeignKey('blombooru_scan_jobs.id', ondelete='SET NULL'), nullable=True, index=True)
    media_ids_json = Column(Text, nullable=True)
    max_items = Column(Integer, default=100)
    only_unclassified = Column(Boolean, default=True)
    force_reclassify = Column(Boolean, default=False)

    processed = Column(Integer, default=0)
    classified_anime = Column(Integer, default=0)
    classified_non_anime = Column(Integer, default=0)
    classified_unknown = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    failed_items_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    scan_job = relationship('ScanJob', backref='classification_jobs')


class TagTranslationJob(Base):
    __tablename__ = 'blombooru_tag_translation_jobs'

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String(20), nullable=False, default='pending', index=True)
    source = Column(String(20), nullable=False, default='background')
    language = Column(String(10), nullable=False, default='zh-CN')
    category = Column(String(50), nullable=True)
    batch_size = Column(Integer, default=100)
    max_per_run = Column(Integer, default=500)

    processed = Column(Integer, default=0)
    translated = Column(Integer, default=0)
    failed = Column(Integer, default=0)
    skipped = Column(Integer, default=0)
    remaining_before = Column(Integer, default=0)
    remaining_after = Column(Integer, nullable=True)

    last_error = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
