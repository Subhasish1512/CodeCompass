"""Utility functions for the demo application."""

import hashlib
import json
from typing import Any, Dict, List, Optional
from datetime import datetime
from .models import User, Product, Order


def hash_password(password: str) -> str:
    """Hash a password using SHA256."""
    return hashlib.sha256(password.encode()).hexdigest()


def validate_email(email: str) -> bool:
    """Validate email format."""
    return "@" in email and "." in email.split("@")[1]


def format_user(user: User) -> Dict[str, Any]:
    """Format user for API response."""
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "is_active": user.is_active,
    }


def format_product(product: Product) -> Dict[str, Any]:
    """Format product for API response."""
    return {
        "id": product.id,
        "name": product.name,
        "description": product.description,
        "price": product.price,
        "stock": product.stock,
    }


def format_order(order: Order) -> Dict[str, Any]:
    """Format order for API response."""
    return {
        "id": order.id,
        "user_id": order.user_id,
        "items": [
            {"product_id": item.product_id, "quantity": item.quantity, "price": item.price}
            for item in order.items
        ],
        "total": order.total,
        "status": order.status,
    }


def serialize_to_json(data: Any) -> str:
    """Serialize data to JSON string."""
    return json.dumps(data, default=str)


def deserialize_from_json(json_str: str) -> Any:
    """Deserialize JSON string to data."""
    return json.loads(json_str)


# Functions demonstrating various call patterns
def process_user_data(user: User) -> Dict[str, Any]:
    """Process user data - calls format_user."""
    return format_user(user)


def process_product_data(product: Product) -> Dict[str, Any]:
    """Process product data - calls format_product."""
    return format_product(product)


def process_order_data(order: Order) -> Dict[str, Any]:
    """Process order data - calls format_order."""
    return format_order(order)


def process_all_data(users: List[User], products: List[Product], orders: List[Order]) -> Dict[str, List]:
    """Process all data - calls individual formatters."""
    return {
        "users": [process_user_data(u) for u in users],
        "products": [process_product_data(p) for p in products],
        "orders": [process_order_data(o) for o in orders],
    }


# Utility class
class DataProcessor:
    """Processes various data types."""

    def __init__(self):
        self.processed_count = 0

    def process_user(self, user: User) -> Dict[str, Any]:
        """Process a single user."""
        self.processed_count += 1
        return format_user(user)

    def process_product(self, product: Product) -> Dict[str, Any]:
        """Process a single product."""
        self.processed_count += 1
        return format_product(product)

    def process_order(self, order: Order) -> Dict[str, Any]:
        """Process a single order."""
        self.processed_count += 1
        return format_order(order)

    def process_batch(self, items: List[Any]) -> List[Dict[str, Any]]:
        """Process a batch of items."""
        results = []
        for item in items:
            if isinstance(item, User):
                results.append(self.process_user(item))
            elif isinstance(item, Product):
                results.append(self.process_product(item))
            elif isinstance(item, Order):
                results.append(self.process_order(item))
        return results


# Function with default arguments and type hints
def create_response(
    success: bool = True,
    data: Optional[Dict] = None,
    error: Optional[str] = None,
    status_code: int = 200
) -> Dict[str, Any]:
    """Create a standardized API response."""
    response = {
        "success": success,
        "status_code": status_code,
        "timestamp": datetime.now().isoformat(),
    }
    if data is not None:
        response["data"] = data
    if error is not None:
        response["error"] = error
    return response


# Generator function
def paginate_items(items: List[Any], page_size: int = 10):
    """Paginate a list of items."""
    for i in range(0, len(items), page_size):
        yield items[i:i + page_size]


# Context manager example
class Transaction:
    """Database transaction context manager."""

    def __init__(self):
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        print("Transaction started")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()

    def commit(self):
        """Commit the transaction."""
        self.committed = True
        print("Transaction committed")

    def rollback(self):
        """Rollback the transaction."""
        self.rolled_back = True
        print("Transaction rolled back")


# Decorator examples
def retry(max_attempts: int = 3):
    """Retry decorator."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        raise
                    print(f"Attempt {attempt + 1} failed: {e}, retrying...")
        return wrapper
    return decorator


def cache_result(func):
    """Simple caching decorator."""
    cache = {}
    def wrapper(*args, **kwargs):
        key = str(args) + str(sorted(kwargs.items()))
        if key in cache:
            print(f"Cache hit for {func.__name__}")
            return cache[key]
        result = func(*args, **kwargs)
        cache[key] = result
        return result
    return wrapper


@cache_result
def expensive_computation(x: int, y: int) -> int:
    """An expensive computation that benefits from caching."""
    return x * y + sum(range(x))


# Module-level variables for testing
DEFAULT_PAGE_SIZE = 20
MAX_RETRIES = 3
API_VERSION = "1.0.0"


# Function that calls multiple other functions
def initialize_demo_data() -> Dict[str, Any]:
    """Initialize demo data - calls multiple service functions."""
    from .services import UserService, ProductService, OrderService

    user_service = UserService()
    product_service = ProductService()
    order_service = OrderService(user_service, product_service)

    # Create sample users
    user1 = user_service.create_user("alice", "alice@example.com")
    user2 = user_service.create_user("bob", "bob@example.com")

    # Create sample products
    product1 = product_service.create_product("Laptop", "Gaming laptop", 999.99, 10)
    product2 = product_service.create_product("Mouse", "Wireless mouse", 29.99, 50)

    # Create sample order
    from .models import OrderItem
    order = order_service.create_order(user1.id, [
        OrderItem(product_id=product1.id, quantity=1, price=product1.price),
        OrderItem(product_id=product2.id, quantity=2, price=product2.price),
    ])

    return {
        "users": [user1, user2],
        "products": [product1, product2],
        "orders": [order] if order else [],
    }