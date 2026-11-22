"""Sample file: decorators on functions and classes."""

import functools
from typing import Callable


def my_decorator(func: Callable) -> Callable:
    """A simple decorator."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        print(f"Calling {func.__name__}")
        return func(*args, **kwargs)
    return wrapper


def decorator_with_args(prefix: str):
    """Decorator factory with arguments."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            print(f"{prefix}: {func.__name__}")
            return func(*args, **kwargs)
        return wrapper
    return decorator


class ClassDecorator:
    """A class used as a decorator."""

    def __init__(self, name: str):
        self.name = name

    def __call__(self, func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            print(f"[{self.name}] {func.__name__}")
            return func(*args, **kwargs)
        return wrapper


@my_decorator
def decorated_function(x: int, y: int) -> int:
    """A function with a simple decorator."""
    return x + y


@decorator_with_args("INFO")
def decorated_with_args(name: str) -> str:
    """A function with a decorator factory."""
    return f"Hello, {name}"


@ClassDecorator("MyClass")
def class_decorated(value: float) -> float:
    """A function decorated with a class instance."""
    return value * 2


@my_decorator
@decorator_with_args("DEBUG")
def multiply(x: int, y: int) -> int:
    """A function with multiple decorators."""
    return x * y


@my_decorator
class DecoratedClass:
    """A class with a decorator."""

    @decorator_with_args("METHOD")
    def method(self) -> str:
        return "method"


def undecorated_function() -> None:
    """A function without decorators."""
    pass