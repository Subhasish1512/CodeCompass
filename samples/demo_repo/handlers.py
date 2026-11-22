"""Request handlers for the demo application."""

# Import aliases for testing resolution
from .services import UserService as US, ProductService as PS
from .services import OrderService
from .models import User, Product, Order, OrderItem

# Relative imports
from . import models
from .models import Order as OrderModel


class BaseHandler:
    """Base handler class."""

    def __init__(self):
        self.user_service = US()
        self.product_service = PS()
        self.order_service = OrderService(self.user_service, self.product_service)

    def handle_request(self, request: dict) -> dict:
        """Handle a generic request."""
        return {"status": "ok", "data": self.process(request)}

    def process(self, request: dict) -> dict:
        """Process the request. Override in subclasses."""
        raise NotImplementedError


class UserHandler(BaseHandler):
    """User request handler."""

    def process(self, request: dict) -> dict:
        """Process user-related requests."""
        action = request.get("action")

        if action == "create":
            return self._create_user(request)
        elif action == "get":
            return self._get_user(request)
        elif action == "deactivate":
            return self._deactivate_user(request)
        else:
            return {"error": f"Unknown action: {action}"}

    def _create_user(self, request: dict) -> dict:
        """Create a new user."""
        username = request.get("username")
        email = request.get("email")
        if not username or not email:
            return {"error": "Missing username or email"}

        user = self.user_service.create_user(username, email)
        return {"user_id": user.id, "username": user.username}

    def _get_user(self, request: dict) -> dict:
        """Get user by ID or email."""
        user_id = request.get("user_id")
        email = request.get("email")

        if user_id:
            user = self.user_service.get_user(user_id)
        elif email:
            user = self.user_service.get_user_by_email(email)
        else:
            return {"error": "Missing user_id or email"}

        if not user:
            return {"error": "User not found"}

        return {"user_id": user.id, "username": user.username, "email": user.email}

    def _deactivate_user(self, request: dict) -> dict:
        """Deactivate a user."""
        user_id = request.get("user_id")
        if not user_id:
            return {"error": "Missing user_id"}

        success = self.user_service.deactivate_user(user_id)
        return {"success": success}


class ProductHandler(BaseHandler):
    """Product request handler."""

    def process(self, request: dict) -> dict:
        """Process product-related requests."""
        action = request.get("action")

        if action == "create":
            return self._create_product(request)
        elif action == "get":
            return self._get_product(request)
        elif action == "list":
            return self._list_products()
        elif action == "update_stock":
            return self._update_stock(request)
        else:
            return {"error": f"Unknown action: {action}"}

    def _create_product(self, request: dict) -> dict:
        """Create a new product."""
        name = request.get("name")
        description = request.get("description", "")
        price = request.get("price", 0.0)
        stock = request.get("stock", 0)

        if not name:
            return {"error": "Missing product name"}

        product = self.product_service.create_product(name, description, price, stock)
        return {"product_id": product.id, "name": product.name}

    def _get_product(self, request: dict) -> dict:
        """Get product by ID."""
        product_id = request.get("product_id")
        if not product_id:
            return {"error": "Missing product_id"}

        product = self.product_service.get_product(product_id)
        if not product:
            return {"error": "Product not found"}

        return {
            "product_id": product.id,
            "name": product.name,
            "price": product.price,
            "stock": product.stock
        }

    def _list_products(self) -> dict:
        """List all products."""
        products = self.product_service.list_products()
        return {"products": [{"id": p.id, "name": p.name, "price": p.price} for p in products]}

    def _update_stock(self, request: dict) -> dict:
        """Update product stock."""
        product_id = request.get("product_id")
        quantity = request.get("quantity", 0)

        if not product_id:
            return {"error": "Missing product_id"}

        success = self.product_service.update_stock(product_id, quantity)
        return {"success": success}


