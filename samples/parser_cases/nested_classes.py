"""Sample file: nested classes, inner classes."""

class OuterClass:
    """Outer class with nested classes."""

    class InnerClass:
        """Inner class inside OuterClass."""

        def inner_method(self):
            """Method inside inner class."""
            return "inner"

    class AnotherInner:
        """Another inner class."""

        def __init__(self):
            self.value = 42

        def get_value(self):
            return self.value

    def outer_method(self):
        """Method in outer class."""
        inner = self.InnerClass()
        return inner.inner_method()


class StandaloneClass:
    """A class at module level."""

    def method(self):
        return "standalone"


def module_function():
    """Module-level function."""
    outer = OuterClass()
    return outer.outer_method()