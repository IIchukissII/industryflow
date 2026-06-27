# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# src/api/models/user.py
from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import Column, String, CheckConstraint
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

    # Check constraint to validate roles
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'engineer', 'observer')", name='check_role'),
    )