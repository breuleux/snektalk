import ast
import ctypes
import functools
import os
import random
import re
import subprocess
import sys
import threading
import time
from itertools import count
from types import ModuleType

from hrepr import H
from jurigged import CodeFile, registry
from jurigged.recode import virtual_file

from .feat.edit import edit
from .session import current_session, new_evalid
from .version import version

cmd_rx = re.compile(r"/([^ ]+)( .*)?")


class ThreadKilledException(Exception):
    pass


class KillableThread(threading.Thread):
    def kill(self):
        ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_long(self.ident), ctypes.py_object(ThreadKilledException)
        )


class NamedThreads:
    def __init__(self):
        self.words = [
            word
            for word in open(
                os.path.join(os.path.dirname(__file__), "words.txt")
            )
            .read()
            .split("\n")
            if word
        ]
        random.shuffle(self.words)
        self.threads = {}
        self.count = count(1)

    def run_in_thread(self, fn, session=None):
        def run():
            reason = " finished"
            self.threads[word] = thread
            with session.set_context():
                with new_evalid():
                    session.queue_result(
                        H.div("Starting thread ", H.strong(word)), type="info",
                    )
                    try:
                        fn()
                    except ThreadKilledException:
                        reason = " killed"
                    except Exception as e:
                        session.queue_result(e, type="exception")
                    finally:
                        del self.threads[word]
                        self.words.append(word)
                        session.queue_result(
                            H.div("Thread ", H.strong(word), reason),
                            type="info",
                        )

        session = session or current_session()
        thread = KillableThread(target=run, daemon=True)

        if not self.words:
            self.words.append(f"t{next(self.count)}")
        word = self.words.pop()

        thread.start()
        return word, thread


threads = NamedThreads()


class StopEvaluator(Exception):
    pass


def safe_fail(fn):
    @functools.wraps(fn)
    def deco(self, *args, **kwargs):
        try:
            fn(self, *args, **kwargs)
        except Exception as e:
            self.session.blt["_"] = e
            self.session.blt["$$exc_info"] = sys.exc_info()
            self.session.queue_result(e, type="exception")

    return deco


def evaluate(expr, glb, lcl):
    filename = virtual_file("repl", expr)
    mname = glb.get("__name__", None)
    cf = CodeFile(filename=filename, source=expr, module_name=mname)
    registry.cache[filename] = cf

    tree = ast.parse(expr)
    assert isinstance(tree, ast.Module)
    assert len(tree.body) > 0
    *body, last = tree.body
    if isinstance(last, ast.Expr):
        last = ast.Expression(last.value)
    else:
        body.append(last)
        last = None
    for stmt in body:
        compiled = compile(
            ast.Module(body=[stmt], type_ignores=[]),
            mode="exec",
            filename=filename,
        )
        exec(compiled, glb, lcl)
    if last:
        compiled = compile(last, mode="eval", filename=filename)
        rval = eval(compiled, glb, lcl)
    else:
        rval = None

    cf.code.set_globals(glb)
    for v in lcl.values():
        cf.associate(v)

    return rval


