"""
Adaptive Schema Manager - Dynamic field registration and versioning.

Manages the data schema that flows through the system.
Learning Army (Layer 6) can request new fields here.
All changes are version controlled.
"""

import json
import hashlib
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Set
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class FieldType(Enum):
    """Types of fields in the schema."""
    BASE = "base"           # Raw from adapter (LTP, OHLCV, OI)
    COMPUTED = "computed"   # Calculated (VWAP, FVG, Greeks)
    DERIVED = "derived"     # Derived from computed (signals)


class FieldSource(Enum):
    """Source of field data."""
    FYERS = "fyers"
    NSE = "nse"
    COMPUTED = "computed"
    EXTERNAL = "external"


@dataclass
class FieldDefinition:
    """Definition of a data field."""
    name: str
    field_type: FieldType
    source: FieldSource
    description: str
    dependencies: List[str] = field(default_factory=list)
    formula: Optional[str] = None
    compute_fn: Optional[str] = None  # Function name in IndicatorRegistry
    params: Dict[str, Any] = field(default_factory=dict)
    refresh_rate: str = "per_tick"  # per_tick, per_candle, on_demand
    added_by: str = "system"  # system, learning_army, manual
    added_at: Optional[str] = None
    reason: Optional[str] = None
    version: int = 1
    enabled: bool = True

    def __post_init__(self):
        if self.added_at is None:
            self.added_at = datetime.now().isoformat()

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["field_type"] = self.field_type.value
        d["source"] = self.source.value
        return d

    @classmethod
    def from_dict(cls, data: Dict) -> "FieldDefinition":
        data = dict(data)
        data["field_type"] = FieldType(data.get("field_type", "base"))
        data["source"] = FieldSource(data.get("source", "computed"))
        return cls(**data)


@dataclass
class SchemaVersion:
    """Version of the schema."""
    version: int
    timestamp: str
    changes: List[Dict]
    hash: str

    def to_dict(self) -> Dict:
        return asdict(self)


