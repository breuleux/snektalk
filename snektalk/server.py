import errno
import json
import os
import random
import socket
import subprocess

from hrepr import H
from sanic import Sanic

from .session import Evaluator, Session

here = os.path.dirname(__file__)
assets_path = os.path.join(here, "../assets")


def check_port(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("0.0.0.0", port))
    except socket.error as e:
        if e.errno == errno.EADDRINUSE:
            return False
        else:
            raise
    s.close()
    return True


def find_port(preferred_port, min_port, max_port):
    """Find a free port in the specified range.

    Use preferred_port if available (does not have to be in the range).
    """
    candidate = preferred_port
    while not check_port(candidate):
        print("Nope to", candidate)
        candidate = random.randint(min_port, max_port)
    return candidate


def define(glb=None):
    app = Sanic("snektalk")
    app.static("/", f"{assets_path}/index.html")
    app.static("/scripts/", f"{assets_path}/scripts/")
    app.static("/style/", f"{assets_path}/style/")

    @app.websocket("/sktk")
    async def feed(request, ws):
        sess = Session(glb or {}, ws)

        while True:
            command = json.loads(await ws.recv())
            print("recv", command)
            await sess.recv(**command)

    return app


def serve(glb=None):
    app = define(glb)
    app.run(host="0.0.0.0", port=6499)


def run(func):
    glb = func.__globals__
    port = find_port(6499, min_port=6500, max_port=6600)

    app = Sanic("snektalk")
    app.static("/", f"{assets_path}/index.html")
    app.static("/scripts/", f"{assets_path}/scripts/")
    app.static("/style/", f"{assets_path}/style/")

    @app.websocket("/sktk")
    async def feed(request, ws):
        sess = Session(glb or {}, ws, Evaluator)
        sess.schedule(sess.command_submit(expr=func))
        while True:
            command = json.loads(await ws.recv())
            print("recv", command)
            await sess.recv(**command)

    @app.listener("after_server_start")
    async def launch_func(app, loop):
        subprocess.run(["open", f"http://localhost:{port}/"])

    app.run(host="0.0.0.0", port=port)