"""Data models for the demo application."""

from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime


@dataclass
class User:
    """User model."""
    id: int
    username: str
    email: str
    created_at: datetime
    is_active: bool = True


@dataclass
class Order:
    """Order model."""
    id: int
    user_id: int
    items: List["OrderItem"]
    total: float
    status: str = "pending"


@dataclass
class OrderItem:
    """Order item model."""
    product_id: int
    quantity: int
    price: float


@dataclass
class Product:
    """Product model."""
    id: int
    name: str
    description: str
    price: float
    stock: int