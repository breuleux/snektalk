import json
import os
import subprocess

from hrepr import H
from sanic import Sanic

from .session import Session, Evaluator

here = os.path.dirname(__file__)
assets_path = os.path.join(here, "../assets")


def define(glb=None):
    app = Sanic("pythonface")
    app.static("/", f"{assets_path}/index.html")
    app.static("/scripts/", f"{assets_path}/scripts/")
    app.static("/style/", f"{assets_path}/style/")

    @app.websocket("/pf")
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

    app = Sanic("pythonface")
    app.static("/", f"{assets_path}/index.html")
    app.static("/scripts/", f"{assets_path}/scripts/")
    app.static("/style/", f"{assets_path}/style/")

    @app.websocket("/pf")
    async def feed(request, ws):
        sess = Session(glb or {}, ws)
        Evaluator(sess, glb).push()

        await sess.run(func)

        while True:
            command = json.loads(await ws.recv())
            print("recv", command)
            await sess.recv(**command)

    @app.listener("after_server_start")
    async def launch_func(app, loop):
        subprocess.run(["open", "http://localhost:6499/"])

    app.run(host="0.0.0.0", port=6499)