class Evaluator:
    def __init__(self, module, glb, lcl, session):
        self.session = session
        if module is None:
            module = ModuleType("__main__")
            module.__dict__.update(glb)
        self.module = module
        self.glb = glb
        self.lcl = lcl

    def eval(self, expr, glb=None, lcl=None):
        if glb is None:
            glb = self.glb
        if lcl is None:
            lcl = self.lcl if self.lcl is not None else glb
        return evaluate(expr, glb, lcl)

    def format_modname(self):
        modname = getattr(self.module, "__name__", "<module>")
        return H.span["snek-interpreter-in"](modname)

    shorthand = {
        "q": "quit",
        "d": "debug",
    }

    @safe_fail
    def command_eval(self, expr, glb=None, lcl=None):
        assert isinstance(expr, str)
        expr = expr.lstrip()

        self.session.queue(command="echo", value=expr, process=False)

        result = self.eval(expr, glb, lcl)
        typ = "statement" if result is None else "expression"

        self.session.blt["_"] = result

        self.session.queue_result(result, type=typ)

    @safe_fail
    def command_dir(self, expr, glb=None, lcl=None):
        from .repr import hdir

        expr = expr.lstrip()

        self.session.queue(command="echo", value=f"/dir {expr}", process=False)

        result = hdir(self.eval(expr, glb, lcl))
        typ = "statement" if result is None else "expression"

        self.session.blt["_"] = result

        self.session.queue_result(result, type=typ)

    @safe_fail
    def command_edit(self, expr, glb, lcl):
        expr = expr.lstrip()
        obj = self.eval(expr, glb, lcl)
        self.session.queue_result(
            edit(obj, evaluator=self, autofocus=True), type="expression"
        )

    @safe_fail
    def command_debug(self, expr, glb, lcl):
        from .debug import SnekTalkDb

        expr = expr.lstrip()
        glb = glb or self.glb
        lcl = lcl or self.lcl
        if expr:
            self.session.queue(
                command="echo", value=f"/debug {expr}", process=False
            )
            result = SnekTalkDb().runeval_step(expr, glb, lcl)
            typ = "statement" if result is None else "expression"
            self.session.queue_result(result, type=typ)

        else:
            exc = self.session.blt.get("$$exc_info", None)
            if exc:
                self.session.queue(
                    command="echo",
                    value=f"Debugging {exc[0].__qualname__}: {exc[1]}",
                    language="text",
                    process=False,
                )
                tb = exc[2]
                SnekTalkDb().interaction(tb.tb_frame, tb)
            else:
                self.session.queue_result(
                    H.div("Last expression was not an exception"),
                    type="exception",
                )

    @safe_fail
    def command_shell(self, expr, glb, lcl):
        expr = expr.lstrip()
        self.session.queue(command="echo", value=f"//{expr}", process=False)
        proc = subprocess.run(expr, shell=True, capture_output=True)
        if proc.stdout:
            self.session.queue_result(
                H.div["snek-shellout"](proc.stdout.decode("utf8")),
                type="expression",
            )
        if proc.stderr:
            self.session.queue_result(
                H.div["snek-shellout"](proc.stderr.decode("utf8")),
                type="exception",
            )

    @safe_fail
    def command_thread(self, expr, glb, lcl):
        assert isinstance(expr, str)
        expr = expr.lstrip()
        self.session.queue(command="echo", value=expr, process=False)

        def run():
            result = self.eval(expr, glb, lcl)
            typ = "statement" if result is None else "expression"
            self.session.blt["_"] = result
            self.session.queue_result(result, type=typ)

        threads.run_in_thread(run)

    def command_kill(self, expr, glb, lcl):
        tname = expr.strip()
        self.session.queue(
            command="echo", value=f"/kill {tname}", process=False
        )
        if tname in threads.threads:
            thread = threads.threads[tname]
            thread.kill()
            self.session.queue(
                command="result",
                value=f"Sent an exception to {tname}. It should terminate as soon as possible.",
                type="print",
            )
        else:
            self.session.queue(
                command="result",
                value=f"No thread named {tname}"
                if tname
                else "Please provide the name of the thread to kill",
                type="exception",
            )

    def command_quit(self, expr, glb, lcl):
        self.session.queue_result(H.div("/quit"), type="echo")
        self.session.queue_result(
            H.div("Quitting interpreter in ", self.format_modname()),
            type="info",
        )
        # Small delay so that the messages get flushed
        time.sleep(0.01)
        raise StopEvaluator()

    def missing(self, expr, cmd, arg, glb, lcl):
        self.session.queue(command="echo", value=expr, process=False)
        self.session.queue_result(
            H.div(f"Command '{cmd}' does not exist"), type="exception"
        )

    def dispatch(self, expr, glb=None, lcl=None):
        if expr.startswith("//"):
            cmd = "shell"
            arg = expr[2:]
        elif match := cmd_rx.fullmatch(expr):
            cmd, arg = match.groups()
            if arg is None:
                arg = ""
        elif expr.startswith("?"):
            cmd = "dir"
            arg = expr[1:]
        else:
            cmd = "eval"
            arg = expr

        cmd = self.shorthand.get(cmd, cmd)
        method = getattr(self, f"command_{cmd}", None)
        if method is None:
            self.missing(expr, cmd, arg, glb, lcl)
        else:
            method(arg, glb, lcl)

    def loop(self):
        pyv = sys.version_info
        self.session.queue_result(
            H.div(
                "Starting interpreter in ",
                self.format_modname(),
                H.br(),
                f"Snektalk {version} using Python {pyv.major}.{pyv.minor}.{pyv.micro}",
            ),
            type="info",
        )
        try:
            while True:
                prompt = H.span["snek-input-mode-python"](">>>")
                with self.session.prompt(prompt) as cmd:
                    if cmd["command"] == "expr":
                        expr = cmd["expr"]
                        if expr.strip():
                            self.dispatch(expr)
                    elif cmd["command"] == "noop":
                        pass
        except StopEvaluator:
            return

    run = command_eval
