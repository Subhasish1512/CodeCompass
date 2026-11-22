"""API endpoints for the demo application."""

from typing import Optional
from .handlers import (
    UserHandler, ProductHandler, OrderHandler,
    validate_and_process, process_user_order,
    AdminHandler, SecureHandler
)
from .models import User, Product, Order
from .services import UserService, ProductService, OrderService


# Global handler instances (singleton pattern)
_user_handler: Optional[UserHandler] = None
_product_handler: Optional[ProductHandler] = None
_order_handler: Optional[OrderHandler] = None


def get_user_handler() -> UserHandler:
    """Get or create user handler."""
    global _user_handler
    if _user_handler is None:
        _user_handler = UserHandler()
    return _user_handler


def get_product_handler() -> ProductHandler:
    """Get or create product handler."""
    global _product_handler
    if _product_handler is None:
        _product_handler = ProductHandler()
    return _product_handler


def get_order_handler() -> OrderHandler:
    """Get or create order handler."""
    global _order_handler
    if _order_handler is None:
        _order_handler = OrderHandler(
            get_user_handler().user_service,
            get_product_handler().product_service
        )
    return _order_handler


def create_user_endpoint(data: dict) -> dict:
    """Create user API endpoint."""
    handler = get_user_handler()
    return handler.process({
        "action": "create",
        "username": data.get("username"),
        "email": data.get("email")
    })


def get_user_endpoint(user_id: int) -> dict:
    """Get user API endpoint."""
    handler = get_user_handler()
    return handler.process({"action": "get", "user_id": user_id})


def create_product_endpoint(data: dict) -> dict:
    """Create product API endpoint."""
    handler = get_product_handler()
    return handler.process({
        "action": "create",
        "name": data.get("name"),
        "description": data.get("description", ""),
        "price": data.get("price", 0.0),
        "stock": data.get("stock", 0)
    })


def create_order_endpoint(data: dict) -> dict:
    """Create order API endpoint - demonstrates call cycle."""
    # This creates a cycle: create_order_endpoint -> process_user_order ->
    # user_handler.process -> order_handler.process
    return process_user_order(
        get_user_handler(),
        get_order_handler(),
        {"user_id": data.get("user_id"), "items": data.get("items", [])}
    )


def get_order_endpoint(order_id: int) -> dict:
    """Get order API endpoint."""
    handler = get_order_handler()
    return handler.process({"action": "get", "order_id": order_id})


def cancel_order_endpoint(order_id: int) -> dict:
    """Cancel order API endpoint."""
    handler = get_order_handler()
    return handler.process({"action": "cancel", "order_id": order_id})


def generic_endpoint(request: dict) -> dict:
    """Generic endpoint that routes to validate_and_process."""
    # This calls validate_and_process which creates different handlers
    # based on the request type
    return validate_and_process(request)


# Admin endpoints
def admin_delete_user_endpoint(user_id: int) -> dict:
    """Admin delete user endpoint."""
    handler = AdminHandler()
    return handler.process({"action": "delete_user", "user_id": user_id})


def admin_list_users_endpoint() -> dict:
    """Admin list users endpoint."""
    handler = AdminHandler()
    return handler.process({"action": "list_all_users"})


# Secure handler endpoint
def secure_endpoint(request: dict) -> dict:
    """Secure endpoint with logging and auditing."""
    handler = SecureHandler()
    return handler.process(request)


# Utility functions
def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy", "service": "demo-api"}


def get_stats() -> dict:
    """Get service statistics."""
    return {
        "users": len(get_user_handler().user_service._users),
        "products": len(get_product_handler().product_service._products),
        "orders": len(get_order_handler().order_service._orders),
    }


# Demonstration of dynamic dispatch / duck typing
def process_any_handler(handler, action: str, **kwargs) -> dict:
    """
    Process any handler with duck typing.
    Resolution is approximate because we don't know the exact type at analysis time.
    """
    if hasattr(handler, 'process'):
        return handler.process({"action": action, **kwargs})
    return {"error": "Handler does not have process method"}


# Function showing method resolution with self
class APIRouter:
    """Simple API router demonstrating self/cls method calls."""

    def __init__(self):
        self.routes = {
            "users": get_user_handler,
            "products": get_product_handler,
            "orders": get_order_handler,
        }

    def route(self, path: str, action: str, data: dict) -> dict:
        """Route request to appropriate handler."""
        handler_factory = self.routes.get(path)
        if not handler_factory:
            return {"error": f"Unknown path: {path}"}

        handler = handler_factory()
        return self._dispatch(handler, action, data)

    def _dispatch(self, handler, action: str, data: dict) -> dict:
        """Dispatch to handler method."""
        return handler.process({"action": action, **data})

    def handle_request(self, request: dict) -> dict:
        """Handle a full request."""
        path = request.get("path", "")
        action = request.get("action", "")
        data = request.get("data", {})
        return self.route(path, action, data)