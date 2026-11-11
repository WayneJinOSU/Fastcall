from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base


Base = declarative_base()


class RobotAppModel(Base):
    __tablename__ = "robot_app"

    id = Column(String(40), primary_key=True)
    app_code = Column(String(128), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    enabled = Column(Boolean(), nullable=False)
    draft_json = Column(JSON(), nullable=False)
    draft_revision = Column(BigInteger(), nullable=False)
    created_at = Column(DateTime(6), nullable=False)
    updated_at = Column(DateTime(6), nullable=False)


class RobotAppVersionModel(Base):
    __tablename__ = "robot_app_version"
    __table_args__ = (
        UniqueConstraint("app_id", "version_no", name="uq_robot_version_no"),
        UniqueConstraint("app_id", "config_hash", name="uq_robot_version_hash"),
        Index("ix_robot_version_draft", "app_id", "source_draft_revision"),
        Index("ix_robot_version_config_hash", "config_hash"),
    )

    id = Column(String(40), primary_key=True)
    app_id = Column(String(40), ForeignKey("robot_app.id"), nullable=False)
    version_no = Column(BigInteger(), nullable=False)
    source_draft_revision = Column(BigInteger(), nullable=False)
    content_json = Column(JSON(), nullable=False)
    config_hash = Column(String(71), nullable=False)
    schema_version = Column(String(32), nullable=False)
    runtime_engine = Column(String(32), nullable=False)
    runtime_adapter = Column(String(64), nullable=False)
    runtime_catalog_version = Column(String(32), nullable=False)
    validation_report_json = Column(JSON(), nullable=False)
    validated_at = Column(DateTime(6), nullable=False)
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime(6), nullable=False)


class RobotReleaseModel(Base):
    __tablename__ = "robot_release"

    id = Column(String(40), primary_key=True)
    app_id = Column(String(40), ForeignKey("robot_app.id"), nullable=False, unique=True)
    robot_app_version_id = Column(
        String(40), ForeignKey("robot_app_version.id"), nullable=False
    )
    updated_by = Column(String(128), nullable=False)
    updated_at = Column(DateTime(6), nullable=False)


class RobotConfigAuditLogModel(Base):
    __tablename__ = "robot_config_audit_log"

    id = Column(String(40), primary_key=True)
    actor = Column(String(128), nullable=False)
    action = Column(String(64), nullable=False)
    object_type = Column(String(64), nullable=False)
    object_id = Column(String(40), nullable=False)
    app_version_id = Column(String(40), nullable=True)
    config_hash = Column(String(71), nullable=True)
    detail_json = Column(JSON(), nullable=False)
    created_at = Column(DateTime(6), nullable=False)
