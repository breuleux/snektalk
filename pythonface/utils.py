from itertools import count
from types import FunctionType, MethodType

from hrepr import H, hjson, hrepr

from .registry import callback_registry
from .session import current_evaluator

_count = count()


##########################
# Special JSON converter #
##########################


@hjson.dump.variant
def _pf_hjson(self, fn: (MethodType, FunctionType)):
    method_id = callback_registry.register(fn)
    return f"$$PFCB({method_id})"


def pf_hjson(obj):
    return str(_pf_hjson(obj))


#############
# Utilities #
#############


def join(elems, sep):
    rval = [elems[0]]
    for elem in elems[1:]:
        rval.append(sep)
        rval.append(elem)
    return rval


###########################
# Click/shift-click logic #
###########################


def _default_click(obj, evt):
    ctx = current_evaluator()
    if evt.get("shiftKey", False):
        ctx.queue(
            command="result",
            value=hrepr(obj),
            type="print",
        )
    else:
        varname = ctx.session.getvar(obj)
        ctx.queue(
            command="pastevar",
            value=varname,
        )


def _safe_set(elem, **props):
    if elem.is_virtual():
        return H.div(elem, **props)
    else:
        return elem(**props)


def represents(obj, elem, pinnable=False):
    if obj is None:
        return elem
    elif elem.get_attribute("objid", None) is not None:
        return _safe_set(elem, pinnable=pinnable)
    else:
        method_id = callback_registry.register(MethodType(_default_click, obj))
        return _safe_set(elem, objid=method_id, pinnable=pinnable)


##############
# Interactor #
##############


class BaseJSCaller:
    def __init__(self, interactor, jsid):
        self._interactor = interactor
        self._jsid = jsid

    def _getcode(self, method_name, args):
        if not self._interactor:
            raise Exception("The JavaScript interface is not active.")
        argtext = ",".join(map(pf_hjson, args))
        return f"""
        require(
            ['{self._jsid}'],
            wobj => {{
                let obj = wobj.deref();
                if (obj !== null) {{
                    obj.{method_name}({argtext});
                }}
            }}
        );
        """


class AJSCaller(BaseJSCaller):
    def __getattr__(self, method_name):
        async def call(*args):
            code = self._getcode(method_name, args)
            prom = asyncio.Promise()
            current_evaluator().queue(
                command="eval",
                value=code,
                promise=prom,
            )
            return await prom

        return call


class JSCaller(BaseJSCaller):
    def __init__(self, interactor, jsid, return_hrepr):
        super().__init__(interactor, jsid)
        self._return_hrepr = return_hrepr

    def __getattr__(self, method_name):
        def call(*args):
            code = self._getcode(method_name, args)
            if self._return_hrepr:
                return H.javascript(code)
            else:
                current_evaluator().queue(
                    command="eval",
                    value=code,
                )

        return call


class Interactor:
    js_code = None

    @classmethod
    def show(cls, *args, nav=False, **kwargs):
        instance = cls(*args, **kwargs)
        html = hrepr(instance)
        if nav:
            current_evaluator().queue(
                command="set_nav",
                value=html,
            )
        else:
            print(html)
        return instance

    def __init__(self, parameters):
        self.jsid = f"interactor{next(_count)}"
        self.js = JSCaller(self, self.jsid, return_hrepr=False)
        self.hjs = JSCaller(self, self.jsid, return_hrepr=True)
        self.ajs = AJSCaller(self, self.jsid)
        self.parameters = parameters
        methods = {}
        for method_name in dir(self):
            if method_name.startswith("py_"):
                methods[method_name[3:]] = getattr(self, method_name)
        self.parameters["py"] = methods
        self.active = False

    def __bool__(self):
        return self.active

    @classmethod
    def __hrepr_resources__(cls, H):
        if cls.js_code:
            rval = H.javascript(cls.js_code)
        else:
            rval = H.javascript(src=cls.js_source)
        return [rval(export=cls.js_constructor)]

    def __hrepr__(self, H, hrepr):
        params = pf_hjson(self.parameters)
        self.active = True
        return H.div(
            H.div(id=self.jsid),
            H.script(
                f"""
                define(
                    '{self.jsid}',
                    ['{self.js_constructor}'],
                    ctor => {{
                        let elem = document.getElementById('{self.jsid}');
                        let obj = new ctor(elem, {params});
                        return new WeakRef(obj);
                    }}
                );
                require(['{self.jsid}'], _ => null);
                """
            ),
        )


####################
# Misc interactors #
####################


class ReadOnly(Interactor):
    js_constructor = "ReadOnlyEditor"
    js_source = "/scripts/ro.js"