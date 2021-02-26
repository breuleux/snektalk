import importlib
import os
import sys
from types import ModuleType

from coleo import Option, default, run_cli
from jurigged import registry
from jurigged.utils import glob_filter

from . import runpy as snek_runpy
from .evaluator import Evaluator
from .server import serve


def main():
    run_cli(cli)


def cli():
    # Module or module:function to run
    # [options: -m]
    module: Option = default(None)

    # Path to the script to run
    # [positional: ?]
    script: Option = default(None)

    # Script arguments
    # [remainder]
    argv: Option

    # Don't watch changes on the filesystem
    # [options: --no-watch]
    no_watch: Option & bool = default(False)

    # Server port
    # [alias: -p]
    port: Option & int = default(None)

    # Path to socket
    # [alias: -S]
    socket: Option & str = default(None)

    pattern = glob_filter(".")
    if no_watch:
        watch_args = None
    else:
        watch_args = {
            "registry": registry,
            "pattern": pattern,
        }

    server_args = {
        "port": port,
        "sock": socket,
    }

    sess = serve(
        watch_args=watch_args,
        template={"title": module or script or "snektalk"},
        **server_args,
    )
    mod = None
    exc = None

    try:
        if module:
            if script is not None:
                argv.insert(0, script)
            sys.argv[1:] = argv

            if ":" in module:
                module, func = module.split(":", 1)
                mod = importlib.import_module(module)
                call = getattr(mod, func)
                call()

            else:
                mod = snek_runpy.run_module(module, run_name="__main__")

        elif script:
            path = os.path.abspath(script)
            if pattern(path):
                # It won't auto-trigger through runpy, probably some idiosyncracy of
                # module resolution
                registry.prepare("__main__", path)
            sys.argv[1:] = argv
            mod = snek_runpy.run_path(path, run_name="__main__")

        else:
            mod = ModuleType("__main__")

    except Exception as exc:
        if sess is not None:
            sess.blt["$$exc_info"] = sys.exc_info()
            sess.schedule(sess.send_result(exc, type="exception"))
        else:
            raise

    finally:
        if sess is not None and mod is not None:
            Evaluator(mod, vars(mod), None, sess).loop()
