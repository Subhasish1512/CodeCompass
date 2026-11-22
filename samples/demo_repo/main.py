"""
Demo repository for CodeCompass smoke testing.
"""

from typing import List, Optional
from dataclasses import dataclass


@dataclass
class User:
    """User model."""
    id: int
    name: str
    email: str


class UserService:
    """Service for managing users."""

    def __init__(self):
        self.users: List[User] = []

    def create_user(self, name: str, email: str) -> User:
        """Create a new user."""
        user = User(id=len(self.users) + 1, name=name, email=email)
        self.users.append(user)
        return user

    def get_user(self, user_id: int) -> Optional[User]:
        """Get user by ID."""
        for user in self.users:
            if user.id == user_id:
                return user
        return None

    def list_users(self) -> List[User]:
        """List all users."""
        return self.users

    def delete_user(self, user_id: int) -> bool:
        """Delete user by ID."""
        user = self.get_user(user_id)
        if user:
            self.users.remove(user)
            return True
        return False


class EmailService:
    """Service for sending emails."""

    def send_welcome(self, user: User) -> bool:
        """Send welcome email to user."""
        print(f"Sending welcome email to {user.email}")
        return True

    def send_notification(self, user: User, message: str) -> bool:
        """Send notification to user."""
        print(f"Notifying {user.email}: {message}")
        return True


def main() -> None:
    """Main entry point."""
    user_service = UserService()
    email_service = EmailService()

    # Create some users
    alice = user_service.create_user("Alice", "alice@example.com")
    bob = user_service.create_user("Bob", "bob@example.com")

    # Send welcome emails
    email_service.send_welcome(alice)
    email_service.send_welcome(bob)

    # List users
    for user in user_service.list_users():
        print(f"User: {user.name} ({user.email})")


if __name__ == "__main__":
    main()