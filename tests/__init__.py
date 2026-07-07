import importlib.machinery
import importlib.util
import re
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def load_script(relpath: str) -> types.ModuleType:
    """Import a script by repo-relative path. An explicit SourceFileLoader is
    required: spec_from_file_location can't infer a loader for the bin/ scripts,
    which have no .py suffix. The scripts guard their stdin/exec handling behind
    `if __name__ == '__main__'`, so importing only defines the functions —
    nothing reads stdin, execs, or exits."""
    path = _ROOT / relpath
    name = re.sub(r"\W", "_", path.stem)
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module