class OrderHandler(BaseHandler):
    """Order request handler."""

    def process(self, request: dict) -> dict:
        """Process order-related requests."""
        action = request.get("action")

        if action == "create":
            return self._create_order(request)
        elif action == "get":
            return self._get_order(request)
        elif action == "cancel":
            return self._cancel_order(request)
        else:
            return {"error": f"Unknown action: {action}"}

    def _create_order(self, request: dict) -> dict:
        """Create a new order."""
        user_id = request.get("user_id")
        items_data = request.get("items", [])

        if not user_id:
            return {"error": "Missing user_id"}
        if not items_data:
            return {"error": "No items in order"}

        items = []
        for item_data in items_data:
            items.append(OrderItem(
                product_id=item_data.get("product_id", 0),
                quantity=item_data.get("quantity", 1),
                price=item_data.get("price", 0.0)
            ))

        order = self.order_service.create_order(user_id, items)
        if not order:
            return {"error": "Failed to create order (user not found or insufficient stock)"}

        return {"order_id": order.id, "total": order.total}

    def _get_order(self, request: dict) -> dict:
        """Get order by ID."""
        order_id = request.get("order_id")
        if not order_id:
            return {"error": "Missing order_id"}

        order = self.order_service.get_order(order_id)
        if not order:
            return {"error": "Order not found"}

        return {
            "order_id": order.id,
            "user_id": order.user_id,
            "total": order.total,
            "status": order.status,
            "items": [
                {"product_id": i.product_id, "quantity": i.quantity, "price": i.price}
                for i in order.items
            ]
        }

    def _cancel_order(self, request: dict) -> dict:
        """Cancel an order."""
        order_id = request.get("order_id")
        if not order_id:
            return {"error": "Missing order_id"}

        success = self.order_service.cancel_order(order_id)
        return {"success": success}


# Function that creates a call cycle for testing
def process_user_order(user_handler: UserHandler, order_handler: OrderHandler, request: dict) -> dict:
    """
    Process a user order - creates a call cycle:
    process_user_order -> user_handler.process -> order_handler.process
    """
    # First validate user exists
    user_result = user_handler.process({"action": "get", "user_id": request.get("user_id")})
    if "error" in user_result:
        return user_result

    # Then process the order
    order_result = order_handler.process({"action": "create", **request})
    return order_result


def validate_and_process(request: dict) -> dict:
    """Validate and process a request - creates indirect recursion through handlers."""
    handler_map = {
        "user": UserHandler(),
        "product": ProductHandler(),
        "order": OrderHandler(),
    }

    handler_type = request.get("type")
    if handler_type not in handler_map:
        return {"error": f"Unknown handler type: {handler_type}"}

    handler = handler_map[handler_type]
    return handler.process(request)


# Async-style functions for testing
async def async_get_user(user_id: int) -> dict:
    """Async function to get user."""
    handler = UserHandler()
    return handler.process({"action": "get", "user_id": user_id})


async def async_create_order(user_id: int, items: list) -> dict:
    """Async function to create order."""
    handler = OrderHandler(UserHandler().user_service, ProductHandler().product_service)
    return handler.process({"action": "create", "user_id": user_id, "items": items})


# Decorator example
def log_calls(func):
    """Decorator that logs function calls."""
    def wrapper(*args, **kwargs):
        print(f"Calling {func.__name__}")
        return func(*args, **kwargs)
    return wrapper


@log_calls
def calculate_total(items: list) -> float:
    """Calculate total price of items."""
    return sum(item.get("price", 0) * item.get("quantity", 1) for item in items)


# Class demonstrating inheritance
class AdminHandler(UserHandler):
    """Admin handler with extended functionality."""

    def process(self, request: dict) -> dict:
        """Process admin requests."""
        action = request.get("action")

        if action == "delete_user":
            return self._delete_user(request)
        elif action == "list_all_users":
            return self._list_all_users()
        else:
            # Delegate to parent for regular user actions
            return super().process(request)

    def _delete_user(self, request: dict) -> dict:
        """Delete a user (admin only)."""
        user_id = request.get("user_id")
        if not user_id:
            return {"error": "Missing user_id"}

        # In real implementation, would actually delete
        return {"success": True, "message": f"User {user_id} deleted"}

    def _list_all_users(self) -> dict:
        """List all users (admin only)."""
        # In real implementation, would query all users
        return {"users": []}


# Multiple inheritance example
class Loggable:
    """Mixin for logging."""

    def log(self, message: str) -> None:
        """Log a message."""
        print(f"[LOG] {message}")


class Auditable:
    """Mixin for auditing."""

    def audit(self, action: str, details: dict) -> None:
        """Audit an action."""
        print(f"[AUDIT] {action}: {details}")


class SecureHandler(AdminHandler, Loggable, Auditable):
    """Handler with logging and auditing."""

    def process(self, request: dict) -> dict:
        """Process with logging and auditing."""
        self.log(f"Processing request: {request}")
        result = super().process(request)
        self.audit("process_request", {"request": request, "result": result})
        return result