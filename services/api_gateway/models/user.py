# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# src/api/models/user.py
from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import Boolean, Column, String, CheckConstraint, false
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class User(SQLAlchemyBaseUserTableUUID, Base):
    """
    Extended User model with multi-tenant support

    Inherits from SQLAlchemyBaseUserTableUUID which provides:
    - id (UUID primary key)
    - email (unique)
    - hashed_password
    - is_active
    - is_superuser
    - is_verified
    """

    # Multi-tenant fields
    company_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    role = Column(
        String(50),
        nullable=False,
        default="observer"
    )

    # Until the holder sets their own password, the account reads itself, changes its password and
    # signs out, and nothing more (ADR-0004 dec 11): the first-run superuser's default password.
    password_change_required = Column(Boolean, nullable=False, default=False, server_default=false())

    # Check constraint to validate roles
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'engineer', 'observer')", name='check_role'),
    )