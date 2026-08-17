"""Compatibility imports for dependency-neutral source-safety observations.

The value types live outside both the application and phase-scoped scripts so
that ``SourceIngestionGate`` and FL1 tooling share identities without either
layer importing the other. Policy decisions remain owned by
``SourceIngestionGate``.
"""

from violet_source_safety import (
    CloudAvailability,
    FileChangeIdentity,
    FileObjectIdentity,
    HandleObservation,
    SourceDecision,
    SourceSafetyPolicy,
)

__all__ = [
    "CloudAvailability",
    "FileChangeIdentity",
    "FileObjectIdentity",
    "HandleObservation",
    "SourceDecision",
    "SourceSafetyPolicy",
]
