"""Sample file: various import styles including aliases and relative imports."""

# Standard library imports
import os
import sys
import json
import asyncio
from typing import List, Dict, Optional
from pathlib import Path
from collections import defaultdict
from functools import wraps

# Third-party imports (simulated)
import numpy as np
import pandas as pd
from requests import get, post
from django.db import models
from django.conf import settings as django_settings

# Aliased imports
import xml.etree.ElementTree as ET
from decimal import Decimal as D
from datetime import datetime, timedelta as td

# From-imports with aliases
from mypackage.module import SomeClass as SC
from mypackage.module import func1, func2 as f2, func3
from .local_module import LocalClass
from ..parent_module import ParentClass
from ...grandparent import GrandParentClass

# Relative imports
from . import sibling_module
from .sibling_module import sibling_function
from .. import parent_package
from ..sibling_package import sibling_package_function

# Import with star (not recommended but valid)
from mypackage.utils import *

# Conditional imports
try:
    import optional_dependency
    HAS_OPTIONAL = True
except ImportError:
    optional_dependency = None
    HAS_OPTIONAL = False

# Import inside function (local import)
def function_with_local_import():
    import math
    return math.pi


class MyModel(models.Model):
    """Django-like model showing inheritance."""
    name = models.CharField(max_length=100)
    value = models.IntegerField(default=0)

    class Meta:
        db_table = "my_model"


def process_data(data: List[Dict]) -> Dict:
    """Function using imported modules."""
    result = defaultdict(list)
    for item in data:
        result[item.get("key", "default")].append(item)
    return dict(result)


async def async_fetch(url: str) -> dict:
    """Async function using imported aiohttp (simulated)."""
    # Simulated: response = await aiohttp.get(url)
    return {"url": url, "status": 200}