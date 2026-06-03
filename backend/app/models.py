import enum
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, Column, DateTime, Enum, Float,
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
