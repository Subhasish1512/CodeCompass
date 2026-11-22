"""Sample file: async functions, async methods, await expressions."""

import asyncio
from typing import List


async def async_function(x: int) -> int:
    """A simple async function."""
    await asyncio.sleep(0.1)
    return x * 2


async def async_function_with_args(a: str, b: int = 10) -> str:
    """Async function with arguments and default values."""
    await asyncio.sleep(0.01)
    return f"{a}-{b}"


class AsyncClass:
    """Class with async methods."""

    def __init__(self, name: str):
        self.name = name
        self.counter = 0

    async def async_method(self, value: int) -> int:
        """An async instance method."""
        self.counter += 1
        await asyncio.sleep(0.01)
        return value + self.counter

    async def async_method_no_args(self) -> str:
        """Async method without arguments."""
        await asyncio.sleep(0.01)
        return f"Hello from {self.name}"

    @staticmethod
    async def static_async_method(x: int) -> int:
        """Static async method."""
        await asyncio.sleep(0.01)
        return x * 3

    @classmethod
    async def class_async_method(cls, y: int) -> int:
        """Class async method."""
        await asyncio.sleep(0.01)
        return y * 4


async def async_generator(n: int):
    """Async generator function."""
    for i in range(n):
        await asyncio.sleep(0.01)
        yield i


async def caller_function() -> List[int]:
    """Function that calls async functions."""
    results = []

    # Call async function
    result1 = await async_function(5)
    results.append(result1)

    # Call async method
    obj = AsyncClass("test")
    result2 = await obj.async_method(10)
    results.append(result2)

    # Call static async method
    result3 = await AsyncClass.static_async_method(3)
    results.append(result3)

    # Await in list comprehension
    values = [await async_function(i) for i in range(3)]
    results.extend(values)

    return results


async def await_in_loop() -> None:
    """Function with await in a loop."""
    for i in range(5):
        await async_function(i)


async def await_in_try_except() -> str:
    """Function with await in try/except."""
    try:
        result = await async_function(42)
        return str(result)
    except Exception as e:
        return f"Error: {e}"