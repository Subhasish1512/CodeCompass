"""Sample file: methods calling other methods via self, super() calls."""

class BaseClass:
    """Base class with methods."""

    def base_method(self) -> str:
        """Base method."""
        return "base"

    def another_base(self, x: int) -> int:
        """Another base method."""
        return x * 2

    def helper(self) -> str:
        """Helper method."""
        return "helper"


class DerivedClass(BaseClass):
    """Derived class overriding and calling base methods."""

    def derived_method(self) -> str:
        """Method that calls another method on self."""
        return self.base_method() + "_derived"

    def overriding_method(self) -> str:
        """Method that overrides and calls super()."""
        return super().base_method() + "_overridden"

    def calls_multiple(self) -> str:
        """Method that calls multiple other methods."""
        a = self.base_method()
        b = self.another_base(5)
        c = self.helper()
        return f"{a}-{b}-{c}"

    def calls_private(self) -> str:
        """Method that calls a private method."""
        return self._private_helper()

    def _private_helper(self) -> str:
        """Private helper method."""
        return "private"


class AnotherDerived(BaseClass):
    """Another derived class."""

    def uses_super_with_args(self, value: int) -> int:
        """Calls super with arguments."""
        return super().another_base(value) + 10

    def calls_sibling_method(self) -> str:
        """Calls another method on self that calls base."""
        return self.overriding_method()


class MethodCaller:
    """Class demonstrating various method call patterns."""

    def __init__(self):
        self.derived = DerivedClass()
        self.base = BaseClass()

    def call_on_self(self) -> str:
        """Call method on self."""
        return self.helper()

    def call_on_instance(self) -> str:
        """Call method on another instance."""
        return self.derived.derived_method()

    def call_on_base(self) -> str:
        """Call method on base class instance."""
        return self.base.base_method()

    def call_chain(self) -> str:
        """Chain of method calls."""
        return self.derived.calls_multiple()

    def helper(self) -> str:
        """Helper method."""
        return "caller_helper"


class ComplexInheritance(BaseClass):
    """More complex inheritance with multiple levels."""

    def level1(self) -> str:
        """Level 1 method."""
        return self.base_method() + "_l1"

    def level2(self) -> str:
        """Level 2 method calling level1."""
        return self.level1() + "_l2"

    def level3(self) -> str:
        """Level 3 method calling level2."""
        return self.level2() + "_l3"


def external_function():
    """Function that creates instances and calls methods."""
    obj = DerivedClass()
    return obj.derived_method()


def another_external():
    """Another external function."""
    caller = MethodCaller()
    return caller.call_chain()