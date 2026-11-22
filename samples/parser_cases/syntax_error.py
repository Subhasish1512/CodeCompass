"""Sample file: contains a syntax error for testing error handling."""

def valid_function():
    """This function is valid."""
    return "hello"


class ValidClass:
    """This class is valid."""

    def valid_method(self):
        return 42


# Syntax error: missing closing parenthesis
def broken_function(
    print("This will cause a syntax error")


# Another syntax error: invalid indentation
class BrokenClass:
    def method(self):
    return "bad indent"


# Syntax error: unterminated string
def another_broken():
    return "unterminated string


# Valid code after errors (tree-sitter should still parse this)
def after_error():
    """This function comes after syntax errors."""
    x = 1 + 2
    return x


class AfterError:
    """Class after syntax errors."""

    def method(self):
        return "ok"


# Valid lambda
valid_lambda = lambda x: x * 2

# Valid comprehension after error
valid_list = [i for i in range(10)]