class AdaptiveSchemaManager:
    """
    Manages dynamic data schema with version control.

    Features:
    - Base fields from adapters (OHLCV, OI, Greeks)
    - Computed fields added on-demand (VWAP, FVG)
    - Version controlled changes
    - Learning Army integration for field requests
    """

    # Base fields always available from Fyers
    BASE_FIELDS = {
        "symbol": FieldDefinition(
            name="symbol", field_type=FieldType.BASE, source=FieldSource.FYERS,
            description="Trading symbol", added_by="system"
        ),
        "ltp": FieldDefinition(
            name="ltp", field_type=FieldType.BASE, source=FieldSource.FYERS,
            description="Last traded price", added_by="system"
        ),
        "open": FieldDefinition(
            name="open", field_type=FieldType.BASE, source=FieldSource.FYERS,
            description="Open price", added_by="system"
        ),
        "high": FieldDefinition(
            name="high", field_type=FieldType.BASE, source=FieldSource.FYERS,
            description="High price", added_by="system"
        ),
        "low": FieldDefinition(
            name="low", field_type=FieldType.BASE, source=FieldSource.FYERS,
            description="Low price", added_by="system"
        ),
        "close": FieldDefinition(
            name="close", field_type=FieldType.BASE, source=FieldSource.FYERS,
            description="Close price", added_by="system"
        ),
        "volume": FieldDefinition(
            name="volume", field_type=FieldType.BASE, source=FieldSource.FYERS,
            description="Trading volume", added_by="system"
        ),
        "oi": FieldDefinition(
            name="oi", field_type=FieldType.BASE, source=FieldSource.FYERS,
            description="Open interest", added_by="system"
        ),
        "bid": FieldDefinition(
            name="bid", field_type=FieldType.BASE, source=FieldSource.FYERS,
            description="Best bid price", added_by="system"
        ),
        "ask": FieldDefinition(
            name="ask", field_type=FieldType.BASE, source=FieldSource.FYERS,
            description="Best ask price", added_by="system"
        ),
        "prev_close": FieldDefinition(
            name="prev_close", field_type=FieldType.BASE, source=FieldSource.FYERS,
            description="Previous close price", added_by="system"
        ),
    }

    def __init__(self, schema_dir: Optional[Path] = None):
        self.schema_dir = schema_dir or Path(__file__).parent / "versions"
        self.schema_dir.mkdir(parents=True, exist_ok=True)

        # Active schema
        self._fields: Dict[str, FieldDefinition] = {}
        self._versions: List[SchemaVersion] = []
        self._current_version = 0

        # Pending requests from Learning Army
        self._pending_requests: List[Dict] = []

        # Initialize with base fields
        self._initialize_base_fields()

        # Load saved schema if exists
        self._load_schema()

    def _initialize_base_fields(self):
        """Initialize with base fields from adapter."""
        for name, field_def in self.BASE_FIELDS.items():
            self._fields[name] = field_def

    def _compute_schema_hash(self) -> str:
        """Compute hash of current schema for versioning."""
        schema_data = {
            name: field.to_dict()
            for name, field in sorted(self._fields.items())
        }
        return hashlib.sha256(
            json.dumps(schema_data, sort_keys=True).encode()
        ).hexdigest()[:12]

    def _save_schema(self):
        """Save current schema to disk."""
        schema_file = self.schema_dir / "current_schema.json"
        data = {
            "version": self._current_version,
            "timestamp": datetime.now().isoformat(),
            "hash": self._compute_schema_hash(),
            "fields": {name: f.to_dict() for name, f in self._fields.items()},
            "history": [v.to_dict() for v in self._versions[-10:]],  # Keep last 10
        }
        with open(schema_file, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Schema saved: version={self._current_version}, hash={data['hash']}")

    def _load_schema(self):
        """Load schema from disk if exists."""
        schema_file = self.schema_dir / "current_schema.json"
        if not schema_file.exists():
            return

        try:
            with open(schema_file) as f:
                data = json.load(f)

            self._current_version = data.get("version", 0)

            # Load fields
            for name, field_data in data.get("fields", {}).items():
                if name not in self.BASE_FIELDS:  # Don't overwrite base fields
                    self._fields[name] = FieldDefinition.from_dict(field_data)

            # Load history
            for v_data in data.get("history", []):
                self._versions.append(SchemaVersion(**v_data))

            logger.info(f"Schema loaded: version={self._current_version}, fields={len(self._fields)}")
        except Exception as e:
            logger.error(f"Failed to load schema: {e}")

    def get_fields(self) -> Dict[str, FieldDefinition]:
        """Get all active fields."""
        return {k: v for k, v in self._fields.items() if v.enabled}

    def get_field(self, name: str) -> Optional[FieldDefinition]:
        """Get a specific field definition."""
        return self._fields.get(name)

    def has_field(self, name: str) -> bool:
        """Check if field exists and is enabled."""
        field = self._fields.get(name)
        return field is not None and field.enabled

    def get_computed_fields(self) -> Dict[str, FieldDefinition]:
        """Get only computed fields."""
        return {
            k: v for k, v in self._fields.items()
            if v.field_type == FieldType.COMPUTED and v.enabled
        }

    def get_base_fields(self) -> Dict[str, FieldDefinition]:
        """Get only base fields."""
        return {
            k: v for k, v in self._fields.items()
            if v.field_type == FieldType.BASE and v.enabled
        }

    def add_field(
        self,
        name: str,
        description: str,
        dependencies: List[str],
        compute_fn: str,
        params: Optional[Dict] = None,
        reason: str = "",
        added_by: str = "manual",
        refresh_rate: str = "per_tick",
    ) -> bool:
        """
        Add a new computed field to the schema.

        Args:
            name: Field name (e.g., "vwap")
            description: Human-readable description
            dependencies: List of fields needed to compute (e.g., ["close", "volume"])
            compute_fn: Function name in IndicatorRegistry
            params: Optional parameters for the compute function
            reason: Why this field was added
            added_by: Who added it (system, learning_army, manual)
            refresh_rate: How often to recompute

        Returns:
            True if field was added successfully
        """
        # Validate dependencies exist
        for dep in dependencies:
            if not self.has_field(dep):
                logger.error(f"Cannot add field '{name}': missing dependency '{dep}'")
                return False

        # Check if field already exists
        if name in self._fields:
            existing = self._fields[name]
            if existing.enabled:
                logger.warning(f"Field '{name}' already exists, updating...")
                existing.version += 1
                existing.description = description
                existing.dependencies = dependencies
                existing.compute_fn = compute_fn
                existing.params = params or {}
                existing.reason = reason
            else:
                existing.enabled = True
                existing.version += 1
        else:
            # Create new field
            self._fields[name] = FieldDefinition(
                name=name,
                field_type=FieldType.COMPUTED,
                source=FieldSource.COMPUTED,
                description=description,
                dependencies=dependencies,
                compute_fn=compute_fn,
                params=params or {},
                refresh_rate=refresh_rate,
                added_by=added_by,
                reason=reason,
            )

        # Version control
        self._current_version += 1
        change = {
            "action": "add_field",
            "field": name,
            "added_by": added_by,
            "reason": reason,
        }
        self._versions.append(SchemaVersion(
            version=self._current_version,
            timestamp=datetime.now().isoformat(),
            changes=[change],
            hash=self._compute_schema_hash(),
        ))

        # Save to disk
        self._save_schema()

        logger.info(f"Added field '{name}' (v{self._current_version}): {reason}")
        return True

    def remove_field(self, name: str, reason: str = "") -> bool:
        """
        Remove (disable) a computed field.

        Base fields cannot be removed.
        """
        if name in self.BASE_FIELDS:
            logger.error(f"Cannot remove base field '{name}'")
            return False

        if name not in self._fields:
            logger.warning(f"Field '{name}' does not exist")
            return False

        self._fields[name].enabled = False

        # Version control
        self._current_version += 1
        change = {
            "action": "remove_field",
            "field": name,
            "reason": reason,
        }
        self._versions.append(SchemaVersion(
            version=self._current_version,
            timestamp=datetime.now().isoformat(),
            changes=[change],
            hash=self._compute_schema_hash(),
        ))

        self._save_schema()
        logger.info(f"Removed field '{name}' (v{self._current_version}): {reason}")
        return True

    def request_field(self, request: Dict) -> str:
        """
        Accept a field request from Learning Army (Layer 6).

        Request format:
        {
            "type": "add_indicator",
            "name": "vwap",
            "description": "Volume Weighted Average Price",
            "dependencies": ["close", "volume"],
            "compute_fn": "compute_vwap",
            "params": {},
            "reason": "Improves entry accuracy by 12%",
            "confidence": 0.85,
            "requester": "QuantLearnerAgent"
        }

        Returns:
            Request ID for tracking
        """
        request_id = hashlib.sha256(
            f"{request.get('name', '')}{datetime.now().isoformat()}".encode()
        ).hexdigest()[:8]

        request["request_id"] = request_id
        request["status"] = "pending"
        request["requested_at"] = datetime.now().isoformat()

        self._pending_requests.append(request)

        logger.info(f"Field request received: {request_id} - {request.get('name')}")
        return request_id

    def approve_request(self, request_id: str) -> bool:
        """
        Approve a pending field request (typically after debate).
        """
        for req in self._pending_requests:
            if req.get("request_id") == request_id:
                success = self.add_field(
                    name=req.get("name", ""),
                    description=req.get("description", ""),
                    dependencies=req.get("dependencies", []),
                    compute_fn=req.get("compute_fn", ""),
                    params=req.get("params", {}),
                    reason=req.get("reason", ""),
                    added_by="learning_army",
                )
                if success:
                    req["status"] = "approved"
                    req["approved_at"] = datetime.now().isoformat()
                else:
                    req["status"] = "failed"
                return success

        logger.error(f"Request {request_id} not found")
        return False

    def reject_request(self, request_id: str, reason: str = "") -> bool:
        """Reject a pending field request."""
        for req in self._pending_requests:
            if req.get("request_id") == request_id:
                req["status"] = "rejected"
                req["rejected_at"] = datetime.now().isoformat()
                req["rejection_reason"] = reason
                logger.info(f"Request {request_id} rejected: {reason}")
                return True
        return False

    def get_pending_requests(self) -> List[Dict]:
        """Get all pending field requests."""
        return [r for r in self._pending_requests if r.get("status") == "pending"]

    def get_schema_version(self) -> int:
        """Get current schema version."""
        return self._current_version

    def get_schema_history(self, limit: int = 10) -> List[Dict]:
        """Get recent schema changes."""
        return [v.to_dict() for v in self._versions[-limit:]]

    def export_schema(self) -> Dict:
        """Export full schema as dict."""
        return {
            "version": self._current_version,
            "hash": self._compute_schema_hash(),
            "base_fields": list(self.BASE_FIELDS.keys()),
            "computed_fields": list(self.get_computed_fields().keys()),
            "all_fields": {name: f.to_dict() for name, f in self._fields.items()},
        }
