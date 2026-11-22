"""Business logic services for the demo application."""

from typing import List, Optional
from .models import User, Order, OrderItem, Product


class UserService:
    """User management service."""

    def __init__(self):
        self._users: dict[int, User] = {}

    def create_user(self, username: str, email: str) -> User:
        """Create a new user."""
        user_id = len(self._users) + 1
        user = User(
            id=user_id,
            username=username,
            email=email,
            created_at=__import__('datetime').datetime.now()
        )
        self._users[user_id] = user
        return user

    def get_user(self, user_id: int) -> Optional[User]:
        """Get user by ID."""
        return self._users.get(user_id)

    def get_user_by_email(self, email: str) -> Optional[User]:
        """Get user by email."""
        for user in self._users.values():
            if user.email == email:
                return user
        return None

    def deactivate_user(self, user_id: int) -> bool:
        """Deactivate a user."""
        user = self._users.get(user_id)
        if user:
            user.is_active = False
            return True
        return False


class ProductService:
    """Product management service."""

    def __init__(self):
        self._products: dict[int, Product] = {}

    def create_product(self, name: str, description: str, price: float, stock: int) -> Product:
        """Create a new product."""
        product_id = len(self._products) + 1
        product = Product(
            id=product_id,
            name=name,
            description=description,
            price=price,
            stock=stock
        )
        self._products[product_id] = product
        return product

    def get_product(self, product_id: int) -> Optional[Product]:
        """Get product by ID."""
        return self._products.get(product_id)

    def list_products(self) -> List[Product]:
        """List all products."""
        return list(self._products.values())

    def update_stock(self, product_id: int, quantity: int) -> bool:
        """Update product stock."""
        product = self._products.get(product_id)
        if product and product.stock + quantity >= 0:
            product.stock += quantity
            return True
        return False


class OrderService:
    """Order management service."""

    def __init__(self, user_service: UserService, product_service: ProductService):
        self._user_service = user_service
        self._product_service = product_service
        self._orders: dict[int, Order] = {}

    def create_order(self, user_id: int, items: List[OrderItem]) -> Optional[Order]:
        """Create a new order."""
        user = self._user_service.get_user(user_id)
        if not user:
            return None

        # Validate stock
        for item in items:
            product = self._product_service.get_product(item.product_id)
            if not product or product.stock < item.quantity:
                return None

        # Reserve stock
        for item in items:
            self._product_service.update_stock(item.product_id, -item.quantity)

        order_id = len(self._orders) + 1
        total = sum(item.price * item.quantity for item in items)
        order = Order(
            id=order_id,
            user_id=user_id,
            items=items,
            total=total,
            status="confirmed"
        )
        self._orders[order_id] = order
        return order

    def get_order(self, order_id: int) -> Optional[Order]:
        """Get order by ID."""
        return self._orders.get(order_id)

    def cancel_order(self, order_id: int) -> bool:
        """Cancel an order and restore stock."""
        order = self._orders.get(order_id)
        if not order or order.status == "cancelled":
            return False

        # Restore stock
        for item in order.items:
            self._product_service.update_stock(item.product_id, item.quantity)

        order.status = "cancelled"
        return True