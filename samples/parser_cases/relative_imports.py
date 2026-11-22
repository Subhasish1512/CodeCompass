"""Sample file: relative imports at various levels."""

# Level 1: from . import ...
from . import utils
from .utils import helper_function, HelperClass
from .utils import helper_function as hf

# Level 2: from .. import ...
from .. import config
from ..config import settings, Settings
from ..config import settings as cfg

# Level 3: from ... import ...
from ... import core
from ...core import BaseClass, base_function
from ...core import base_function as bf

# Level 4: from .... import ...
from .... import root_module
from ....root_module import RootClass

# Relative imports with module attribute access
from .submodule import SubClass
from ..sibling import SiblingClass
from ...cousin import CousinClass

# Mixed: relative import of specific names with aliases
from . import (
    module_a,
    module_b as mb,
    module_c,
)

from ..package import (
    ClassA,
    ClassB as CB,
    function_x,
    function_y as fy,
)

# Relative import with star
from . import *
from ..utils import *

# In a function
def use_relative_imports():
    from .local import local_function
    from ..parent import parent_function
    return local_function() + parent_function()


class RelativeImportUser:
    """Class that uses relative imports in methods."""

    def method_one(self):
        from . import utils
        return utils.helper_function()

    def method_two(self):
        from ..config import settings
        return settings.DEBUG

    async def async_method(self):
        from ...core import base_function
        return await base_function()


# Import at class level (valid in Python 3.7+)
from .annotations import AnnotatedClass
from ..types import TypeVar, Generic