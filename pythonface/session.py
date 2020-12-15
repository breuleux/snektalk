import asyncio
import builtins
import inspect
import json
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from itertools import count

from hrepr import H, Tag, hrepr

from .registry import UNAVAILABLE, callback_registry

_c = count()

_current_evaluator = ContextVar("current_evaluator", default=None)


def current_session():
    return _current_evaluator.get().session


def current_evaluator():
    return _current_evaluator.get()


class EvaluatorContext:
    def __init__(self, session):
        self.session = session
        self.evalid = next(_c)

    @contextmanager
    def push_context(self):
        token = _current_evaluator.set(self)
        try:
            yield
        finally:
            _current_evaluator.reset(token)

    def eval(self, expr):
        with self.push_context():
            try:
                return eval(expr, self.session.glb)
            except SyntaxError:
                exec(expr, self.session.glb)
                return None

    def queue(self, **command):
        self.session.queue(**command, evalid=self.evalid)

    async def send(self, **command):
        return await self.session.send(**command, evalid=self.evalid)


class Session:
    def __init__(self, glb, socket):
        self.glb = glb
        self.blt = vars(builtins)
        self.idmap = {}
        self.varcount = count(1)
        self.socket = socket
        self.sent_resources = set()
        self.loop = asyncio.get_running_loop()

    async def direct_send(self, **command):
        """Send a command to the client."""
        await self.socket.send(json.dumps(command))

    async def send(self, **command):
        """Send a command to the client, plus any resources.

        Any field that is a Tag and contains resources will send
        resource commands to the client to load these resources.
        A resource is only sent once, the first time it is needed.
        """
        resources = []
        for k, v in command.items():
            if isinstance(v, Tag):
                resources.extend(v.collect_resources())
                command[k] = str(v)

        for resource in resources:
            if resource not in self.sent_resources:
                await self.direct_send(
                    command="resource",
                    value=str(resource),
                )
                self.sent_resources.add(resource)

        await self.direct_send(**command)

    def queue(self, **command):
        """Queue a command to the client, plus any resources.

        This queues the command using the session's asyncio loop.
        """
        self.loop.create_task(self.send(**command))

    def newvar(self):
        """Create a new variable."""
        return f"_{next(self.varcount)}"

    def getvar(self, obj):
        """Get the variable name corresponding to the object.

        If the object is not already associated to a variable, one
        will be created and set in the global scope.
        """
        ido = id(obj)
        if ido in self.idmap:
            varname = self.idmap[ido]
        else:
            varname = self.newvar()
            self.idmap[ido] = varname
        self.blt[varname] = obj
        return varname

    def represent(self, typ, result):
        if isinstance(result, Tag):
            return typ, result

        try:
            html = hrepr(result)
        except Exception as exc:
            try:
                html = hrepr(exc)
            except Exception:
                html = H.pre(
                    traceback.format_exception(
                        builtins.type(exc), exc, exc.__traceback__
                    )
                )
                typ = "hrepr_exception"
        return typ, html

    async def send_result(self, result, *, type, evalid):
        type, html = self.represent(type, result)
        await self.send(
            command="result",
            value=html,
            type=type,
            evalid=evalid,
        )

    async def recv(self, **command):
        cmd = command.pop("command", "none")
        meth = getattr(self, f"command_{cmd}", None)
        await meth(**command)

    async def run(self, fn):
        ev = EvaluatorContext(self)
        with ev.push_context():
            try:
                result = fn()
                typ = "expression"
            except Exception as e:
                result = e
                typ = "exception"

        self.blt["_"] = result

        await self.send_result(
            result,
            type=typ,
            evalid=ev.evalid,
        )

    async def command_submit(self, *, expr):
        ev = EvaluatorContext(self)

        try:
            result = ev.eval(expr)
            typ = "statement" if result is None else "expression"
        except Exception as e:
            result = e
            typ = "exception"

        self.blt["_"] = result

        await self.direct_send(
            command="echo",
            value=expr,
        )

        await self.send_result(
            result,
            type=typ,
            evalid=ev.evalid,
        )

    async def command_callback(self, *, id, response_id, arguments):
        ev = EvaluatorContext(self)

        try:
            cb = callback_registry.resolve(int(id))
        except KeyError:
            ev.queue(
                command="status",
                type="error",
                value="value is unavailable; it might have been garbage-collected",
            )
            return

        try:
            with ev.push_context():
                if inspect.isawaitable(cb):
                    result = await cb(*arguments)
                else:
                    result = cb(*arguments)

            await self.send(
                command="response",
                value=result,
                response_id=response_id,
            )

        except Exception as exc:
            await self.send(
                command="response",
                error={
                    "type": type(exc).__name__,
                    "message": str(exc.args[0]) if exc.args else None,
                },
                response_id=response_id,
            )
