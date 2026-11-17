var us = { exports: {} }, Ar = {};
var Ef;
function Sh() {
  if (Ef) return Ar;
  Ef = 1;
  var i = /* @__PURE__ */ Symbol.for("react.transitional.element"), f = /* @__PURE__ */ Symbol.for("react.fragment");
  function u(o, m, p) {
    var h = null;
    if (p !== void 0 && (h = "" + p), m.key !== void 0 && (h = "" + m.key), "key" in m) {
      p = {};
      for (var v in m)
        v !== "key" && (p[v] = m[v]);
    } else p = m;
    return m = p.ref, {
      $$typeof: i,
      type: o,
      key: h,
      ref: m !== void 0 ? m : null,
      props: p
    };
  }
  return Ar.Fragment = f, Ar.jsx = u, Ar.jsxs = u, Ar;
}
var Af;
function Eh() {
  return Af || (Af = 1, us.exports = Sh()), us.exports;
}
var g = Eh(), fs = { exports: {} }, pe = {};
var Mf;
function Ah() {
  if (Mf) return pe;
  Mf = 1;
  var i = /* @__PURE__ */ Symbol.for("react.transitional.element"), f = /* @__PURE__ */ Symbol.for("react.portal"), u = /* @__PURE__ */ Symbol.for("react.fragment"), o = /* @__PURE__ */ Symbol.for("react.strict_mode"), m = /* @__PURE__ */ Symbol.for("react.profiler"), p = /* @__PURE__ */ Symbol.for("react.consumer"), h = /* @__PURE__ */ Symbol.for("react.context"), v = /* @__PURE__ */ Symbol.for("react.forward_ref"), x = /* @__PURE__ */ Symbol.for("react.suspense"), b = /* @__PURE__ */ Symbol.for("react.memo"), D = /* @__PURE__ */ Symbol.for("react.lazy"), C = /* @__PURE__ */ Symbol.for("react.activity"), T = Symbol.iterator;
  function L(S) {
    return S === null || typeof S != "object" ? null : (S = T && S[T] || S["@@iterator"], typeof S == "function" ? S : null);
  }
  var G = {
    isMounted: function() {
      return !1;
    },
    enqueueForceUpdate: function() {
    },
    enqueueReplaceState: function() {
    },
    enqueueSetState: function() {
    }
  }, Q = Object.assign, de = {};
  function fe(S, _, Y) {
    this.props = S, this.context = _, this.refs = de, this.updater = Y || G;
  }
  fe.prototype.isReactComponent = {}, fe.prototype.setState = function(S, _) {
    if (typeof S != "object" && typeof S != "function" && S != null)
      throw Error(
        "takes an object of state variables to update or a function which returns an object of state variables."
      );
    this.updater.enqueueSetState(this, S, _, "setState");
  }, fe.prototype.forceUpdate = function(S) {
    this.updater.enqueueForceUpdate(this, S, "forceUpdate");
  };
  function se() {
  }
  se.prototype = fe.prototype;
  function be(S, _, Y) {
    this.props = S, this.context = _, this.refs = de, this.updater = Y || G;
  }
  var Oe = be.prototype = new se();
  Oe.constructor = be, Q(Oe, fe.prototype), Oe.isPureReactComponent = !0;
  var Le = Array.isArray;
  function J() {
  }
  var K = { H: null, A: null, T: null, S: null }, U = Object.prototype.hasOwnProperty;
  function xe(S, _, Y) {
    var F = Y.ref;
    return {
      $$typeof: i,
      type: S,
      key: _,
      ref: F !== void 0 ? F : null,
      props: Y
    };
  }
  function tt(S, _) {
    return xe(S.type, _, S.props);
  }
  function R(S) {
    return typeof S == "object" && S !== null && S.$$typeof === i;
  }
  function $(S) {
    var _ = { "=": "=0", ":": "=2" };
    return "$" + S.replace(/[=:]/g, function(Y) {
      return _[Y];
    });
  }
  var ye = /\/+/g;
  function he(S, _) {
    return typeof S == "object" && S !== null && S.key != null ? $("" + S.key) : _.toString(36);
  }
  function Qe(S) {
    switch (S.status) {
      case "fulfilled":
        return S.value;
      case "rejected":
        throw S.reason;
      default:
        switch (typeof S.status == "string" ? S.then(J, J) : (S.status = "pending", S.then(
          function(_) {
            S.status === "pending" && (S.status = "fulfilled", S.value = _);
          },
          function(_) {
            S.status === "pending" && (S.status = "rejected", S.reason = _);
          }
        )), S.status) {
          case "fulfilled":
            return S.value;
          case "rejected":
            throw S.reason;
        }
    }
    throw S;
  }
  function z(S, _, Y, F, te) {
    var le = typeof S;
    (le === "undefined" || le === "boolean") && (S = null);
    var Se = !1;
    if (S === null) Se = !0;
    else
      switch (le) {
        case "bigint":
        case "string":
        case "number":
          Se = !0;
          break;
        case "object":
          switch (S.$$typeof) {
            case i:
            case f:
              Se = !0;
              break;
            case D:
              return Se = S._init, z(
                Se(S._payload),
                _,
                Y,
                F,
                te
              );
          }
      }
    if (Se)
      return te = te(S), Se = F === "" ? "." + he(S, 0) : F, Le(te) ? (Y = "", Se != null && (Y = Se.replace(ye, "$&/") + "/"), z(te, _, Y, "", function(Ut) {
        return Ut;
      })) : te != null && (R(te) && (te = tt(
        te,
        Y + (te.key == null || S && S.key === te.key ? "" : ("" + te.key).replace(
          ye,
          "$&/"
        ) + "/") + Se
      )), _.push(te)), 1;
    Se = 0;
    var Ge = F === "" ? "." : F + ":";
    if (Le(S))
      for (var me = 0; me < S.length; me++)
        F = S[me], le = Ge + he(F, me), Se += z(
          F,
          _,
          Y,
          le,
          te
        );
    else if (me = L(S), typeof me == "function")
      for (S = me.call(S), me = 0; !(F = S.next()).done; )
        F = F.value, le = Ge + he(F, me++), Se += z(
          F,
          _,
          Y,
          le,
          te
        );
    else if (le === "object") {
      if (typeof S.then == "function")
        return z(
          Qe(S),
          _,
          Y,
          F,
          te
        );
      throw _ = String(S), Error(
        "Objects are not valid as a React child (found: " + (_ === "[object Object]" ? "object with keys {" + Object.keys(S).join(", ") + "}" : _) + "). If you meant to render a collection of children, use an array instead."
      );
    }
    return Se;
  }
  function q(S, _, Y) {
    if (S == null) return S;
    var F = [], te = 0;
    return z(S, F, "", "", function(le) {
      return _.call(Y, le, te++);
    }), F;
  }
  function ne(S) {
    if (S._status === -1) {
      var _ = S._result;
      _ = _(), _.then(
        function(Y) {
          (S._status === 0 || S._status === -1) && (S._status = 1, S._result = Y);
        },
        function(Y) {
          (S._status === 0 || S._status === -1) && (S._status = 2, S._result = Y);
        }
      ), S._status === -1 && (S._status = 0, S._result = _);
    }
    if (S._status === 1) return S._result.default;
    throw S._result;
  }
  var He = typeof reportError == "function" ? reportError : function(S) {
    if (typeof window == "object" && typeof window.ErrorEvent == "function") {
      var _ = new window.ErrorEvent("error", {
        bubbles: !0,
        cancelable: !0,
        message: typeof S == "object" && S !== null && typeof S.message == "string" ? String(S.message) : String(S),
        error: S
      });
      if (!window.dispatchEvent(_)) return;
    } else if (typeof process == "object" && typeof process.emit == "function") {
      process.emit("uncaughtException", S);
      return;
    }
    console.error(S);
  }, B = {
    map: q,
    forEach: function(S, _, Y) {
      q(
        S,
        function() {
          _.apply(this, arguments);
        },
        Y
      );
    },
    count: function(S) {
      var _ = 0;
      return q(S, function() {
        _++;
      }), _;
    },
    toArray: function(S) {
      return q(S, function(_) {
        return _;
      }) || [];
    },
    only: function(S) {
      if (!R(S))
        throw Error(
          "React.Children.only expected to receive a single React element child."
        );
      return S;
    }
  };
  return pe.Activity = C, pe.Children = B, pe.Component = fe, pe.Fragment = u, pe.Profiler = m, pe.PureComponent = be, pe.StrictMode = o, pe.Suspense = x, pe.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE = K, pe.__COMPILER_RUNTIME = {
    __proto__: null,
    c: function(S) {
      return K.H.useMemoCache(S);
    }
  }, pe.cache = function(S) {
    return function() {
      return S.apply(null, arguments);
    };
  }, pe.cacheSignal = function() {
    return null;
  }, pe.cloneElement = function(S, _, Y) {
    if (S == null)
      throw Error(
        "The argument must be a React element, but you passed " + S + "."
      );
    var F = Q({}, S.props), te = S.key;
    if (_ != null)
      for (le in _.key !== void 0 && (te = "" + _.key), _)
        !U.call(_, le) || le === "key" || le === "__self" || le === "__source" || le === "ref" && _.ref === void 0 || (F[le] = _[le]);
    var le = arguments.length - 2;
    if (le === 1) F.children = Y;
    else if (1 < le) {
      for (var Se = Array(le), Ge = 0; Ge < le; Ge++)
        Se[Ge] = arguments[Ge + 2];
      F.children = Se;
    }
    return xe(S.type, te, F);
  }, pe.createContext = function(S) {
    return S = {
      $$typeof: h,
      _currentValue: S,
      _currentValue2: S,
      _threadCount: 0,
      Provider: null,
      Consumer: null
    }, S.Provider = S, S.Consumer = {
      $$typeof: p,
      _context: S
    }, S;
  }, pe.createElement = function(S, _, Y) {
    var F, te = {}, le = null;
    if (_ != null)
      for (F in _.key !== void 0 && (le = "" + _.key), _)
        U.call(_, F) && F !== "key" && F !== "__self" && F !== "__source" && (te[F] = _[F]);
    var Se = arguments.length - 2;
    if (Se === 1) te.children = Y;
    else if (1 < Se) {
      for (var Ge = Array(Se), me = 0; me < Se; me++)
        Ge[me] = arguments[me + 2];
      te.children = Ge;
    }
    if (S && S.defaultProps)
      for (F in Se = S.defaultProps, Se)
        te[F] === void 0 && (te[F] = Se[F]);
    return xe(S, le, te);
  }, pe.createRef = function() {
    return { current: null };
  }, pe.forwardRef = function(S) {
    return { $$typeof: v, render: S };
  }, pe.isValidElement = R, pe.lazy = function(S) {
    return {
      $$typeof: D,
      _payload: { _status: -1, _result: S },
      _init: ne
    };
  }, pe.memo = function(S, _) {
    return {
      $$typeof: b,
      type: S,
      compare: _ === void 0 ? null : _
    };
  }, pe.startTransition = function(S) {
    var _ = K.T, Y = {};
    K.T = Y;
    try {
      var F = S(), te = K.S;
      te !== null && te(Y, F), typeof F == "object" && F !== null && typeof F.then == "function" && F.then(J, He);
    } catch (le) {
      He(le);
    } finally {
      _ !== null && Y.types !== null && (_.types = Y.types), K.T = _;
    }
  }, pe.unstable_useCacheRefresh = function() {
    return K.H.useCacheRefresh();
  }, pe.use = function(S) {
    return K.H.use(S);
  }, pe.useActionState = function(S, _, Y) {
    return K.H.useActionState(S, _, Y);
  }, pe.useCallback = function(S, _) {
    return K.H.useCallback(S, _);
  }, pe.useContext = function(S) {
    return K.H.useContext(S);
  }, pe.useDebugValue = function() {
  }, pe.useDeferredValue = function(S, _) {
    return K.H.useDeferredValue(S, _);
  }, pe.useEffect = function(S, _) {
    return K.H.useEffect(S, _);
  }, pe.useEffectEvent = function(S) {
    return K.H.useEffectEvent(S);
  }, pe.useId = function() {
    return K.H.useId();
  }, pe.useImperativeHandle = function(S, _, Y) {
    return K.H.useImperativeHandle(S, _, Y);
  }, pe.useInsertionEffect = function(S, _) {
    return K.H.useInsertionEffect(S, _);
  }, pe.useLayoutEffect = function(S, _) {
    return K.H.useLayoutEffect(S, _);
  }, pe.useMemo = function(S, _) {
    return K.H.useMemo(S, _);
  }, pe.useOptimistic = function(S, _) {
    return K.H.useOptimistic(S, _);
  }, pe.useReducer = function(S, _, Y) {
    return K.H.useReducer(S, _, Y);
  }, pe.useRef = function(S) {
    return K.H.useRef(S);
  }, pe.useState = function(S) {
    return K.H.useState(S);
  }, pe.useSyncExternalStore = function(S, _, Y) {
    return K.H.useSyncExternalStore(
      S,
      _,
      Y
    );
  }, pe.useTransition = function() {
    return K.H.useTransition();
  }, pe.version = "19.2.3", pe;
}
var kf;
function Ms() {
  return kf || (kf = 1, fs.exports = Ah()), fs.exports;
}
var s = Ms(), ds = { exports: {} }, Mr = {}, ms = { exports: {} }, ps = {};
var zf;
function Mh() {
  return zf || (zf = 1, (function(i) {
    function f(z, q) {
      var ne = z.length;
      z.push(q);
      e: for (; 0 < ne; ) {
        var He = ne - 1 >>> 1, B = z[He];
        if (0 < m(B, q))
          z[He] = q, z[ne] = B, ne = He;
        else break e;
      }
    }
    function u(z) {
      return z.length === 0 ? null : z[0];
    }
    function o(z) {
      if (z.length === 0) return null;
      var q = z[0], ne = z.pop();
      if (ne !== q) {
        z[0] = ne;
        e: for (var He = 0, B = z.length, S = B >>> 1; He < S; ) {
          var _ = 2 * (He + 1) - 1, Y = z[_], F = _ + 1, te = z[F];
          if (0 > m(Y, ne))
            F < B && 0 > m(te, Y) ? (z[He] = te, z[F] = ne, He = F) : (z[He] = Y, z[_] = ne, He = _);
          else if (F < B && 0 > m(te, ne))
            z[He] = te, z[F] = ne, He = F;
          else break e;
        }
      }
      return q;
    }
    function m(z, q) {
      var ne = z.sortIndex - q.sortIndex;
      return ne !== 0 ? ne : z.id - q.id;
    }
    if (i.unstable_now = void 0, typeof performance == "object" && typeof performance.now == "function") {
      var p = performance;
      i.unstable_now = function() {
        return p.now();
      };
    } else {
      var h = Date, v = h.now();
      i.unstable_now = function() {
        return h.now() - v;
      };
    }
    var x = [], b = [], D = 1, C = null, T = 3, L = !1, G = !1, Q = !1, de = !1, fe = typeof setTimeout == "function" ? setTimeout : null, se = typeof clearTimeout == "function" ? clearTimeout : null, be = typeof setImmediate < "u" ? setImmediate : null;
    function Oe(z) {
      for (var q = u(b); q !== null; ) {
        if (q.callback === null) o(b);
        else if (q.startTime <= z)
          o(b), q.sortIndex = q.expirationTime, f(x, q);
        else break;
        q = u(b);
      }
    }
    function Le(z) {
      if (Q = !1, Oe(z), !G)
        if (u(x) !== null)
          G = !0, J || (J = !0, $());
        else {
          var q = u(b);
          q !== null && Qe(Le, q.startTime - z);
        }
    }
    var J = !1, K = -1, U = 5, xe = -1;
    function tt() {
      return de ? !0 : !(i.unstable_now() - xe < U);
    }
    function R() {
      if (de = !1, J) {
        var z = i.unstable_now();
        xe = z;
        var q = !0;
        try {
          e: {
            G = !1, Q && (Q = !1, se(K), K = -1), L = !0;
            var ne = T;
            try {
              t: {
                for (Oe(z), C = u(x); C !== null && !(C.expirationTime > z && tt()); ) {
                  var He = C.callback;
                  if (typeof He == "function") {
                    C.callback = null, T = C.priorityLevel;
                    var B = He(
                      C.expirationTime <= z
                    );
                    if (z = i.unstable_now(), typeof B == "function") {
                      C.callback = B, Oe(z), q = !0;
                      break t;
                    }
                    C === u(x) && o(x), Oe(z);
                  } else o(x);
                  C = u(x);
                }
                if (C !== null) q = !0;
                else {
                  var S = u(b);
                  S !== null && Qe(
                    Le,
                    S.startTime - z
                  ), q = !1;
                }
              }
              break e;
            } finally {
              C = null, T = ne, L = !1;
            }
            q = void 0;
          }
        } finally {
          q ? $() : J = !1;
        }
      }
    }
    var $;
    if (typeof be == "function")
      $ = function() {
        be(R);
      };
    else if (typeof MessageChannel < "u") {
      var ye = new MessageChannel(), he = ye.port2;
      ye.port1.onmessage = R, $ = function() {
        he.postMessage(null);
      };
    } else
      $ = function() {
        fe(R, 0);
      };
    function Qe(z, q) {
      K = fe(function() {
        z(i.unstable_now());
      }, q);
    }
    i.unstable_IdlePriority = 5, i.unstable_ImmediatePriority = 1, i.unstable_LowPriority = 4, i.unstable_NormalPriority = 3, i.unstable_Profiling = null, i.unstable_UserBlockingPriority = 2, i.unstable_cancelCallback = function(z) {
      z.callback = null;
    }, i.unstable_forceFrameRate = function(z) {
      0 > z || 125 < z ? console.error(
        "forceFrameRate takes a positive int between 0 and 125, forcing frame rates higher than 125 fps is not supported"
      ) : U = 0 < z ? Math.floor(1e3 / z) : 5;
    }, i.unstable_getCurrentPriorityLevel = function() {
      return T;
    }, i.unstable_next = function(z) {
      switch (T) {
        case 1:
        case 2:
        case 3:
          var q = 3;
          break;
        default:
          q = T;
      }
      var ne = T;
      T = q;
      try {
        return z();
      } finally {
        T = ne;
      }
    }, i.unstable_requestPaint = function() {
      de = !0;
    }, i.unstable_runWithPriority = function(z, q) {
      switch (z) {
        case 1:
        case 2:
        case 3:
        case 4:
        case 5:
          break;
        default:
          z = 3;
      }
      var ne = T;
      T = z;
      try {
        return q();
      } finally {
        T = ne;
      }
    }, i.unstable_scheduleCallback = function(z, q, ne) {
      var He = i.unstable_now();
      switch (typeof ne == "object" && ne !== null ? (ne = ne.delay, ne = typeof ne == "number" && 0 < ne ? He + ne : He) : ne = He, z) {
        case 1:
          var B = -1;
          break;
        case 2:
          B = 250;
          break;
        case 5:
          B = 1073741823;
          break;
        case 4:
          B = 1e4;
          break;
        default:
          B = 5e3;
      }
      return B = ne + B, z = {
        id: D++,
        callback: q,
        priorityLevel: z,
        startTime: ne,
        expirationTime: B,
        sortIndex: -1
      }, ne > He ? (z.sortIndex = ne, f(b, z), u(x) === null && z === u(b) && (Q ? (se(K), K = -1) : Q = !0, Qe(Le, ne - He))) : (z.sortIndex = B, f(x, z), G || L || (G = !0, J || (J = !0, $()))), z;
    }, i.unstable_shouldYield = tt, i.unstable_wrapCallback = function(z) {
      var q = T;
      return function() {
        var ne = T;
        T = q;
        try {
          return z.apply(this, arguments);
        } finally {
          T = ne;
        }
      };
    };
  })(ps)), ps;
}
var Hf;
function kh() {
  return Hf || (Hf = 1, ms.exports = Mh()), ms.exports;
}
var hs = { exports: {} }, St = {};
var Tf;
function zh() {
  if (Tf) return St;
  Tf = 1;
  var i = Ms();
  function f(x) {
    var b = "https://react.dev/errors/" + x;
    if (1 < arguments.length) {
      b += "?args[]=" + encodeURIComponent(arguments[1]);
      for (var D = 2; D < arguments.length; D++)
        b += "&args[]=" + encodeURIComponent(arguments[D]);
    }
    return "Minified React error #" + x + "; visit " + b + " for the full message or use the non-minified dev environment for full errors and additional helpful warnings.";
  }
  function u() {
  }
  var o = {
    d: {
      f: u,
      r: function() {
        throw Error(f(522));
      },
      D: u,
      C: u,
      L: u,
      m: u,
      X: u,
      S: u,
      M: u
    },
    p: 0,
    findDOMNode: null
  }, m = /* @__PURE__ */ Symbol.for("react.portal");
  function p(x, b, D) {
    var C = 3 < arguments.length && arguments[3] !== void 0 ? arguments[3] : null;
    return {
      $$typeof: m,
      key: C == null ? null : "" + C,
      children: x,
      containerInfo: b,
      implementation: D
    };
  }
  var h = i.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
  function v(x, b) {
    if (x === "font") return "";
    if (typeof b == "string")
      return b === "use-credentials" ? b : "";
  }
  return St.__DOM_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE = o, St.createPortal = function(x, b) {
    var D = 2 < arguments.length && arguments[2] !== void 0 ? arguments[2] : null;
    if (!b || b.nodeType !== 1 && b.nodeType !== 9 && b.nodeType !== 11)
      throw Error(f(299));
    return p(x, b, null, D);
  }, St.flushSync = function(x) {
    var b = h.T, D = o.p;
    try {
      if (h.T = null, o.p = 2, x) return x();
    } finally {
      h.T = b, o.p = D, o.d.f();
    }
  }, St.preconnect = function(x, b) {
    typeof x == "string" && (b ? (b = b.crossOrigin, b = typeof b == "string" ? b === "use-credentials" ? b : "" : void 0) : b = null, o.d.C(x, b));
  }, St.prefetchDNS = function(x) {
    typeof x == "string" && o.d.D(x);
  }, St.preinit = function(x, b) {
    if (typeof x == "string" && b && typeof b.as == "string") {
      var D = b.as, C = v(D, b.crossOrigin), T = typeof b.integrity == "string" ? b.integrity : void 0, L = typeof b.fetchPriority == "string" ? b.fetchPriority : void 0;
      D === "style" ? o.d.S(
        x,
        typeof b.precedence == "string" ? b.precedence : void 0,
        {
          crossOrigin: C,
          integrity: T,
          fetchPriority: L
        }
      ) : D === "script" && o.d.X(x, {
        crossOrigin: C,
        integrity: T,
        fetchPriority: L,
        nonce: typeof b.nonce == "string" ? b.nonce : void 0
      });
    }
  }, St.preinitModule = function(x, b) {
    if (typeof x == "string")
      if (typeof b == "object" && b !== null) {
        if (b.as == null || b.as === "script") {
          var D = v(
            b.as,
            b.crossOrigin
          );
          o.d.M(x, {
            crossOrigin: D,
            integrity: typeof b.integrity == "string" ? b.integrity : void 0,
            nonce: typeof b.nonce == "string" ? b.nonce : void 0
          });
        }
      } else b == null && o.d.M(x);
  }, St.preload = function(x, b) {
    if (typeof x == "string" && typeof b == "object" && b !== null && typeof b.as == "string") {
      var D = b.as, C = v(D, b.crossOrigin);
      o.d.L(x, D, {
        crossOrigin: C,
        integrity: typeof b.integrity == "string" ? b.integrity : void 0,
        nonce: typeof b.nonce == "string" ? b.nonce : void 0,
        type: typeof b.type == "string" ? b.type : void 0,
        fetchPriority: typeof b.fetchPriority == "string" ? b.fetchPriority : void 0,
        referrerPolicy: typeof b.referrerPolicy == "string" ? b.referrerPolicy : void 0,
        imageSrcSet: typeof b.imageSrcSet == "string" ? b.imageSrcSet : void 0,
        imageSizes: typeof b.imageSizes == "string" ? b.imageSizes : void 0,
        media: typeof b.media == "string" ? b.media : void 0
      });
    }
  }, St.preloadModule = function(x, b) {
    if (typeof x == "string")
      if (b) {
        var D = v(b.as, b.crossOrigin);
        o.d.m(x, {
          as: typeof b.as == "string" && b.as !== "script" ? b.as : void 0,
          crossOrigin: D,
          integrity: typeof b.integrity == "string" ? b.integrity : void 0
        });
      } else o.d.m(x);
  }, St.requestFormReset = function(x) {
    o.d.r(x);
  }, St.unstable_batchedUpdates = function(x, b) {
    return x(b);
  }, St.useFormState = function(x, b, D) {
    return h.H.useFormState(x, b, D);
  }, St.useFormStatus = function() {
    return h.H.useHostTransitionStatus();
  }, St.version = "19.2.3", St;
}
var Nf;
function dd() {
  if (Nf) return hs.exports;
  Nf = 1;
  function i() {
    if (!(typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ > "u" || typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE != "function"))
      try {
        __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE(i);
      } catch (f) {
        console.error(f);
      }
  }
  return i(), hs.exports = zh(), hs.exports;
}
var Cf;
function Hh() {
  if (Cf) return Mr;
  Cf = 1;
  var i = kh(), f = Ms(), u = dd();
  function o(e) {
    var t = "https://react.dev/errors/" + e;
    if (1 < arguments.length) {
      t += "?args[]=" + encodeURIComponent(arguments[1]);
      for (var a = 2; a < arguments.length; a++)
        t += "&args[]=" + encodeURIComponent(arguments[a]);
    }
    return "Minified React error #" + e + "; visit " + t + " for the full message or use the non-minified dev environment for full errors and additional helpful warnings.";
  }
  function m(e) {
    return !(!e || e.nodeType !== 1 && e.nodeType !== 9 && e.nodeType !== 11);
  }
  function p(e) {
    var t = e, a = e;
    if (e.alternate) for (; t.return; ) t = t.return;
    else {
      e = t;
      do
        t = e, (t.flags & 4098) !== 0 && (a = t.return), e = t.return;
      while (e);
    }
    return t.tag === 3 ? a : null;
  }
  function h(e) {
    if (e.tag === 13) {
      var t = e.memoizedState;
      if (t === null && (e = e.alternate, e !== null && (t = e.memoizedState)), t !== null) return t.dehydrated;
    }
    return null;
  }
  function v(e) {
    if (e.tag === 31) {
      var t = e.memoizedState;
      if (t === null && (e = e.alternate, e !== null && (t = e.memoizedState)), t !== null) return t.dehydrated;
    }
    return null;
  }
  function x(e) {
    if (p(e) !== e)
      throw Error(o(188));
  }
  function b(e) {
    var t = e.alternate;
    if (!t) {
      if (t = p(e), t === null) throw Error(o(188));
      return t !== e ? null : e;
    }
    for (var a = e, l = t; ; ) {
      var n = a.return;
      if (n === null) break;
      var r = n.alternate;
      if (r === null) {
        if (l = n.return, l !== null) {
          a = l;
          continue;
        }
        break;
      }
      if (n.child === r.child) {
        for (r = n.child; r; ) {
          if (r === a) return x(n), e;
          if (r === l) return x(n), t;
          r = r.sibling;
        }
        throw Error(o(188));
      }
      if (a.return !== l.return) a = n, l = r;
      else {
        for (var c = !1, d = n.child; d; ) {
          if (d === a) {
            c = !0, a = n, l = r;
            break;
          }
          if (d === l) {
            c = !0, l = n, a = r;
            break;
          }
          d = d.sibling;
        }
        if (!c) {
          for (d = r.child; d; ) {
            if (d === a) {
              c = !0, a = r, l = n;
              break;
            }
            if (d === l) {
              c = !0, l = r, a = n;
              break;
            }
            d = d.sibling;
          }
          if (!c) throw Error(o(189));
        }
      }
      if (a.alternate !== l) throw Error(o(190));
    }
    if (a.tag !== 3) throw Error(o(188));
    return a.stateNode.current === a ? e : t;
  }
  function D(e) {
    var t = e.tag;
    if (t === 5 || t === 26 || t === 27 || t === 6) return e;
    for (e = e.child; e !== null; ) {
      if (t = D(e), t !== null) return t;
      e = e.sibling;
    }
    return null;
  }
  var C = Object.assign, T = /* @__PURE__ */ Symbol.for("react.element"), L = /* @__PURE__ */ Symbol.for("react.transitional.element"), G = /* @__PURE__ */ Symbol.for("react.portal"), Q = /* @__PURE__ */ Symbol.for("react.fragment"), de = /* @__PURE__ */ Symbol.for("react.strict_mode"), fe = /* @__PURE__ */ Symbol.for("react.profiler"), se = /* @__PURE__ */ Symbol.for("react.consumer"), be = /* @__PURE__ */ Symbol.for("react.context"), Oe = /* @__PURE__ */ Symbol.for("react.forward_ref"), Le = /* @__PURE__ */ Symbol.for("react.suspense"), J = /* @__PURE__ */ Symbol.for("react.suspense_list"), K = /* @__PURE__ */ Symbol.for("react.memo"), U = /* @__PURE__ */ Symbol.for("react.lazy"), xe = /* @__PURE__ */ Symbol.for("react.activity"), tt = /* @__PURE__ */ Symbol.for("react.memo_cache_sentinel"), R = Symbol.iterator;
  function $(e) {
    return e === null || typeof e != "object" ? null : (e = R && e[R] || e["@@iterator"], typeof e == "function" ? e : null);
  }
  var ye = /* @__PURE__ */ Symbol.for("react.client.reference");
  function he(e) {
    if (e == null) return null;
    if (typeof e == "function")
      return e.$$typeof === ye ? null : e.displayName || e.name || null;
    if (typeof e == "string") return e;
    switch (e) {
      case Q:
        return "Fragment";
      case fe:
        return "Profiler";
      case de:
        return "StrictMode";
      case Le:
        return "Suspense";
      case J:
        return "SuspenseList";
      case xe:
        return "Activity";
    }
    if (typeof e == "object")
      switch (e.$$typeof) {
        case G:
          return "Portal";
        case be:
          return e.displayName || "Context";
        case se:
          return (e._context.displayName || "Context") + ".Consumer";
        case Oe:
          var t = e.render;
          return e = e.displayName, e || (e = t.displayName || t.name || "", e = e !== "" ? "ForwardRef(" + e + ")" : "ForwardRef"), e;
        case K:
          return t = e.displayName || null, t !== null ? t : he(e.type) || "Memo";
        case U:
          t = e._payload, e = e._init;
          try {
            return he(e(t));
          } catch {
          }
      }
    return null;
  }
  var Qe = Array.isArray, z = f.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE, q = u.__DOM_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE, ne = {
    pending: !1,
    data: null,
    method: null,
    action: null
  }, He = [], B = -1;
  function S(e) {
    return { current: e };
  }
  function _(e) {
    0 > B || (e.current = He[B], He[B] = null, B--);
  }
  function Y(e, t) {
    B++, He[B] = e.current, e.current = t;
  }
  var F = S(null), te = S(null), le = S(null), Se = S(null);
  function Ge(e, t) {
    switch (Y(le, t), Y(te, e), Y(F, null), t.nodeType) {
      case 9:
      case 11:
        e = (e = t.documentElement) && (e = e.namespaceURI) ? X1(e) : 0;
        break;
      default:
        if (e = t.tagName, t = t.namespaceURI)
          t = X1(t), e = Q1(t, e);
        else
          switch (e) {
            case "svg":
              e = 1;
              break;
            case "math":
              e = 2;
              break;
            default:
              e = 0;
          }
    }
    _(F), Y(F, e);
  }
  function me() {
    _(F), _(te), _(le);
  }
  function Ut(e) {
    e.memoizedState !== null && Y(Se, e);
    var t = F.current, a = Q1(t, e.type);
    t !== a && (Y(te, e), Y(F, a));
  }
  function Et(e) {
    te.current === e && (_(F), _(te)), Se.current === e && (_(Se), yr._currentValue = ne);
  }
  var vt, Ra;
  function Fe(e) {
    if (vt === void 0)
      try {
        throw Error();
      } catch (a) {
        var t = a.stack.trim().match(/\n( *(at )?)/);
        vt = t && t[1] || "", Ra = -1 < a.stack.indexOf(`
    at`) ? " (<anonymous>)" : -1 < a.stack.indexOf("@") ? "@unknown:0:0" : "";
      }
    return `
` + vt + e + Ra;
  }
  var Tn = !1;
  function Zl(e, t) {
    if (!e || Tn) return "";
    Tn = !0;
    var a = Error.prepareStackTrace;
    Error.prepareStackTrace = void 0;
    try {
      var l = {
        DetermineComponentFrameRoot: function() {
          try {
            if (t) {
              var O = function() {
                throw Error();
              };
              if (Object.defineProperty(O.prototype, "props", {
                set: function() {
                  throw Error();
                }
              }), typeof Reflect == "object" && Reflect.construct) {
                try {
                  Reflect.construct(O, []);
                } catch (H) {
                  var k = H;
                }
                Reflect.construct(e, [], O);
              } else {
                try {
                  O.call();
                } catch (H) {
                  k = H;
                }
                e.call(O.prototype);
              }
            } else {
              try {
                throw Error();
              } catch (H) {
                k = H;
              }
              (O = e()) && typeof O.catch == "function" && O.catch(function() {
              });
            }
          } catch (H) {
            if (H && k && typeof H.stack == "string")
              return [H.stack, k.stack];
          }
          return [null, null];
        }
      };
      l.DetermineComponentFrameRoot.displayName = "DetermineComponentFrameRoot";
      var n = Object.getOwnPropertyDescriptor(
        l.DetermineComponentFrameRoot,
        "name"
      );
      n && n.configurable && Object.defineProperty(
        l.DetermineComponentFrameRoot,
        "name",
        { value: "DetermineComponentFrameRoot" }
      );
      var r = l.DetermineComponentFrameRoot(), c = r[0], d = r[1];
      if (c && d) {
        var y = c.split(`
`), M = d.split(`
`);
        for (n = l = 0; l < y.length && !y[l].includes("DetermineComponentFrameRoot"); )
          l++;
        for (; n < M.length && !M[n].includes(
          "DetermineComponentFrameRoot"
        ); )
          n++;
        if (l === y.length || n === M.length)
          for (l = y.length - 1, n = M.length - 1; 1 <= l && 0 <= n && y[l] !== M[n]; )
            n--;
        for (; 1 <= l && 0 <= n; l--, n--)
          if (y[l] !== M[n]) {
            if (l !== 1 || n !== 1)
              do
                if (l--, n--, 0 > n || y[l] !== M[n]) {
                  var N = `
` + y[l].replace(" at new ", " at ");
                  return e.displayName && N.includes("<anonymous>") && (N = N.replace("<anonymous>", e.displayName)), N;
                }
              while (1 <= l && 0 <= n);
            break;
          }
      }
    } finally {
      Tn = !1, Error.prepareStackTrace = a;
    }
    return (a = e ? e.displayName || e.name : "") ? Fe(a) : "";
  }
  function qt(e, t) {
    switch (e.tag) {
      case 26:
      case 27:
      case 5:
        return Fe(e.type);
      case 16:
        return Fe("Lazy");
      case 13:
        return e.child !== t && t !== null ? Fe("Suspense Fallback") : Fe("Suspense");
      case 19:
        return Fe("SuspenseList");
      case 0:
      case 15:
        return Zl(e.type, !1);
      case 11:
        return Zl(e.type.render, !1);
      case 1:
        return Zl(e.type, !0);
      case 31:
        return Fe("Activity");
      default:
        return "";
    }
  }
  function Pt(e) {
    try {
      var t = "", a = null;
      do
        t += qt(e, a), a = e, e = e.return;
      while (e);
      return t;
    } catch (l) {
      return `
Error generating stack: ` + l.message + `
` + l.stack;
    }
  }
  var Dl = Object.prototype.hasOwnProperty, Rl = i.unstable_scheduleCallback, Vl = i.unstable_cancelCallback, Cr = i.unstable_shouldYield, Ll = i.unstable_requestPaint, Ke = i.unstable_now, Nn = i.unstable_getCurrentPriorityLevel, _r = i.unstable_ImmediatePriority, Ul = i.unstable_UserBlockingPriority, ea = i.unstable_NormalPriority, jr = i.unstable_LowPriority, Cn = i.unstable_IdlePriority, _n = i.log, jn = i.unstable_setDisableYieldValue, ha = null, xt = null;
  function oa(e) {
    if (typeof _n == "function" && jn(e), xt && typeof xt.setStrictMode == "function")
      try {
        xt.setStrictMode(ha, e);
      } catch {
      }
  }
  var yt = Math.clz32 ? Math.clz32 : ie, Z = Math.log, V = Math.LN2;
  function ie(e) {
    return e >>>= 0, e === 0 ? 32 : 31 - (Z(e) / V | 0) | 0;
  }
  var X = 256, re = 262144, ue = 4194304;
  function Ze(e) {
    var t = e & 42;
    if (t !== 0) return t;
    switch (e & -e) {
      case 1:
        return 1;
      case 2:
        return 2;
      case 4:
        return 4;
      case 8:
        return 8;
      case 16:
        return 16;
      case 32:
        return 32;
      case 64:
        return 64;
      case 128:
        return 128;
      case 256:
      case 512:
      case 1024:
      case 2048:
      case 4096:
      case 8192:
      case 16384:
      case 32768:
      case 65536:
      case 131072:
        return e & 261888;
      case 262144:
      case 524288:
      case 1048576:
      case 2097152:
        return e & 3932160;
      case 4194304:
      case 8388608:
      case 16777216:
      case 33554432:
        return e & 62914560;
      case 67108864:
        return 67108864;
      case 134217728:
        return 134217728;
      case 268435456:
        return 268435456;
      case 536870912:
        return 536870912;
      case 1073741824:
        return 0;
      default:
        return e;
    }
  }
  function ee(e, t, a) {
    var l = e.pendingLanes;
    if (l === 0) return 0;
    var n = 0, r = e.suspendedLanes, c = e.pingedLanes;
    e = e.warmLanes;
    var d = l & 134217727;
    return d !== 0 ? (l = d & ~r, l !== 0 ? n = Ze(l) : (c &= d, c !== 0 ? n = Ze(c) : a || (a = d & ~e, a !== 0 && (n = Ze(a))))) : (d = l & ~r, d !== 0 ? n = Ze(d) : c !== 0 ? n = Ze(c) : a || (a = l & ~e, a !== 0 && (n = Ze(a)))), n === 0 ? 0 : t !== 0 && t !== n && (t & r) === 0 && (r = n & -n, a = t & -t, r >= a || r === 32 && (a & 4194048) !== 0) ? t : n;
  }
  function Ye(e, t) {
    return (e.pendingLanes & ~(e.suspendedLanes & ~e.pingedLanes) & t) === 0;
  }
  function De(e, t) {
    switch (e) {
      case 1:
      case 2:
      case 4:
      case 8:
      case 64:
        return t + 250;
      case 16:
      case 32:
      case 128:
      case 256:
      case 512:
      case 1024:
      case 2048:
      case 4096:
      case 8192:
      case 16384:
      case 32768:
      case 65536:
      case 131072:
      case 262144:
      case 524288:
      case 1048576:
      case 2097152:
        return t + 5e3;
      case 4194304:
      case 8388608:
      case 16777216:
      case 33554432:
        return -1;
      case 67108864:
      case 134217728:
      case 268435456:
      case 536870912:
      case 1073741824:
        return -1;
      default:
        return -1;
    }
  }
  function Bt() {
    var e = ue;
    return ue <<= 1, (ue & 62914560) === 0 && (ue = 4194304), e;
  }
  function ta(e) {
    for (var t = [], a = 0; 31 > a; a++) t.push(e);
    return t;
  }
  function kt(e, t) {
    e.pendingLanes |= t, t !== 268435456 && (e.suspendedLanes = 0, e.pingedLanes = 0, e.warmLanes = 0);
  }
  function wt(e, t, a, l, n, r) {
    var c = e.pendingLanes;
    e.pendingLanes = a, e.suspendedLanes = 0, e.pingedLanes = 0, e.warmLanes = 0, e.expiredLanes &= a, e.entangledLanes &= a, e.errorRecoveryDisabledLanes &= a, e.shellSuspendCounter = 0;
    var d = e.entanglements, y = e.expirationTimes, M = e.hiddenUpdates;
    for (a = c & ~a; 0 < a; ) {
      var N = 31 - yt(a), O = 1 << N;
      d[N] = 0, y[N] = -1;
      var k = M[N];
      if (k !== null)
        for (M[N] = null, N = 0; N < k.length; N++) {
          var H = k[N];
          H !== null && (H.lane &= -536870913);
        }
      a &= ~O;
    }
    l !== 0 && ca(e, l, 0), r !== 0 && n === 0 && e.tag !== 0 && (e.suspendedLanes |= r & ~(c & ~t));
  }
  function ca(e, t, a) {
    e.pendingLanes |= t, e.suspendedLanes &= ~t;
    var l = 31 - yt(t);
    e.entangledLanes |= t, e.entanglements[l] = e.entanglements[l] | 1073741824 | a & 261930;
  }
  function ga(e, t) {
    var a = e.entangledLanes |= t;
    for (e = e.entanglements; a; ) {
      var l = 31 - yt(a), n = 1 << l;
      n & t | e[l] & t && (e[l] |= t), a &= ~n;
    }
  }
  function Or(e, t) {
    var a = t & -t;
    return a = (a & 42) !== 0 ? 1 : ul(a), (a & (e.suspendedLanes | t)) !== 0 ? 0 : a;
  }
  function ul(e) {
    switch (e) {
      case 2:
        e = 1;
        break;
      case 8:
        e = 4;
        break;
      case 32:
        e = 16;
        break;
      case 256:
      case 512:
      case 1024:
      case 2048:
      case 4096:
      case 8192:
      case 16384:
      case 32768:
      case 65536:
      case 131072:
      case 262144:
      case 524288:
      case 1048576:
      case 2097152:
      case 4194304:
      case 8388608:
      case 16777216:
      case 33554432:
        e = 128;
        break;
      case 268435456:
        e = 134217728;
        break;
      default:
        e = 0;
    }
    return e;
  }
  function fl(e) {
    return e &= -e, 2 < e ? 8 < e ? (e & 134217727) !== 0 ? 32 : 268435456 : 8 : 2;
  }
  function On() {
    var e = q.p;
    return e !== 0 ? e : (e = window.event, e === void 0 ? 32 : gf(e.type));
  }
  function dl(e, t) {
    var a = q.p;
    try {
      return q.p = e, t();
    } finally {
      q.p = a;
    }
  }
  var aa = Math.random().toString(36).slice(2), Ie = "__reactFiber$" + aa, mt = "__reactProps$" + aa, Te = "__reactContainer$" + aa, At = "__reactEvents$" + aa, ql = "__reactListeners$" + aa, dm = "__reactHandles$" + aa, _s = "__reactResources$" + aa, Zn = "__reactMarker$" + aa;
  function to(e) {
    delete e[Ie], delete e[mt], delete e[At], delete e[ql], delete e[dm];
  }
  function Bl(e) {
    var t = e[Ie];
    if (t) return t;
    for (var a = e.parentNode; a; ) {
      if (t = a[Te] || a[Ie]) {
        if (a = t.alternate, t.child !== null || a !== null && a.child !== null)
          for (e = P1(e); e !== null; ) {
            if (a = e[Ie]) return a;
            e = P1(e);
          }
        return t;
      }
      e = a, a = e.parentNode;
    }
    return null;
  }
  function Gl(e) {
    if (e = e[Ie] || e[Te]) {
      var t = e.tag;
      if (t === 5 || t === 6 || t === 13 || t === 31 || t === 26 || t === 27 || t === 3)
        return e;
    }
    return null;
  }
  function Dn(e) {
    var t = e.tag;
    if (t === 5 || t === 26 || t === 27 || t === 6) return e.stateNode;
    throw Error(o(33));
  }
  function Yl(e) {
    var t = e[_s];
    return t || (t = e[_s] = { hoistableStyles: /* @__PURE__ */ new Map(), hoistableScripts: /* @__PURE__ */ new Map() }), t;
  }
  function ft(e) {
    e[Zn] = !0;
  }
  var js = /* @__PURE__ */ new Set(), Os = {};
  function ml(e, t) {
    Xl(e, t), Xl(e + "Capture", t);
  }
  function Xl(e, t) {
    for (Os[e] = t, e = 0; e < t.length; e++)
      js.add(t[e]);
  }
  var mm = RegExp(
    "^[:A-Z_a-z\\u00C0-\\u00D6\\u00D8-\\u00F6\\u00F8-\\u02FF\\u0370-\\u037D\\u037F-\\u1FFF\\u200C-\\u200D\\u2070-\\u218F\\u2C00-\\u2FEF\\u3001-\\uD7FF\\uF900-\\uFDCF\\uFDF0-\\uFFFD][:A-Z_a-z\\u00C0-\\u00D6\\u00D8-\\u00F6\\u00F8-\\u02FF\\u0370-\\u037D\\u037F-\\u1FFF\\u200C-\\u200D\\u2070-\\u218F\\u2C00-\\u2FEF\\u3001-\\uD7FF\\uF900-\\uFDCF\\uFDF0-\\uFFFD\\-.0-9\\u00B7\\u0300-\\u036F\\u203F-\\u2040]*$"
  ), Zs = {}, Ds = {};
  function pm(e) {
    return Dl.call(Ds, e) ? !0 : Dl.call(Zs, e) ? !1 : mm.test(e) ? Ds[e] = !0 : (Zs[e] = !0, !1);
  }
  function Zr(e, t, a) {
    if (pm(t))
      if (a === null) e.removeAttribute(t);
      else {
        switch (typeof a) {
          case "undefined":
          case "function":
          case "symbol":
            e.removeAttribute(t);
            return;
          case "boolean":
            var l = t.toLowerCase().slice(0, 5);
            if (l !== "data-" && l !== "aria-") {
              e.removeAttribute(t);
              return;
            }
        }
        e.setAttribute(t, "" + a);
      }
  }
  function Dr(e, t, a) {
    if (a === null) e.removeAttribute(t);
    else {
      switch (typeof a) {
        case "undefined":
        case "function":
        case "symbol":
        case "boolean":
          e.removeAttribute(t);
          return;
      }
      e.setAttribute(t, "" + a);
    }
  }
  function ba(e, t, a, l) {
    if (l === null) e.removeAttribute(a);
    else {
      switch (typeof l) {
        case "undefined":
        case "function":
        case "symbol":
        case "boolean":
          e.removeAttribute(a);
          return;
      }
      e.setAttributeNS(t, a, "" + l);
    }
  }
  function Gt(e) {
    switch (typeof e) {
      case "bigint":
      case "boolean":
      case "number":
      case "string":
      case "undefined":
        return e;
      case "object":
        return e;
      default:
        return "";
    }
  }
  function Rs(e) {
    var t = e.type;
    return (e = e.nodeName) && e.toLowerCase() === "input" && (t === "checkbox" || t === "radio");
  }
  function hm(e, t, a) {
    var l = Object.getOwnPropertyDescriptor(
      e.constructor.prototype,
      t
    );
    if (!e.hasOwnProperty(t) && typeof l < "u" && typeof l.get == "function" && typeof l.set == "function") {
      var n = l.get, r = l.set;
      return Object.defineProperty(e, t, {
        configurable: !0,
        get: function() {
          return n.call(this);
        },
        set: function(c) {
          a = "" + c, r.call(this, c);
        }
      }), Object.defineProperty(e, t, {
        enumerable: l.enumerable
      }), {
        getValue: function() {
          return a;
        },
        setValue: function(c) {
          a = "" + c;
        },
        stopTracking: function() {
          e._valueTracker = null, delete e[t];
        }
      };
    }
  }
  function ao(e) {
    if (!e._valueTracker) {
      var t = Rs(e) ? "checked" : "value";
      e._valueTracker = hm(
        e,
        t,
        "" + e[t]
      );
    }
  }
  function Vs(e) {
    if (!e) return !1;
    var t = e._valueTracker;
    if (!t) return !0;
    var a = t.getValue(), l = "";
    return e && (l = Rs(e) ? e.checked ? "true" : "false" : e.value), e = l, e !== a ? (t.setValue(e), !0) : !1;
  }
  function Rr(e) {
    if (e = e || (typeof document < "u" ? document : void 0), typeof e > "u") return null;
    try {
      return e.activeElement || e.body;
    } catch {
      return e.body;
    }
  }
  var gm = /[\n"\\]/g;
  function Yt(e) {
    return e.replace(
      gm,
      function(t) {
        return "\\" + t.charCodeAt(0).toString(16) + " ";
      }
    );
  }
  function lo(e, t, a, l, n, r, c, d) {
    e.name = "", c != null && typeof c != "function" && typeof c != "symbol" && typeof c != "boolean" ? e.type = c : e.removeAttribute("type"), t != null ? c === "number" ? (t === 0 && e.value === "" || e.value != t) && (e.value = "" + Gt(t)) : e.value !== "" + Gt(t) && (e.value = "" + Gt(t)) : c !== "submit" && c !== "reset" || e.removeAttribute("value"), t != null ? no(e, c, Gt(t)) : a != null ? no(e, c, Gt(a)) : l != null && e.removeAttribute("value"), n == null && r != null && (e.defaultChecked = !!r), n != null && (e.checked = n && typeof n != "function" && typeof n != "symbol"), d != null && typeof d != "function" && typeof d != "symbol" && typeof d != "boolean" ? e.name = "" + Gt(d) : e.removeAttribute("name");
  }
  function Ls(e, t, a, l, n, r, c, d) {
    if (r != null && typeof r != "function" && typeof r != "symbol" && typeof r != "boolean" && (e.type = r), t != null || a != null) {
      if (!(r !== "submit" && r !== "reset" || t != null)) {
        ao(e);
        return;
      }
      a = a != null ? "" + Gt(a) : "", t = t != null ? "" + Gt(t) : a, d || t === e.value || (e.value = t), e.defaultValue = t;
    }
    l = l ?? n, l = typeof l != "function" && typeof l != "symbol" && !!l, e.checked = d ? e.checked : !!l, e.defaultChecked = !!l, c != null && typeof c != "function" && typeof c != "symbol" && typeof c != "boolean" && (e.name = c), ao(e);
  }
  function no(e, t, a) {
    t === "number" && Rr(e.ownerDocument) === e || e.defaultValue === "" + a || (e.defaultValue = "" + a);
  }
  function Ql(e, t, a, l) {
    if (e = e.options, t) {
      t = {};
      for (var n = 0; n < a.length; n++)
        t["$" + a[n]] = !0;
      for (a = 0; a < e.length; a++)
        n = t.hasOwnProperty("$" + e[a].value), e[a].selected !== n && (e[a].selected = n), n && l && (e[a].defaultSelected = !0);
    } else {
      for (a = "" + Gt(a), t = null, n = 0; n < e.length; n++) {
        if (e[n].value === a) {
          e[n].selected = !0, l && (e[n].defaultSelected = !0);
          return;
        }
        t !== null || e[n].disabled || (t = e[n]);
      }
      t !== null && (t.selected = !0);
    }
  }
  function Us(e, t, a) {
    if (t != null && (t = "" + Gt(t), t !== e.value && (e.value = t), a == null)) {
      e.defaultValue !== t && (e.defaultValue = t);
      return;
    }
    e.defaultValue = a != null ? "" + Gt(a) : "";
  }
  function qs(e, t, a, l) {
    if (t == null) {
      if (l != null) {
        if (a != null) throw Error(o(92));
        if (Qe(l)) {
          if (1 < l.length) throw Error(o(93));
          l = l[0];
        }
        a = l;
      }
      a == null && (a = ""), t = a;
    }
    a = Gt(t), e.defaultValue = a, l = e.textContent, l === a && l !== "" && l !== null && (e.value = l), ao(e);
  }
  function Kl(e, t) {
    if (t) {
      var a = e.firstChild;
      if (a && a === e.lastChild && a.nodeType === 3) {
        a.nodeValue = t;
        return;
      }
    }
    e.textContent = t;
  }
  var bm = new Set(
    "animationIterationCount aspectRatio borderImageOutset borderImageSlice borderImageWidth boxFlex boxFlexGroup boxOrdinalGroup columnCount columns flex flexGrow flexPositive flexShrink flexNegative flexOrder gridArea gridRow gridRowEnd gridRowSpan gridRowStart gridColumn gridColumnEnd gridColumnSpan gridColumnStart fontWeight lineClamp lineHeight opacity order orphans scale tabSize widows zIndex zoom fillOpacity floodOpacity stopOpacity strokeDasharray strokeDashoffset strokeMiterlimit strokeOpacity strokeWidth MozAnimationIterationCount MozBoxFlex MozBoxFlexGroup MozLineClamp msAnimationIterationCount msFlex msZoom msFlexGrow msFlexNegative msFlexOrder msFlexPositive msFlexShrink msGridColumn msGridColumnSpan msGridRow msGridRowSpan WebkitAnimationIterationCount WebkitBoxFlex WebKitBoxFlexGroup WebkitBoxOrdinalGroup WebkitColumnCount WebkitColumns WebkitFlex WebkitFlexGrow WebkitFlexPositive WebkitFlexShrink WebkitLineClamp".split(
      " "
    )
  );
  function Bs(e, t, a) {
    var l = t.indexOf("--") === 0;
    a == null || typeof a == "boolean" || a === "" ? l ? e.setProperty(t, "") : t === "float" ? e.cssFloat = "" : e[t] = "" : l ? e.setProperty(t, a) : typeof a != "number" || a === 0 || bm.has(t) ? t === "float" ? e.cssFloat = a : e[t] = ("" + a).trim() : e[t] = a + "px";
  }
  function Gs(e, t, a) {
    if (t != null && typeof t != "object")
      throw Error(o(62));
    if (e = e.style, a != null) {
      for (var l in a)
        !a.hasOwnProperty(l) || t != null && t.hasOwnProperty(l) || (l.indexOf("--") === 0 ? e.setProperty(l, "") : l === "float" ? e.cssFloat = "" : e[l] = "");
      for (var n in t)
        l = t[n], t.hasOwnProperty(n) && a[n] !== l && Bs(e, n, l);
    } else
      for (var r in t)
        t.hasOwnProperty(r) && Bs(e, r, t[r]);
  }
  function ro(e) {
    if (e.indexOf("-") === -1) return !1;
    switch (e) {
      case "annotation-xml":
      case "color-profile":
      case "font-face":
      case "font-face-src":
      case "font-face-uri":
      case "font-face-format":
      case "font-face-name":
      case "missing-glyph":
        return !1;
      default:
        return !0;
    }
  }
  var vm = /* @__PURE__ */ new Map([
    ["acceptCharset", "accept-charset"],
    ["htmlFor", "for"],
    ["httpEquiv", "http-equiv"],
    ["crossOrigin", "crossorigin"],
    ["accentHeight", "accent-height"],
    ["alignmentBaseline", "alignment-baseline"],
    ["arabicForm", "arabic-form"],
    ["baselineShift", "baseline-shift"],
    ["capHeight", "cap-height"],
    ["clipPath", "clip-path"],
    ["clipRule", "clip-rule"],
    ["colorInterpolation", "color-interpolation"],
    ["colorInterpolationFilters", "color-interpolation-filters"],
    ["colorProfile", "color-profile"],
    ["colorRendering", "color-rendering"],
    ["dominantBaseline", "dominant-baseline"],
    ["enableBackground", "enable-background"],
    ["fillOpacity", "fill-opacity"],
    ["fillRule", "fill-rule"],
    ["floodColor", "flood-color"],
    ["floodOpacity", "flood-opacity"],
    ["fontFamily", "font-family"],
    ["fontSize", "font-size"],
    ["fontSizeAdjust", "font-size-adjust"],
    ["fontStretch", "font-stretch"],
    ["fontStyle", "font-style"],
    ["fontVariant", "font-variant"],
    ["fontWeight", "font-weight"],
    ["glyphName", "glyph-name"],
    ["glyphOrientationHorizontal", "glyph-orientation-horizontal"],
    ["glyphOrientationVertical", "glyph-orientation-vertical"],
    ["horizAdvX", "horiz-adv-x"],
    ["horizOriginX", "horiz-origin-x"],
    ["imageRendering", "image-rendering"],
    ["letterSpacing", "letter-spacing"],
    ["lightingColor", "lighting-color"],
    ["markerEnd", "marker-end"],
    ["markerMid", "marker-mid"],
    ["markerStart", "marker-start"],
    ["overlinePosition", "overline-position"],
    ["overlineThickness", "overline-thickness"],
    ["paintOrder", "paint-order"],
    ["panose-1", "panose-1"],
    ["pointerEvents", "pointer-events"],
    ["renderingIntent", "rendering-intent"],
    ["shapeRendering", "shape-rendering"],
    ["stopColor", "stop-color"],
    ["stopOpacity", "stop-opacity"],
    ["strikethroughPosition", "strikethrough-position"],
    ["strikethroughThickness", "strikethrough-thickness"],
    ["strokeDasharray", "stroke-dasharray"],
    ["strokeDashoffset", "stroke-dashoffset"],
    ["strokeLinecap", "stroke-linecap"],
    ["strokeLinejoin", "stroke-linejoin"],
    ["strokeMiterlimit", "stroke-miterlimit"],
    ["strokeOpacity", "stroke-opacity"],
    ["strokeWidth", "stroke-width"],
    ["textAnchor", "text-anchor"],
    ["textDecoration", "text-decoration"],
    ["textRendering", "text-rendering"],
    ["transformOrigin", "transform-origin"],
    ["underlinePosition", "underline-position"],
    ["underlineThickness", "underline-thickness"],
    ["unicodeBidi", "unicode-bidi"],
    ["unicodeRange", "unicode-range"],
    ["unitsPerEm", "units-per-em"],
    ["vAlphabetic", "v-alphabetic"],
    ["vHanging", "v-hanging"],
    ["vIdeographic", "v-ideographic"],
    ["vMathematical", "v-mathematical"],
    ["vectorEffect", "vector-effect"],
    ["vertAdvY", "vert-adv-y"],
    ["vertOriginX", "vert-origin-x"],
    ["vertOriginY", "vert-origin-y"],
    ["wordSpacing", "word-spacing"],
    ["writingMode", "writing-mode"],
    ["xmlnsXlink", "xmlns:xlink"],
    ["xHeight", "x-height"]
  ]), xm = /^[\u0000-\u001F ]*j[\r\n\t]*a[\r\n\t]*v[\r\n\t]*a[\r\n\t]*s[\r\n\t]*c[\r\n\t]*r[\r\n\t]*i[\r\n\t]*p[\r\n\t]*t[\r\n\t]*:/i;
  function Vr(e) {
    return xm.test("" + e) ? "javascript:throw new Error('React has blocked a javascript: URL as a security precaution.')" : e;
  }
  function va() {
  }
  var io = null;
  function oo(e) {
    return e = e.target || e.srcElement || window, e.correspondingUseElement && (e = e.correspondingUseElement), e.nodeType === 3 ? e.parentNode : e;
  }
  var Jl = null, $l = null;
  function Ys(e) {
    var t = Gl(e);
    if (t && (e = t.stateNode)) {
      var a = e[mt] || null;
      e: switch (e = t.stateNode, t.type) {
        case "input":
          if (lo(
            e,
            a.value,
            a.defaultValue,
            a.defaultValue,
            a.checked,
            a.defaultChecked,
            a.type,
            a.name
          ), t = a.name, a.type === "radio" && t != null) {
            for (a = e; a.parentNode; ) a = a.parentNode;
            for (a = a.querySelectorAll(
              'input[name="' + Yt(
                "" + t
              ) + '"][type="radio"]'
            ), t = 0; t < a.length; t++) {
              var l = a[t];
              if (l !== e && l.form === e.form) {
                var n = l[mt] || null;
                if (!n) throw Error(o(90));
                lo(
                  l,
                  n.value,
                  n.defaultValue,
                  n.defaultValue,
                  n.checked,
                  n.defaultChecked,
                  n.type,
                  n.name
                );
              }
            }
            for (t = 0; t < a.length; t++)
              l = a[t], l.form === e.form && Vs(l);
          }
          break e;
        case "textarea":
          Us(e, a.value, a.defaultValue);
          break e;
        case "select":
          t = a.value, t != null && Ql(e, !!a.multiple, t, !1);
      }
    }
  }
  var co = !1;
  function Xs(e, t, a) {
    if (co) return e(t, a);
    co = !0;
    try {
      var l = e(t);
      return l;
    } finally {
      if (co = !1, (Jl !== null || $l !== null) && (Mi(), Jl && (t = Jl, e = $l, $l = Jl = null, Ys(t), e)))
        for (t = 0; t < e.length; t++) Ys(e[t]);
    }
  }
  function Rn(e, t) {
    var a = e.stateNode;
    if (a === null) return null;
    var l = a[mt] || null;
    if (l === null) return null;
    a = l[t];
    e: switch (t) {
      case "onClick":
      case "onClickCapture":
      case "onDoubleClick":
      case "onDoubleClickCapture":
      case "onMouseDown":
      case "onMouseDownCapture":
      case "onMouseMove":
      case "onMouseMoveCapture":
      case "onMouseUp":
      case "onMouseUpCapture":
      case "onMouseEnter":
        (l = !l.disabled) || (e = e.type, l = !(e === "button" || e === "input" || e === "select" || e === "textarea")), e = !l;
        break e;
      default:
        e = !1;
    }
    if (e) return null;
    if (a && typeof a != "function")
      throw Error(
        o(231, t, typeof a)
      );
    return a;
  }
  var xa = !(typeof window > "u" || typeof window.document > "u" || typeof window.document.createElement > "u"), so = !1;
  if (xa)
    try {
      var Vn = {};
      Object.defineProperty(Vn, "passive", {
        get: function() {
          so = !0;
        }
      }), window.addEventListener("test", Vn, Vn), window.removeEventListener("test", Vn, Vn);
    } catch {
      so = !1;
    }
  var Va = null, uo = null, Lr = null;
  function Qs() {
    if (Lr) return Lr;
    var e, t = uo, a = t.length, l, n = "value" in Va ? Va.value : Va.textContent, r = n.length;
    for (e = 0; e < a && t[e] === n[e]; e++) ;
    var c = a - e;
    for (l = 1; l <= c && t[a - l] === n[r - l]; l++) ;
    return Lr = n.slice(e, 1 < l ? 1 - l : void 0);
  }
  function Ur(e) {
    var t = e.keyCode;
    return "charCode" in e ? (e = e.charCode, e === 0 && t === 13 && (e = 13)) : e = t, e === 10 && (e = 13), 32 <= e || e === 13 ? e : 0;
  }
  function qr() {
    return !0;
  }
  function Ks() {
    return !1;
  }
  function zt(e) {
    function t(a, l, n, r, c) {
      this._reactName = a, this._targetInst = n, this.type = l, this.nativeEvent = r, this.target = c, this.currentTarget = null;
      for (var d in e)
        e.hasOwnProperty(d) && (a = e[d], this[d] = a ? a(r) : r[d]);
      return this.isDefaultPrevented = (r.defaultPrevented != null ? r.defaultPrevented : r.returnValue === !1) ? qr : Ks, this.isPropagationStopped = Ks, this;
    }
    return C(t.prototype, {
      preventDefault: function() {
        this.defaultPrevented = !0;
        var a = this.nativeEvent;
        a && (a.preventDefault ? a.preventDefault() : typeof a.returnValue != "unknown" && (a.returnValue = !1), this.isDefaultPrevented = qr);
      },
      stopPropagation: function() {
        var a = this.nativeEvent;
        a && (a.stopPropagation ? a.stopPropagation() : typeof a.cancelBubble != "unknown" && (a.cancelBubble = !0), this.isPropagationStopped = qr);
      },
      persist: function() {
      },
      isPersistent: qr
    }), t;
  }
  var pl = {
    eventPhase: 0,
    bubbles: 0,
    cancelable: 0,
    timeStamp: function(e) {
      return e.timeStamp || Date.now();
    },
    defaultPrevented: 0,
    isTrusted: 0
  }, Br = zt(pl), Ln = C({}, pl, { view: 0, detail: 0 }), ym = zt(Ln), fo, mo, Un, Gr = C({}, Ln, {
    screenX: 0,
    screenY: 0,
    clientX: 0,
    clientY: 0,
    pageX: 0,
    pageY: 0,
    ctrlKey: 0,
    shiftKey: 0,
    altKey: 0,
    metaKey: 0,
    getModifierState: ho,
    button: 0,
    buttons: 0,
    relatedTarget: function(e) {
      return e.relatedTarget === void 0 ? e.fromElement === e.srcElement ? e.toElement : e.fromElement : e.relatedTarget;
    },
    movementX: function(e) {
      return "movementX" in e ? e.movementX : (e !== Un && (Un && e.type === "mousemove" ? (fo = e.screenX - Un.screenX, mo = e.screenY - Un.screenY) : mo = fo = 0, Un = e), fo);
    },
    movementY: function(e) {
      return "movementY" in e ? e.movementY : mo;
    }
  }), Js = zt(Gr), wm = C({}, Gr, { dataTransfer: 0 }), Sm = zt(wm), Em = C({}, Ln, { relatedTarget: 0 }), po = zt(Em), Am = C({}, pl, {
    animationName: 0,
    elapsedTime: 0,
    pseudoElement: 0
  }), Mm = zt(Am), km = C({}, pl, {
    clipboardData: function(e) {
      return "clipboardData" in e ? e.clipboardData : window.clipboardData;
    }
  }), zm = zt(km), Hm = C({}, pl, { data: 0 }), $s = zt(Hm), Tm = {
    Esc: "Escape",
    Spacebar: " ",
    Left: "ArrowLeft",
    Up: "ArrowUp",
    Right: "ArrowRight",
    Down: "ArrowDown",
    Del: "Delete",
    Win: "OS",
    Menu: "ContextMenu",
    Apps: "ContextMenu",
    Scroll: "ScrollLock",
    MozPrintableKey: "Unidentified"
  }, Nm = {
    8: "Backspace",
    9: "Tab",
    12: "Clear",
    13: "Enter",
    16: "Shift",
    17: "Control",
    18: "Alt",
    19: "Pause",
    20: "CapsLock",
    27: "Escape",
    32: " ",
    33: "PageUp",
    34: "PageDown",
    35: "End",
    36: "Home",
    37: "ArrowLeft",
    38: "ArrowUp",
    39: "ArrowRight",
    40: "ArrowDown",
    45: "Insert",
    46: "Delete",
    112: "F1",
    113: "F2",
    114: "F3",
    115: "F4",
    116: "F5",
    117: "F6",
    118: "F7",
    119: "F8",
    120: "F9",
    121: "F10",
    122: "F11",
    123: "F12",
    144: "NumLock",
    145: "ScrollLock",
    224: "Meta"
  }, Cm = {
    Alt: "altKey",
    Control: "ctrlKey",
    Meta: "metaKey",
    Shift: "shiftKey"
  };
  function _m(e) {
    var t = this.nativeEvent;
    return t.getModifierState ? t.getModifierState(e) : (e = Cm[e]) ? !!t[e] : !1;
  }
  function ho() {
    return _m;
  }
  var jm = C({}, Ln, {
    key: function(e) {
      if (e.key) {
        var t = Tm[e.key] || e.key;
        if (t !== "Unidentified") return t;
      }
      return e.type === "keypress" ? (e = Ur(e), e === 13 ? "Enter" : String.fromCharCode(e)) : e.type === "keydown" || e.type === "keyup" ? Nm[e.keyCode] || "Unidentified" : "";
    },
    code: 0,
    location: 0,
    ctrlKey: 0,
    shiftKey: 0,
    altKey: 0,
    metaKey: 0,
    repeat: 0,
    locale: 0,
    getModifierState: ho,
    charCode: function(e) {
      return e.type === "keypress" ? Ur(e) : 0;
    },
    keyCode: function(e) {
      return e.type === "keydown" || e.type === "keyup" ? e.keyCode : 0;
    },
    which: function(e) {
      return e.type === "keypress" ? Ur(e) : e.type === "keydown" || e.type === "keyup" ? e.keyCode : 0;
    }
  }), Om = zt(jm), Zm = C({}, Gr, {
    pointerId: 0,
    width: 0,
    height: 0,
    pressure: 0,
    tangentialPressure: 0,
    tiltX: 0,
    tiltY: 0,
    twist: 0,
    pointerType: 0,
    isPrimary: 0
  }), Fs = zt(Zm), Dm = C({}, Ln, {
    touches: 0,
    targetTouches: 0,
    changedTouches: 0,
    altKey: 0,
    metaKey: 0,
    ctrlKey: 0,
    shiftKey: 0,
    getModifierState: ho
  }), Rm = zt(Dm), Vm = C({}, pl, {
    propertyName: 0,
    elapsedTime: 0,
    pseudoElement: 0
  }), Lm = zt(Vm), Um = C({}, Gr, {
    deltaX: function(e) {
      return "deltaX" in e ? e.deltaX : "wheelDeltaX" in e ? -e.wheelDeltaX : 0;
    },
    deltaY: function(e) {
      return "deltaY" in e ? e.deltaY : "wheelDeltaY" in e ? -e.wheelDeltaY : "wheelDelta" in e ? -e.wheelDelta : 0;
    },
    deltaZ: 0,
    deltaMode: 0
  }), qm = zt(Um), Bm = C({}, pl, {
    newState: 0,
    oldState: 0
  }), Gm = zt(Bm), Ym = [9, 13, 27, 32], go = xa && "CompositionEvent" in window, qn = null;
  xa && "documentMode" in document && (qn = document.documentMode);
  var Xm = xa && "TextEvent" in window && !qn, Ws = xa && (!go || qn && 8 < qn && 11 >= qn), Is = " ", Ps = !1;
  function eu(e, t) {
    switch (e) {
      case "keyup":
        return Ym.indexOf(t.keyCode) !== -1;
      case "keydown":
        return t.keyCode !== 229;
      case "keypress":
      case "mousedown":
      case "focusout":
        return !0;
      default:
        return !1;
    }
  }
  function tu(e) {
    return e = e.detail, typeof e == "object" && "data" in e ? e.data : null;
  }
  var Fl = !1;
  function Qm(e, t) {
    switch (e) {
      case "compositionend":
        return tu(t);
      case "keypress":
        return t.which !== 32 ? null : (Ps = !0, Is);
      case "textInput":
        return e = t.data, e === Is && Ps ? null : e;
      default:
        return null;
    }
  }
  function Km(e, t) {
    if (Fl)
      return e === "compositionend" || !go && eu(e, t) ? (e = Qs(), Lr = uo = Va = null, Fl = !1, e) : null;
    switch (e) {
      case "paste":
        return null;
      case "keypress":
        if (!(t.ctrlKey || t.altKey || t.metaKey) || t.ctrlKey && t.altKey) {
          if (t.char && 1 < t.char.length)
            return t.char;
          if (t.which) return String.fromCharCode(t.which);
        }
        return null;
      case "compositionend":
        return Ws && t.locale !== "ko" ? null : t.data;
      default:
        return null;
    }
  }
  var Jm = {
    color: !0,
    date: !0,
    datetime: !0,
    "datetime-local": !0,
    email: !0,
    month: !0,
    number: !0,
    password: !0,
    range: !0,
    search: !0,
    tel: !0,
    text: !0,
    time: !0,
    url: !0,
    week: !0
  };
  function au(e) {
    var t = e && e.nodeName && e.nodeName.toLowerCase();
    return t === "input" ? !!Jm[e.type] : t === "textarea";
  }
  function lu(e, t, a, l) {
    Jl ? $l ? $l.push(l) : $l = [l] : Jl = l, t = _i(t, "onChange"), 0 < t.length && (a = new Br(
      "onChange",
      "change",
      null,
      a,
      l
    ), e.push({ event: a, listeners: t }));
  }
  var Bn = null, Gn = null;
  function $m(e) {
    L1(e, 0);
  }
  function Yr(e) {
    var t = Dn(e);
    if (Vs(t)) return e;
  }
  function nu(e, t) {
    if (e === "change") return t;
  }
  var ru = !1;
  if (xa) {
    var bo;
    if (xa) {
      var vo = "oninput" in document;
      if (!vo) {
        var iu = document.createElement("div");
        iu.setAttribute("oninput", "return;"), vo = typeof iu.oninput == "function";
      }
      bo = vo;
    } else bo = !1;
    ru = bo && (!document.documentMode || 9 < document.documentMode);
  }
  function ou() {
    Bn && (Bn.detachEvent("onpropertychange", cu), Gn = Bn = null);
  }
  function cu(e) {
    if (e.propertyName === "value" && Yr(Gn)) {
      var t = [];
      lu(
        t,
        Gn,
        e,
        oo(e)
      ), Xs($m, t);
    }
  }
  function Fm(e, t, a) {
    e === "focusin" ? (ou(), Bn = t, Gn = a, Bn.attachEvent("onpropertychange", cu)) : e === "focusout" && ou();
  }
  function Wm(e) {
    if (e === "selectionchange" || e === "keyup" || e === "keydown")
      return Yr(Gn);
  }
  function Im(e, t) {
    if (e === "click") return Yr(t);
  }
  function Pm(e, t) {
    if (e === "input" || e === "change")
      return Yr(t);
  }
  function ep(e, t) {
    return e === t && (e !== 0 || 1 / e === 1 / t) || e !== e && t !== t;
  }
  var jt = typeof Object.is == "function" ? Object.is : ep;
  function Yn(e, t) {
    if (jt(e, t)) return !0;
    if (typeof e != "object" || e === null || typeof t != "object" || t === null)
      return !1;
    var a = Object.keys(e), l = Object.keys(t);
    if (a.length !== l.length) return !1;
    for (l = 0; l < a.length; l++) {
      var n = a[l];
      if (!Dl.call(t, n) || !jt(e[n], t[n]))
        return !1;
    }
    return !0;
  }
  function su(e) {
    for (; e && e.firstChild; ) e = e.firstChild;
    return e;
  }
  function uu(e, t) {
    var a = su(e);
    e = 0;
    for (var l; a; ) {
      if (a.nodeType === 3) {
        if (l = e + a.textContent.length, e <= t && l >= t)
          return { node: a, offset: t - e };
        e = l;
      }
      e: {
        for (; a; ) {
          if (a.nextSibling) {
            a = a.nextSibling;
            break e;
          }
          a = a.parentNode;
        }
        a = void 0;
      }
      a = su(a);
    }
  }
  function fu(e, t) {
    return e && t ? e === t ? !0 : e && e.nodeType === 3 ? !1 : t && t.nodeType === 3 ? fu(e, t.parentNode) : "contains" in e ? e.contains(t) : e.compareDocumentPosition ? !!(e.compareDocumentPosition(t) & 16) : !1 : !1;
  }
  function du(e) {
    e = e != null && e.ownerDocument != null && e.ownerDocument.defaultView != null ? e.ownerDocument.defaultView : window;
    for (var t = Rr(e.document); t instanceof e.HTMLIFrameElement; ) {
      try {
        var a = typeof t.contentWindow.location.href == "string";
      } catch {
        a = !1;
      }
      if (a) e = t.contentWindow;
      else break;
      t = Rr(e.document);
    }
    return t;
  }
  function xo(e) {
    var t = e && e.nodeName && e.nodeName.toLowerCase();
    return t && (t === "input" && (e.type === "text" || e.type === "search" || e.type === "tel" || e.type === "url" || e.type === "password") || t === "textarea" || e.contentEditable === "true");
  }
  var tp = xa && "documentMode" in document && 11 >= document.documentMode, Wl = null, yo = null, Xn = null, wo = !1;
  function mu(e, t, a) {
    var l = a.window === a ? a.document : a.nodeType === 9 ? a : a.ownerDocument;
    wo || Wl == null || Wl !== Rr(l) || (l = Wl, "selectionStart" in l && xo(l) ? l = { start: l.selectionStart, end: l.selectionEnd } : (l = (l.ownerDocument && l.ownerDocument.defaultView || window).getSelection(), l = {
      anchorNode: l.anchorNode,
      anchorOffset: l.anchorOffset,
      focusNode: l.focusNode,
      focusOffset: l.focusOffset
    }), Xn && Yn(Xn, l) || (Xn = l, l = _i(yo, "onSelect"), 0 < l.length && (t = new Br(
      "onSelect",
      "select",
      null,
      t,
      a
    ), e.push({ event: t, listeners: l }), t.target = Wl)));
  }
  function hl(e, t) {
    var a = {};
    return a[e.toLowerCase()] = t.toLowerCase(), a["Webkit" + e] = "webkit" + t, a["Moz" + e] = "moz" + t, a;
  }
  var Il = {
    animationend: hl("Animation", "AnimationEnd"),
    animationiteration: hl("Animation", "AnimationIteration"),
    animationstart: hl("Animation", "AnimationStart"),
    transitionrun: hl("Transition", "TransitionRun"),
    transitionstart: hl("Transition", "TransitionStart"),
    transitioncancel: hl("Transition", "TransitionCancel"),
    transitionend: hl("Transition", "TransitionEnd")
  }, So = {}, pu = {};
  xa && (pu = document.createElement("div").style, "AnimationEvent" in window || (delete Il.animationend.animation, delete Il.animationiteration.animation, delete Il.animationstart.animation), "TransitionEvent" in window || delete Il.transitionend.transition);
  function gl(e) {
    if (So[e]) return So[e];
    if (!Il[e]) return e;
    var t = Il[e], a;
    for (a in t)
      if (t.hasOwnProperty(a) && a in pu)
        return So[e] = t[a];
    return e;
  }
  var hu = gl("animationend"), gu = gl("animationiteration"), bu = gl("animationstart"), ap = gl("transitionrun"), lp = gl("transitionstart"), np = gl("transitioncancel"), vu = gl("transitionend"), xu = /* @__PURE__ */ new Map(), Eo = "abort auxClick beforeToggle cancel canPlay canPlayThrough click close contextMenu copy cut drag dragEnd dragEnter dragExit dragLeave dragOver dragStart drop durationChange emptied encrypted ended error gotPointerCapture input invalid keyDown keyPress keyUp load loadedData loadedMetadata loadStart lostPointerCapture mouseDown mouseMove mouseOut mouseOver mouseUp paste pause play playing pointerCancel pointerDown pointerMove pointerOut pointerOver pointerUp progress rateChange reset resize seeked seeking stalled submit suspend timeUpdate touchCancel touchEnd touchStart volumeChange scroll toggle touchMove waiting wheel".split(
    " "
  );
  Eo.push("scrollEnd");
  function la(e, t) {
    xu.set(e, t), ml(t, [e]);
  }
  var Xr = typeof reportError == "function" ? reportError : function(e) {
    if (typeof window == "object" && typeof window.ErrorEvent == "function") {
      var t = new window.ErrorEvent("error", {
        bubbles: !0,
        cancelable: !0,
        message: typeof e == "object" && e !== null && typeof e.message == "string" ? String(e.message) : String(e),
        error: e
      });
      if (!window.dispatchEvent(t)) return;
    } else if (typeof process == "object" && typeof process.emit == "function") {
      process.emit("uncaughtException", e);
      return;
    }
    console.error(e);
  }, Xt = [], Pl = 0, Ao = 0;
  function Qr() {
    for (var e = Pl, t = Ao = Pl = 0; t < e; ) {
      var a = Xt[t];
      Xt[t++] = null;
      var l = Xt[t];
      Xt[t++] = null;
      var n = Xt[t];
      Xt[t++] = null;
      var r = Xt[t];
      if (Xt[t++] = null, l !== null && n !== null) {
        var c = l.pending;
        c === null ? n.next = n : (n.next = c.next, c.next = n), l.pending = n;
      }
      r !== 0 && yu(a, n, r);
    }
  }
  function Kr(e, t, a, l) {
    Xt[Pl++] = e, Xt[Pl++] = t, Xt[Pl++] = a, Xt[Pl++] = l, Ao |= l, e.lanes |= l, e = e.alternate, e !== null && (e.lanes |= l);
  }
  function Mo(e, t, a, l) {
    return Kr(e, t, a, l), Jr(e);
  }
  function bl(e, t) {
    return Kr(e, null, null, t), Jr(e);
  }
  function yu(e, t, a) {
    e.lanes |= a;
    var l = e.alternate;
    l !== null && (l.lanes |= a);
    for (var n = !1, r = e.return; r !== null; )
      r.childLanes |= a, l = r.alternate, l !== null && (l.childLanes |= a), r.tag === 22 && (e = r.stateNode, e === null || e._visibility & 1 || (n = !0)), e = r, r = r.return;
    return e.tag === 3 ? (r = e.stateNode, n && t !== null && (n = 31 - yt(a), e = r.hiddenUpdates, l = e[n], l === null ? e[n] = [t] : l.push(t), t.lane = a | 536870912), r) : null;
  }
  function Jr(e) {
    if (50 < mr)
      throw mr = 0, Oc = null, Error(o(185));
    for (var t = e.return; t !== null; )
      e = t, t = e.return;
    return e.tag === 3 ? e.stateNode : null;
  }
  var en = {};
  function rp(e, t, a, l) {
    this.tag = e, this.key = a, this.sibling = this.child = this.return = this.stateNode = this.type = this.elementType = null, this.index = 0, this.refCleanup = this.ref = null, this.pendingProps = t, this.dependencies = this.memoizedState = this.updateQueue = this.memoizedProps = null, this.mode = l, this.subtreeFlags = this.flags = 0, this.deletions = null, this.childLanes = this.lanes = 0, this.alternate = null;
  }
  function Ot(e, t, a, l) {
    return new rp(e, t, a, l);
  }
  function ko(e) {
    return e = e.prototype, !(!e || !e.isReactComponent);
  }
  function ya(e, t) {
    var a = e.alternate;
    return a === null ? (a = Ot(
      e.tag,
      t,
      e.key,
      e.mode
    ), a.elementType = e.elementType, a.type = e.type, a.stateNode = e.stateNode, a.alternate = e, e.alternate = a) : (a.pendingProps = t, a.type = e.type, a.flags = 0, a.subtreeFlags = 0, a.deletions = null), a.flags = e.flags & 65011712, a.childLanes = e.childLanes, a.lanes = e.lanes, a.child = e.child, a.memoizedProps = e.memoizedProps, a.memoizedState = e.memoizedState, a.updateQueue = e.updateQueue, t = e.dependencies, a.dependencies = t === null ? null : { lanes: t.lanes, firstContext: t.firstContext }, a.sibling = e.sibling, a.index = e.index, a.ref = e.ref, a.refCleanup = e.refCleanup, a;
  }
  function wu(e, t) {
    e.flags &= 65011714;
    var a = e.alternate;
    return a === null ? (e.childLanes = 0, e.lanes = t, e.child = null, e.subtreeFlags = 0, e.memoizedProps = null, e.memoizedState = null, e.updateQueue = null, e.dependencies = null, e.stateNode = null) : (e.childLanes = a.childLanes, e.lanes = a.lanes, e.child = a.child, e.subtreeFlags = 0, e.deletions = null, e.memoizedProps = a.memoizedProps, e.memoizedState = a.memoizedState, e.updateQueue = a.updateQueue, e.type = a.type, t = a.dependencies, e.dependencies = t === null ? null : {
      lanes: t.lanes,
      firstContext: t.firstContext
    }), e;
  }
  function $r(e, t, a, l, n, r) {
    var c = 0;
    if (l = e, typeof e == "function") ko(e) && (c = 1);
    else if (typeof e == "string")
      c = uh(
        e,
        a,
        F.current
      ) ? 26 : e === "html" || e === "head" || e === "body" ? 27 : 5;
    else
      e: switch (e) {
        case xe:
          return e = Ot(31, a, t, n), e.elementType = xe, e.lanes = r, e;
        case Q:
          return vl(a.children, n, r, t);
        case de:
          c = 8, n |= 24;
          break;
        case fe:
          return e = Ot(12, a, t, n | 2), e.elementType = fe, e.lanes = r, e;
        case Le:
          return e = Ot(13, a, t, n), e.elementType = Le, e.lanes = r, e;
        case J:
          return e = Ot(19, a, t, n), e.elementType = J, e.lanes = r, e;
        default:
          if (typeof e == "object" && e !== null)
            switch (e.$$typeof) {
              case be:
                c = 10;
                break e;
              case se:
                c = 9;
                break e;
              case Oe:
                c = 11;
                break e;
              case K:
                c = 14;
                break e;
              case U:
                c = 16, l = null;
                break e;
            }
          c = 29, a = Error(
            o(130, e === null ? "null" : typeof e, "")
          ), l = null;
      }
    return t = Ot(c, a, t, n), t.elementType = e, t.type = l, t.lanes = r, t;
  }
  function vl(e, t, a, l) {
    return e = Ot(7, e, l, t), e.lanes = a, e;
  }
  function zo(e, t, a) {
    return e = Ot(6, e, null, t), e.lanes = a, e;
  }
  function Su(e) {
    var t = Ot(18, null, null, 0);
    return t.stateNode = e, t;
  }
  function Ho(e, t, a) {
    return t = Ot(
      4,
      e.children !== null ? e.children : [],
      e.key,
      t
    ), t.lanes = a, t.stateNode = {
      containerInfo: e.containerInfo,
      pendingChildren: null,
      implementation: e.implementation
    }, t;
  }
  var Eu = /* @__PURE__ */ new WeakMap();
  function Qt(e, t) {
    if (typeof e == "object" && e !== null) {
      var a = Eu.get(e);
      return a !== void 0 ? a : (t = {
        value: e,
        source: t,
        stack: Pt(t)
      }, Eu.set(e, t), t);
    }
    return {
      value: e,
      source: t,
      stack: Pt(t)
    };
  }
  var tn = [], an = 0, Fr = null, Qn = 0, Kt = [], Jt = 0, La = null, sa = 1, ua = "";
  function wa(e, t) {
    tn[an++] = Qn, tn[an++] = Fr, Fr = e, Qn = t;
  }
  function Au(e, t, a) {
    Kt[Jt++] = sa, Kt[Jt++] = ua, Kt[Jt++] = La, La = e;
    var l = sa;
    e = ua;
    var n = 32 - yt(l) - 1;
    l &= ~(1 << n), a += 1;
    var r = 32 - yt(t) + n;
    if (30 < r) {
      var c = n - n % 5;
      r = (l & (1 << c) - 1).toString(32), l >>= c, n -= c, sa = 1 << 32 - yt(t) + n | a << n | l, ua = r + e;
    } else
      sa = 1 << r | a << n | l, ua = e;
  }
  function To(e) {
    e.return !== null && (wa(e, 1), Au(e, 1, 0));
  }
  function No(e) {
    for (; e === Fr; )
      Fr = tn[--an], tn[an] = null, Qn = tn[--an], tn[an] = null;
    for (; e === La; )
      La = Kt[--Jt], Kt[Jt] = null, ua = Kt[--Jt], Kt[Jt] = null, sa = Kt[--Jt], Kt[Jt] = null;
  }
  function Mu(e, t) {
    Kt[Jt++] = sa, Kt[Jt++] = ua, Kt[Jt++] = La, sa = t.id, ua = t.overflow, La = e;
  }
  var pt = null, Je = null, ze = !1, Ua = null, $t = !1, Co = Error(o(519));
  function qa(e) {
    var t = Error(
      o(
        418,
        1 < arguments.length && arguments[1] !== void 0 && arguments[1] ? "text" : "HTML",
        ""
      )
    );
    throw Kn(Qt(t, e)), Co;
  }
  function ku(e) {
    var t = e.stateNode, a = e.type, l = e.memoizedProps;
    switch (t[Ie] = e, t[mt] = l, a) {
      case "dialog":
        Ae("cancel", t), Ae("close", t);
        break;
      case "iframe":
      case "object":
      case "embed":
        Ae("load", t);
        break;
      case "video":
      case "audio":
        for (a = 0; a < hr.length; a++)
          Ae(hr[a], t);
        break;
      case "source":
        Ae("error", t);
        break;
      case "img":
      case "image":
      case "link":
        Ae("error", t), Ae("load", t);
        break;
      case "details":
        Ae("toggle", t);
        break;
      case "input":
        Ae("invalid", t), Ls(
          t,
          l.value,
          l.defaultValue,
          l.checked,
          l.defaultChecked,
          l.type,
          l.name,
          !0
        );
        break;
      case "select":
        Ae("invalid", t);
        break;
      case "textarea":
        Ae("invalid", t), qs(t, l.value, l.defaultValue, l.children);
    }
    a = l.children, typeof a != "string" && typeof a != "number" && typeof a != "bigint" || t.textContent === "" + a || l.suppressHydrationWarning === !0 || G1(t.textContent, a) ? (l.popover != null && (Ae("beforetoggle", t), Ae("toggle", t)), l.onScroll != null && Ae("scroll", t), l.onScrollEnd != null && Ae("scrollend", t), l.onClick != null && (t.onclick = va), t = !0) : t = !1, t || qa(e, !0);
  }
  function zu(e) {
    for (pt = e.return; pt; )
      switch (pt.tag) {
        case 5:
        case 31:
        case 13:
          $t = !1;
          return;
        case 27:
        case 3:
          $t = !0;
          return;
        default:
          pt = pt.return;
      }
  }
  function ln(e) {
    if (e !== pt) return !1;
    if (!ze) return zu(e), ze = !0, !1;
    var t = e.tag, a;
    if ((a = t !== 3 && t !== 27) && ((a = t === 5) && (a = e.type, a = !(a !== "form" && a !== "button") || $c(e.type, e.memoizedProps)), a = !a), a && Je && qa(e), zu(e), t === 13) {
      if (e = e.memoizedState, e = e !== null ? e.dehydrated : null, !e) throw Error(o(317));
      Je = I1(e);
    } else if (t === 31) {
      if (e = e.memoizedState, e = e !== null ? e.dehydrated : null, !e) throw Error(o(317));
      Je = I1(e);
    } else
      t === 27 ? (t = Je, tl(e.type) ? (e = es, es = null, Je = e) : Je = t) : Je = pt ? Wt(e.stateNode.nextSibling) : null;
    return !0;
  }
  function xl() {
    Je = pt = null, ze = !1;
  }
  function _o() {
    var e = Ua;
    return e !== null && (Ct === null ? Ct = e : Ct.push.apply(
      Ct,
      e
    ), Ua = null), e;
  }
  function Kn(e) {
    Ua === null ? Ua = [e] : Ua.push(e);
  }
  var jo = S(null), yl = null, Sa = null;
  function Ba(e, t, a) {
    Y(jo, t._currentValue), t._currentValue = a;
  }
  function Ea(e) {
    e._currentValue = jo.current, _(jo);
  }
  function Oo(e, t, a) {
    for (; e !== null; ) {
      var l = e.alternate;
      if ((e.childLanes & t) !== t ? (e.childLanes |= t, l !== null && (l.childLanes |= t)) : l !== null && (l.childLanes & t) !== t && (l.childLanes |= t), e === a) break;
      e = e.return;
    }
  }
  function Zo(e, t, a, l) {
    var n = e.child;
    for (n !== null && (n.return = e); n !== null; ) {
      var r = n.dependencies;
      if (r !== null) {
        var c = n.child;
        r = r.firstContext;
        e: for (; r !== null; ) {
          var d = r;
          r = n;
          for (var y = 0; y < t.length; y++)
            if (d.context === t[y]) {
              r.lanes |= a, d = r.alternate, d !== null && (d.lanes |= a), Oo(
                r.return,
                a,
                e
              ), l || (c = null);
              break e;
            }
          r = d.next;
        }
      } else if (n.tag === 18) {
        if (c = n.return, c === null) throw Error(o(341));
        c.lanes |= a, r = c.alternate, r !== null && (r.lanes |= a), Oo(c, a, e), c = null;
      } else c = n.child;
      if (c !== null) c.return = n;
      else
        for (c = n; c !== null; ) {
          if (c === e) {
            c = null;
            break;
          }
          if (n = c.sibling, n !== null) {
            n.return = c.return, c = n;
            break;
          }
          c = c.return;
        }
      n = c;
    }
  }
  function nn(e, t, a, l) {
    e = null;
    for (var n = t, r = !1; n !== null; ) {
      if (!r) {
        if ((n.flags & 524288) !== 0) r = !0;
        else if ((n.flags & 262144) !== 0) break;
      }
      if (n.tag === 10) {
        var c = n.alternate;
        if (c === null) throw Error(o(387));
        if (c = c.memoizedProps, c !== null) {
          var d = n.type;
          jt(n.pendingProps.value, c.value) || (e !== null ? e.push(d) : e = [d]);
        }
      } else if (n === Se.current) {
        if (c = n.alternate, c === null) throw Error(o(387));
        c.memoizedState.memoizedState !== n.memoizedState.memoizedState && (e !== null ? e.push(yr) : e = [yr]);
      }
      n = n.return;
    }
    e !== null && Zo(
      t,
      e,
      a,
      l
    ), t.flags |= 262144;
  }
  function Wr(e) {
    for (e = e.firstContext; e !== null; ) {
      if (!jt(
        e.context._currentValue,
        e.memoizedValue
      ))
        return !0;
      e = e.next;
    }
    return !1;
  }
  function wl(e) {
    yl = e, Sa = null, e = e.dependencies, e !== null && (e.firstContext = null);
  }
  function ht(e) {
    return Hu(yl, e);
  }
  function Ir(e, t) {
    return yl === null && wl(e), Hu(e, t);
  }
  function Hu(e, t) {
    var a = t._currentValue;
    if (t = { context: t, memoizedValue: a, next: null }, Sa === null) {
      if (e === null) throw Error(o(308));
      Sa = t, e.dependencies = { lanes: 0, firstContext: t }, e.flags |= 524288;
    } else Sa = Sa.next = t;
    return a;
  }
  var ip = typeof AbortController < "u" ? AbortController : function() {
    var e = [], t = this.signal = {
      aborted: !1,
      addEventListener: function(a, l) {
        e.push(l);
      }
    };
    this.abort = function() {
      t.aborted = !0, e.forEach(function(a) {
        return a();
      });
    };
  }, op = i.unstable_scheduleCallback, cp = i.unstable_NormalPriority, nt = {
    $$typeof: be,
    Consumer: null,
    Provider: null,
    _currentValue: null,
    _currentValue2: null,
    _threadCount: 0
  };
  function Do() {
    return {
      controller: new ip(),
      data: /* @__PURE__ */ new Map(),
      refCount: 0
    };
  }
  function Jn(e) {
    e.refCount--, e.refCount === 0 && op(cp, function() {
      e.controller.abort();
    });
  }
  var $n = null, Ro = 0, rn = 0, on = null;
  function sp(e, t) {
    if ($n === null) {
      var a = $n = [];
      Ro = 0, rn = Uc(), on = {
        status: "pending",
        value: void 0,
        then: function(l) {
          a.push(l);
        }
      };
    }
    return Ro++, t.then(Tu, Tu), t;
  }
  function Tu() {
    if (--Ro === 0 && $n !== null) {
      on !== null && (on.status = "fulfilled");
      var e = $n;
      $n = null, rn = 0, on = null;
      for (var t = 0; t < e.length; t++) (0, e[t])();
    }
  }
  function up(e, t) {
    var a = [], l = {
      status: "pending",
      value: null,
      reason: null,
      then: function(n) {
        a.push(n);
      }
    };
    return e.then(
      function() {
        l.status = "fulfilled", l.value = t;
        for (var n = 0; n < a.length; n++) (0, a[n])(t);
      },
      function(n) {
        for (l.status = "rejected", l.reason = n, n = 0; n < a.length; n++)
          (0, a[n])(void 0);
      }
    ), l;
  }
  var Nu = z.S;
  z.S = function(e, t) {
    m1 = Ke(), typeof t == "object" && t !== null && typeof t.then == "function" && sp(e, t), Nu !== null && Nu(e, t);
  };
  var Sl = S(null);
  function Vo() {
    var e = Sl.current;
    return e !== null ? e : Xe.pooledCache;
  }
  function Pr(e, t) {
    t === null ? Y(Sl, Sl.current) : Y(Sl, t.pool);
  }
  function Cu() {
    var e = Vo();
    return e === null ? null : { parent: nt._currentValue, pool: e };
  }
  var cn = Error(o(460)), Lo = Error(o(474)), ei = Error(o(542)), ti = { then: function() {
  } };
  function _u(e) {
    return e = e.status, e === "fulfilled" || e === "rejected";
  }
  function ju(e, t, a) {
    switch (a = e[a], a === void 0 ? e.push(t) : a !== t && (t.then(va, va), t = a), t.status) {
      case "fulfilled":
        return t.value;
      case "rejected":
        throw e = t.reason, Zu(e), e;
      default:
        if (typeof t.status == "string") t.then(va, va);
        else {
          if (e = Xe, e !== null && 100 < e.shellSuspendCounter)
            throw Error(o(482));
          e = t, e.status = "pending", e.then(
            function(l) {
              if (t.status === "pending") {
                var n = t;
                n.status = "fulfilled", n.value = l;
              }
            },
            function(l) {
              if (t.status === "pending") {
                var n = t;
                n.status = "rejected", n.reason = l;
              }
            }
          );
        }
        switch (t.status) {
          case "fulfilled":
            return t.value;
          case "rejected":
            throw e = t.reason, Zu(e), e;
        }
        throw Al = t, cn;
    }
  }
  function El(e) {
    try {
      var t = e._init;
      return t(e._payload);
    } catch (a) {
      throw a !== null && typeof a == "object" && typeof a.then == "function" ? (Al = a, cn) : a;
    }
  }
  var Al = null;
  function Ou() {
    if (Al === null) throw Error(o(459));
    var e = Al;
    return Al = null, e;
  }
  function Zu(e) {
    if (e === cn || e === ei)
      throw Error(o(483));
  }
  var sn = null, Fn = 0;
  function ai(e) {
    var t = Fn;
    return Fn += 1, sn === null && (sn = []), ju(sn, e, t);
  }
  function Wn(e, t) {
    t = t.props.ref, e.ref = t !== void 0 ? t : null;
  }
  function li(e, t) {
    throw t.$$typeof === T ? Error(o(525)) : (e = Object.prototype.toString.call(t), Error(
      o(
        31,
        e === "[object Object]" ? "object with keys {" + Object.keys(t).join(", ") + "}" : e
      )
    ));
  }
  function Du(e) {
    function t(E, w) {
      if (e) {
        var A = E.deletions;
        A === null ? (E.deletions = [w], E.flags |= 16) : A.push(w);
      }
    }
    function a(E, w) {
      if (!e) return null;
      for (; w !== null; )
        t(E, w), w = w.sibling;
      return null;
    }
    function l(E) {
      for (var w = /* @__PURE__ */ new Map(); E !== null; )
        E.key !== null ? w.set(E.key, E) : w.set(E.index, E), E = E.sibling;
      return w;
    }
    function n(E, w) {
      return E = ya(E, w), E.index = 0, E.sibling = null, E;
    }
    function r(E, w, A) {
      return E.index = A, e ? (A = E.alternate, A !== null ? (A = A.index, A < w ? (E.flags |= 67108866, w) : A) : (E.flags |= 67108866, w)) : (E.flags |= 1048576, w);
    }
    function c(E) {
      return e && E.alternate === null && (E.flags |= 67108866), E;
    }
    function d(E, w, A, j) {
      return w === null || w.tag !== 6 ? (w = zo(A, E.mode, j), w.return = E, w) : (w = n(w, A), w.return = E, w);
    }
    function y(E, w, A, j) {
      var oe = A.type;
      return oe === Q ? N(
        E,
        w,
        A.props.children,
        j,
        A.key
      ) : w !== null && (w.elementType === oe || typeof oe == "object" && oe !== null && oe.$$typeof === U && El(oe) === w.type) ? (w = n(w, A.props), Wn(w, A), w.return = E, w) : (w = $r(
        A.type,
        A.key,
        A.props,
        null,
        E.mode,
        j
      ), Wn(w, A), w.return = E, w);
    }
    function M(E, w, A, j) {
      return w === null || w.tag !== 4 || w.stateNode.containerInfo !== A.containerInfo || w.stateNode.implementation !== A.implementation ? (w = Ho(A, E.mode, j), w.return = E, w) : (w = n(w, A.children || []), w.return = E, w);
    }
    function N(E, w, A, j, oe) {
      return w === null || w.tag !== 7 ? (w = vl(
        A,
        E.mode,
        j,
        oe
      ), w.return = E, w) : (w = n(w, A), w.return = E, w);
    }
    function O(E, w, A) {
      if (typeof w == "string" && w !== "" || typeof w == "number" || typeof w == "bigint")
        return w = zo(
          "" + w,
          E.mode,
          A
        ), w.return = E, w;
      if (typeof w == "object" && w !== null) {
        switch (w.$$typeof) {
          case L:
            return A = $r(
              w.type,
              w.key,
              w.props,
              null,
              E.mode,
              A
            ), Wn(A, w), A.return = E, A;
          case G:
            return w = Ho(
              w,
              E.mode,
              A
            ), w.return = E, w;
          case U:
            return w = El(w), O(E, w, A);
        }
        if (Qe(w) || $(w))
          return w = vl(
            w,
            E.mode,
            A,
            null
          ), w.return = E, w;
        if (typeof w.then == "function")
          return O(E, ai(w), A);
        if (w.$$typeof === be)
          return O(
            E,
            Ir(E, w),
            A
          );
        li(E, w);
      }
      return null;
    }
    function k(E, w, A, j) {
      var oe = w !== null ? w.key : null;
      if (typeof A == "string" && A !== "" || typeof A == "number" || typeof A == "bigint")
        return oe !== null ? null : d(E, w, "" + A, j);
      if (typeof A == "object" && A !== null) {
        switch (A.$$typeof) {
          case L:
            return A.key === oe ? y(E, w, A, j) : null;
          case G:
            return A.key === oe ? M(E, w, A, j) : null;
          case U:
            return A = El(A), k(E, w, A, j);
        }
        if (Qe(A) || $(A))
          return oe !== null ? null : N(E, w, A, j, null);
        if (typeof A.then == "function")
          return k(
            E,
            w,
            ai(A),
            j
          );
        if (A.$$typeof === be)
          return k(
            E,
            w,
            Ir(E, A),
            j
          );
        li(E, A);
      }
      return null;
    }
    function H(E, w, A, j, oe) {
      if (typeof j == "string" && j !== "" || typeof j == "number" || typeof j == "bigint")
        return E = E.get(A) || null, d(w, E, "" + j, oe);
      if (typeof j == "object" && j !== null) {
        switch (j.$$typeof) {
          case L:
            return E = E.get(
              j.key === null ? A : j.key
            ) || null, y(w, E, j, oe);
          case G:
            return E = E.get(
              j.key === null ? A : j.key
            ) || null, M(w, E, j, oe);
          case U:
            return j = El(j), H(
              E,
              w,
              A,
              j,
              oe
            );
        }
        if (Qe(j) || $(j))
          return E = E.get(A) || null, N(w, E, j, oe, null);
        if (typeof j.then == "function")
          return H(
            E,
            w,
            A,
            ai(j),
            oe
          );
        if (j.$$typeof === be)
          return H(
            E,
            w,
            A,
            Ir(w, j),
            oe
          );
        li(w, j);
      }
      return null;
    }
    function P(E, w, A, j) {
      for (var oe = null, Ce = null, ae = w, ve = w = 0, ke = null; ae !== null && ve < A.length; ve++) {
        ae.index > ve ? (ke = ae, ae = null) : ke = ae.sibling;
        var _e = k(
          E,
          ae,
          A[ve],
          j
        );
        if (_e === null) {
          ae === null && (ae = ke);
          break;
        }
        e && ae && _e.alternate === null && t(E, ae), w = r(_e, w, ve), Ce === null ? oe = _e : Ce.sibling = _e, Ce = _e, ae = ke;
      }
      if (ve === A.length)
        return a(E, ae), ze && wa(E, ve), oe;
      if (ae === null) {
        for (; ve < A.length; ve++)
          ae = O(E, A[ve], j), ae !== null && (w = r(
            ae,
            w,
            ve
          ), Ce === null ? oe = ae : Ce.sibling = ae, Ce = ae);
        return ze && wa(E, ve), oe;
      }
      for (ae = l(ae); ve < A.length; ve++)
        ke = H(
          ae,
          E,
          ve,
          A[ve],
          j
        ), ke !== null && (e && ke.alternate !== null && ae.delete(
          ke.key === null ? ve : ke.key
        ), w = r(
          ke,
          w,
          ve
        ), Ce === null ? oe = ke : Ce.sibling = ke, Ce = ke);
      return e && ae.forEach(function(il) {
        return t(E, il);
      }), ze && wa(E, ve), oe;
    }
    function ce(E, w, A, j) {
      if (A == null) throw Error(o(151));
      for (var oe = null, Ce = null, ae = w, ve = w = 0, ke = null, _e = A.next(); ae !== null && !_e.done; ve++, _e = A.next()) {
        ae.index > ve ? (ke = ae, ae = null) : ke = ae.sibling;
        var il = k(E, ae, _e.value, j);
        if (il === null) {
          ae === null && (ae = ke);
          break;
        }
        e && ae && il.alternate === null && t(E, ae), w = r(il, w, ve), Ce === null ? oe = il : Ce.sibling = il, Ce = il, ae = ke;
      }
      if (_e.done)
        return a(E, ae), ze && wa(E, ve), oe;
      if (ae === null) {
        for (; !_e.done; ve++, _e = A.next())
          _e = O(E, _e.value, j), _e !== null && (w = r(_e, w, ve), Ce === null ? oe = _e : Ce.sibling = _e, Ce = _e);
        return ze && wa(E, ve), oe;
      }
      for (ae = l(ae); !_e.done; ve++, _e = A.next())
        _e = H(ae, E, ve, _e.value, j), _e !== null && (e && _e.alternate !== null && ae.delete(_e.key === null ? ve : _e.key), w = r(_e, w, ve), Ce === null ? oe = _e : Ce.sibling = _e, Ce = _e);
      return e && ae.forEach(function(wh) {
        return t(E, wh);
      }), ze && wa(E, ve), oe;
    }
    function Be(E, w, A, j) {
      if (typeof A == "object" && A !== null && A.type === Q && A.key === null && (A = A.props.children), typeof A == "object" && A !== null) {
        switch (A.$$typeof) {
          case L:
            e: {
              for (var oe = A.key; w !== null; ) {
                if (w.key === oe) {
                  if (oe = A.type, oe === Q) {
                    if (w.tag === 7) {
                      a(
                        E,
                        w.sibling
                      ), j = n(
                        w,
                        A.props.children
                      ), j.return = E, E = j;
                      break e;
                    }
                  } else if (w.elementType === oe || typeof oe == "object" && oe !== null && oe.$$typeof === U && El(oe) === w.type) {
                    a(
                      E,
                      w.sibling
                    ), j = n(w, A.props), Wn(j, A), j.return = E, E = j;
                    break e;
                  }
                  a(E, w);
                  break;
                } else t(E, w);
                w = w.sibling;
              }
              A.type === Q ? (j = vl(
                A.props.children,
                E.mode,
                j,
                A.key
              ), j.return = E, E = j) : (j = $r(
                A.type,
                A.key,
                A.props,
                null,
                E.mode,
                j
              ), Wn(j, A), j.return = E, E = j);
            }
            return c(E);
          case G:
            e: {
              for (oe = A.key; w !== null; ) {
                if (w.key === oe)
                  if (w.tag === 4 && w.stateNode.containerInfo === A.containerInfo && w.stateNode.implementation === A.implementation) {
                    a(
                      E,
                      w.sibling
                    ), j = n(w, A.children || []), j.return = E, E = j;
                    break e;
                  } else {
                    a(E, w);
                    break;
                  }
                else t(E, w);
                w = w.sibling;
              }
              j = Ho(A, E.mode, j), j.return = E, E = j;
            }
            return c(E);
          case U:
            return A = El(A), Be(
              E,
              w,
              A,
              j
            );
        }
        if (Qe(A))
          return P(
            E,
            w,
            A,
            j
          );
        if ($(A)) {
          if (oe = $(A), typeof oe != "function") throw Error(o(150));
          return A = oe.call(A), ce(
            E,
            w,
            A,
            j
          );
        }
        if (typeof A.then == "function")
          return Be(
            E,
            w,
            ai(A),
            j
          );
        if (A.$$typeof === be)
          return Be(
            E,
            w,
            Ir(E, A),
            j
          );
        li(E, A);
      }
      return typeof A == "string" && A !== "" || typeof A == "number" || typeof A == "bigint" ? (A = "" + A, w !== null && w.tag === 6 ? (a(E, w.sibling), j = n(w, A), j.return = E, E = j) : (a(E, w), j = zo(A, E.mode, j), j.return = E, E = j), c(E)) : a(E, w);
    }
    return function(E, w, A, j) {
      try {
        Fn = 0;
        var oe = Be(
          E,
          w,
          A,
          j
        );
        return sn = null, oe;
      } catch (ae) {
        if (ae === cn || ae === ei) throw ae;
        var Ce = Ot(29, ae, null, E.mode);
        return Ce.lanes = j, Ce.return = E, Ce;
      }
    };
  }
  var Ml = Du(!0), Ru = Du(!1), Ga = !1;
  function Uo(e) {
    e.updateQueue = {
      baseState: e.memoizedState,
      firstBaseUpdate: null,
      lastBaseUpdate: null,
      shared: { pending: null, lanes: 0, hiddenCallbacks: null },
      callbacks: null
    };
  }
  function qo(e, t) {
    e = e.updateQueue, t.updateQueue === e && (t.updateQueue = {
      baseState: e.baseState,
      firstBaseUpdate: e.firstBaseUpdate,
      lastBaseUpdate: e.lastBaseUpdate,
      shared: e.shared,
      callbacks: null
    });
  }
  function Ya(e) {
    return { lane: e, tag: 0, payload: null, callback: null, next: null };
  }
  function Xa(e, t, a) {
    var l = e.updateQueue;
    if (l === null) return null;
    if (l = l.shared, (je & 2) !== 0) {
      var n = l.pending;
      return n === null ? t.next = t : (t.next = n.next, n.next = t), l.pending = t, t = Jr(e), yu(e, null, a), t;
    }
    return Kr(e, l, t, a), Jr(e);
  }
  function In(e, t, a) {
    if (t = t.updateQueue, t !== null && (t = t.shared, (a & 4194048) !== 0)) {
      var l = t.lanes;
      l &= e.pendingLanes, a |= l, t.lanes = a, ga(e, a);
    }
  }
  function Bo(e, t) {
    var a = e.updateQueue, l = e.alternate;
    if (l !== null && (l = l.updateQueue, a === l)) {
      var n = null, r = null;
      if (a = a.firstBaseUpdate, a !== null) {
        do {
          var c = {
            lane: a.lane,
            tag: a.tag,
            payload: a.payload,
            callback: null,
            next: null
          };
          r === null ? n = r = c : r = r.next = c, a = a.next;
        } while (a !== null);
        r === null ? n = r = t : r = r.next = t;
      } else n = r = t;
      a = {
        baseState: l.baseState,
        firstBaseUpdate: n,
        lastBaseUpdate: r,
        shared: l.shared,
        callbacks: l.callbacks
      }, e.updateQueue = a;
      return;
    }
    e = a.lastBaseUpdate, e === null ? a.firstBaseUpdate = t : e.next = t, a.lastBaseUpdate = t;
  }
  var Go = !1;
  function Pn() {
    if (Go) {
      var e = on;
      if (e !== null) throw e;
    }
  }
  function er(e, t, a, l) {
    Go = !1;
    var n = e.updateQueue;
    Ga = !1;
    var r = n.firstBaseUpdate, c = n.lastBaseUpdate, d = n.shared.pending;
    if (d !== null) {
      n.shared.pending = null;
      var y = d, M = y.next;
      y.next = null, c === null ? r = M : c.next = M, c = y;
      var N = e.alternate;
      N !== null && (N = N.updateQueue, d = N.lastBaseUpdate, d !== c && (d === null ? N.firstBaseUpdate = M : d.next = M, N.lastBaseUpdate = y));
    }
    if (r !== null) {
      var O = n.baseState;
      c = 0, N = M = y = null, d = r;
      do {
        var k = d.lane & -536870913, H = k !== d.lane;
        if (H ? (Me & k) === k : (l & k) === k) {
          k !== 0 && k === rn && (Go = !0), N !== null && (N = N.next = {
            lane: 0,
            tag: d.tag,
            payload: d.payload,
            callback: null,
            next: null
          });
          e: {
            var P = e, ce = d;
            k = t;
            var Be = a;
            switch (ce.tag) {
              case 1:
                if (P = ce.payload, typeof P == "function") {
                  O = P.call(Be, O, k);
                  break e;
                }
                O = P;
                break e;
              case 3:
                P.flags = P.flags & -65537 | 128;
              case 0:
                if (P = ce.payload, k = typeof P == "function" ? P.call(Be, O, k) : P, k == null) break e;
                O = C({}, O, k);
                break e;
              case 2:
                Ga = !0;
            }
          }
          k = d.callback, k !== null && (e.flags |= 64, H && (e.flags |= 8192), H = n.callbacks, H === null ? n.callbacks = [k] : H.push(k));
        } else
          H = {
            lane: k,
            tag: d.tag,
            payload: d.payload,
            callback: d.callback,
            next: null
          }, N === null ? (M = N = H, y = O) : N = N.next = H, c |= k;
        if (d = d.next, d === null) {
          if (d = n.shared.pending, d === null)
            break;
          H = d, d = H.next, H.next = null, n.lastBaseUpdate = H, n.shared.pending = null;
        }
      } while (!0);
      N === null && (y = O), n.baseState = y, n.firstBaseUpdate = M, n.lastBaseUpdate = N, r === null && (n.shared.lanes = 0), Fa |= c, e.lanes = c, e.memoizedState = O;
    }
  }
  function Vu(e, t) {
    if (typeof e != "function")
      throw Error(o(191, e));
    e.call(t);
  }
  function Lu(e, t) {
    var a = e.callbacks;
    if (a !== null)
      for (e.callbacks = null, e = 0; e < a.length; e++)
        Vu(a[e], t);
  }
  var un = S(null), ni = S(0);
  function Uu(e, t) {
    e = _a, Y(ni, e), Y(un, t), _a = e | t.baseLanes;
  }
  function Yo() {
    Y(ni, _a), Y(un, un.current);
  }
  function Xo() {
    _a = ni.current, _(un), _(ni);
  }
  var Zt = S(null), Ft = null;
  function Qa(e) {
    var t = e.alternate;
    Y(at, at.current & 1), Y(Zt, e), Ft === null && (t === null || un.current !== null || t.memoizedState !== null) && (Ft = e);
  }
  function Qo(e) {
    Y(at, at.current), Y(Zt, e), Ft === null && (Ft = e);
  }
  function qu(e) {
    e.tag === 22 ? (Y(at, at.current), Y(Zt, e), Ft === null && (Ft = e)) : Ka();
  }
  function Ka() {
    Y(at, at.current), Y(Zt, Zt.current);
  }
  function Dt(e) {
    _(Zt), Ft === e && (Ft = null), _(at);
  }
  var at = S(0);
  function ri(e) {
    for (var t = e; t !== null; ) {
      if (t.tag === 13) {
        var a = t.memoizedState;
        if (a !== null && (a = a.dehydrated, a === null || Ic(a) || Pc(a)))
          return t;
      } else if (t.tag === 19 && (t.memoizedProps.revealOrder === "forwards" || t.memoizedProps.revealOrder === "backwards" || t.memoizedProps.revealOrder === "unstable_legacy-backwards" || t.memoizedProps.revealOrder === "together")) {
        if ((t.flags & 128) !== 0) return t;
      } else if (t.child !== null) {
        t.child.return = t, t = t.child;
        continue;
      }
      if (t === e) break;
      for (; t.sibling === null; ) {
        if (t.return === null || t.return === e) return null;
        t = t.return;
      }
      t.sibling.return = t.return, t = t.sibling;
    }
    return null;
  }
  var Aa = 0, ge = null, Ue = null, rt = null, ii = !1, fn = !1, kl = !1, oi = 0, tr = 0, dn = null, fp = 0;
  function Pe() {
    throw Error(o(321));
  }
  function Ko(e, t) {
    if (t === null) return !1;
    for (var a = 0; a < t.length && a < e.length; a++)
      if (!jt(e[a], t[a])) return !1;
    return !0;
  }
  function Jo(e, t, a, l, n, r) {
    return Aa = r, ge = t, t.memoizedState = null, t.updateQueue = null, t.lanes = 0, z.H = e === null || e.memoizedState === null ? A0 : sc, kl = !1, r = a(l, n), kl = !1, fn && (r = Gu(
      t,
      a,
      l,
      n
    )), Bu(e), r;
  }
  function Bu(e) {
    z.H = nr;
    var t = Ue !== null && Ue.next !== null;
    if (Aa = 0, rt = Ue = ge = null, ii = !1, tr = 0, dn = null, t) throw Error(o(300));
    e === null || it || (e = e.dependencies, e !== null && Wr(e) && (it = !0));
  }
  function Gu(e, t, a, l) {
    ge = e;
    var n = 0;
    do {
      if (fn && (dn = null), tr = 0, fn = !1, 25 <= n) throw Error(o(301));
      if (n += 1, rt = Ue = null, e.updateQueue != null) {
        var r = e.updateQueue;
        r.lastEffect = null, r.events = null, r.stores = null, r.memoCache != null && (r.memoCache.index = 0);
      }
      z.H = M0, r = t(a, l);
    } while (fn);
    return r;
  }
  function dp() {
    var e = z.H, t = e.useState()[0];
    return t = typeof t.then == "function" ? ar(t) : t, e = e.useState()[0], (Ue !== null ? Ue.memoizedState : null) !== e && (ge.flags |= 1024), t;
  }
  function $o() {
    var e = oi !== 0;
    return oi = 0, e;
  }
  function Fo(e, t, a) {
    t.updateQueue = e.updateQueue, t.flags &= -2053, e.lanes &= ~a;
  }
  function Wo(e) {
    if (ii) {
      for (e = e.memoizedState; e !== null; ) {
        var t = e.queue;
        t !== null && (t.pending = null), e = e.next;
      }
      ii = !1;
    }
    Aa = 0, rt = Ue = ge = null, fn = !1, tr = oi = 0, dn = null;
  }
  function Mt() {
    var e = {
      memoizedState: null,
      baseState: null,
      baseQueue: null,
      queue: null,
      next: null
    };
    return rt === null ? ge.memoizedState = rt = e : rt = rt.next = e, rt;
  }
  function lt() {
    if (Ue === null) {
      var e = ge.alternate;
      e = e !== null ? e.memoizedState : null;
    } else e = Ue.next;
    var t = rt === null ? ge.memoizedState : rt.next;
    if (t !== null)
      rt = t, Ue = e;
    else {
      if (e === null)
        throw ge.alternate === null ? Error(o(467)) : Error(o(310));
      Ue = e, e = {
        memoizedState: Ue.memoizedState,
        baseState: Ue.baseState,
        baseQueue: Ue.baseQueue,
        queue: Ue.queue,
        next: null
      }, rt === null ? ge.memoizedState = rt = e : rt = rt.next = e;
    }
    return rt;
  }
  function ci() {
    return { lastEffect: null, events: null, stores: null, memoCache: null };
  }
  function ar(e) {
    var t = tr;
    return tr += 1, dn === null && (dn = []), e = ju(dn, e, t), t = ge, (rt === null ? t.memoizedState : rt.next) === null && (t = t.alternate, z.H = t === null || t.memoizedState === null ? A0 : sc), e;
  }
  function si(e) {
    if (e !== null && typeof e == "object") {
      if (typeof e.then == "function") return ar(e);
      if (e.$$typeof === be) return ht(e);
    }
    throw Error(o(438, String(e)));
  }
  function Io(e) {
    var t = null, a = ge.updateQueue;
    if (a !== null && (t = a.memoCache), t == null) {
      var l = ge.alternate;
      l !== null && (l = l.updateQueue, l !== null && (l = l.memoCache, l != null && (t = {
        data: l.data.map(function(n) {
          return n.slice();
        }),
        index: 0
      })));
    }
    if (t == null && (t = { data: [], index: 0 }), a === null && (a = ci(), ge.updateQueue = a), a.memoCache = t, a = t.data[t.index], a === void 0)
      for (a = t.data[t.index] = Array(e), l = 0; l < e; l++)
        a[l] = tt;
    return t.index++, a;
  }
  function Ma(e, t) {
    return typeof t == "function" ? t(e) : t;
  }
  function ui(e) {
    var t = lt();
    return Po(t, Ue, e);
  }
  function Po(e, t, a) {
    var l = e.queue;
    if (l === null) throw Error(o(311));
    l.lastRenderedReducer = a;
    var n = e.baseQueue, r = l.pending;
    if (r !== null) {
      if (n !== null) {
        var c = n.next;
        n.next = r.next, r.next = c;
      }
      t.baseQueue = n = r, l.pending = null;
    }
    if (r = e.baseState, n === null) e.memoizedState = r;
    else {
      t = n.next;
      var d = c = null, y = null, M = t, N = !1;
      do {
        var O = M.lane & -536870913;
        if (O !== M.lane ? (Me & O) === O : (Aa & O) === O) {
          var k = M.revertLane;
          if (k === 0)
            y !== null && (y = y.next = {
              lane: 0,
              revertLane: 0,
              gesture: null,
              action: M.action,
              hasEagerState: M.hasEagerState,
              eagerState: M.eagerState,
              next: null
            }), O === rn && (N = !0);
          else if ((Aa & k) === k) {
            M = M.next, k === rn && (N = !0);
            continue;
          } else
            O = {
              lane: 0,
              revertLane: M.revertLane,
              gesture: null,
              action: M.action,
              hasEagerState: M.hasEagerState,
              eagerState: M.eagerState,
              next: null
            }, y === null ? (d = y = O, c = r) : y = y.next = O, ge.lanes |= k, Fa |= k;
          O = M.action, kl && a(r, O), r = M.hasEagerState ? M.eagerState : a(r, O);
        } else
          k = {
            lane: O,
            revertLane: M.revertLane,
            gesture: M.gesture,
            action: M.action,
            hasEagerState: M.hasEagerState,
            eagerState: M.eagerState,
            next: null
          }, y === null ? (d = y = k, c = r) : y = y.next = k, ge.lanes |= O, Fa |= O;
        M = M.next;
      } while (M !== null && M !== t);
      if (y === null ? c = r : y.next = d, !jt(r, e.memoizedState) && (it = !0, N && (a = on, a !== null)))
        throw a;
      e.memoizedState = r, e.baseState = c, e.baseQueue = y, l.lastRenderedState = r;
    }
    return n === null && (l.lanes = 0), [e.memoizedState, l.dispatch];
  }
  function ec(e) {
    var t = lt(), a = t.queue;
    if (a === null) throw Error(o(311));
    a.lastRenderedReducer = e;
    var l = a.dispatch, n = a.pending, r = t.memoizedState;
    if (n !== null) {
      a.pending = null;
      var c = n = n.next;
      do
        r = e(r, c.action), c = c.next;
      while (c !== n);
      jt(r, t.memoizedState) || (it = !0), t.memoizedState = r, t.baseQueue === null && (t.baseState = r), a.lastRenderedState = r;
    }
    return [r, l];
  }
  function Yu(e, t, a) {
    var l = ge, n = lt(), r = ze;
    if (r) {
      if (a === void 0) throw Error(o(407));
      a = a();
    } else a = t();
    var c = !jt(
      (Ue || n).memoizedState,
      a
    );
    if (c && (n.memoizedState = a, it = !0), n = n.queue, lc(Ku.bind(null, l, n, e), [
      e
    ]), n.getSnapshot !== t || c || rt !== null && rt.memoizedState.tag & 1) {
      if (l.flags |= 2048, mn(
        9,
        { destroy: void 0 },
        Qu.bind(
          null,
          l,
          n,
          a,
          t
        ),
        null
      ), Xe === null) throw Error(o(349));
      r || (Aa & 127) !== 0 || Xu(l, t, a);
    }
    return a;
  }
  function Xu(e, t, a) {
    e.flags |= 16384, e = { getSnapshot: t, value: a }, t = ge.updateQueue, t === null ? (t = ci(), ge.updateQueue = t, t.stores = [e]) : (a = t.stores, a === null ? t.stores = [e] : a.push(e));
  }
  function Qu(e, t, a, l) {
    t.value = a, t.getSnapshot = l, Ju(t) && $u(e);
  }
  function Ku(e, t, a) {
    return a(function() {
      Ju(t) && $u(e);
    });
  }
  function Ju(e) {
    var t = e.getSnapshot;
    e = e.value;
    try {
      var a = t();
      return !jt(e, a);
    } catch {
      return !0;
    }
  }
  function $u(e) {
    var t = bl(e, 2);
    t !== null && _t(t, e, 2);
  }
  function tc(e) {
    var t = Mt();
    if (typeof e == "function") {
      var a = e;
      if (e = a(), kl) {
        oa(!0);
        try {
          a();
        } finally {
          oa(!1);
        }
      }
    }
    return t.memoizedState = t.baseState = e, t.queue = {
      pending: null,
      lanes: 0,
      dispatch: null,
      lastRenderedReducer: Ma,
      lastRenderedState: e
    }, t;
  }
  function Fu(e, t, a, l) {
    return e.baseState = a, Po(
      e,
      Ue,
      typeof l == "function" ? l : Ma
    );
  }
  function mp(e, t, a, l, n) {
    if (mi(e)) throw Error(o(485));
    if (e = t.action, e !== null) {
      var r = {
        payload: n,
        action: e,
        next: null,
        isTransition: !0,
        status: "pending",
        value: null,
        reason: null,
        listeners: [],
        then: function(c) {
          r.listeners.push(c);
        }
      };
      z.T !== null ? a(!0) : r.isTransition = !1, l(r), a = t.pending, a === null ? (r.next = t.pending = r, Wu(t, r)) : (r.next = a.next, t.pending = a.next = r);
    }
  }
  function Wu(e, t) {
    var a = t.action, l = t.payload, n = e.state;
    if (t.isTransition) {
      var r = z.T, c = {};
      z.T = c;
      try {
        var d = a(n, l), y = z.S;
        y !== null && y(c, d), Iu(e, t, d);
      } catch (M) {
        ac(e, t, M);
      } finally {
        r !== null && c.types !== null && (r.types = c.types), z.T = r;
      }
    } else
      try {
        r = a(n, l), Iu(e, t, r);
      } catch (M) {
        ac(e, t, M);
      }
  }
  function Iu(e, t, a) {
    a !== null && typeof a == "object" && typeof a.then == "function" ? a.then(
      function(l) {
        Pu(e, t, l);
      },
      function(l) {
        return ac(e, t, l);
      }
    ) : Pu(e, t, a);
  }
  function Pu(e, t, a) {
    t.status = "fulfilled", t.value = a, e0(t), e.state = a, t = e.pending, t !== null && (a = t.next, a === t ? e.pending = null : (a = a.next, t.next = a, Wu(e, a)));
  }
  function ac(e, t, a) {
    var l = e.pending;
    if (e.pending = null, l !== null) {
      l = l.next;
      do
        t.status = "rejected", t.reason = a, e0(t), t = t.next;
      while (t !== l);
    }
    e.action = null;
  }
  function e0(e) {
    e = e.listeners;
    for (var t = 0; t < e.length; t++) (0, e[t])();
  }
  function t0(e, t) {
    return t;
  }
  function a0(e, t) {
    if (ze) {
      var a = Xe.formState;
      if (a !== null) {
        e: {
          var l = ge;
          if (ze) {
            if (Je) {
              t: {
                for (var n = Je, r = $t; n.nodeType !== 8; ) {
                  if (!r) {
                    n = null;
                    break t;
                  }
                  if (n = Wt(
                    n.nextSibling
                  ), n === null) {
                    n = null;
                    break t;
                  }
                }
                r = n.data, n = r === "F!" || r === "F" ? n : null;
              }
              if (n) {
                Je = Wt(
                  n.nextSibling
                ), l = n.data === "F!";
                break e;
              }
            }
            qa(l);
          }
          l = !1;
        }
        l && (t = a[0]);
      }
    }
    return a = Mt(), a.memoizedState = a.baseState = t, l = {
      pending: null,
      lanes: 0,
      dispatch: null,
      lastRenderedReducer: t0,
      lastRenderedState: t
    }, a.queue = l, a = w0.bind(
      null,
      ge,
      l
    ), l.dispatch = a, l = tc(!1), r = cc.bind(
      null,
      ge,
      !1,
      l.queue
    ), l = Mt(), n = {
      state: t,
      dispatch: null,
      action: e,
      pending: null
    }, l.queue = n, a = mp.bind(
      null,
      ge,
      n,
      r,
      a
    ), n.dispatch = a, l.memoizedState = e, [t, a, !1];
  }
  function l0(e) {
    var t = lt();
    return n0(t, Ue, e);
  }
  function n0(e, t, a) {
    if (t = Po(
      e,
      t,
      t0
    )[0], e = ui(Ma)[0], typeof t == "object" && t !== null && typeof t.then == "function")
      try {
        var l = ar(t);
      } catch (c) {
        throw c === cn ? ei : c;
      }
    else l = t;
    t = lt();
    var n = t.queue, r = n.dispatch;
    return a !== t.memoizedState && (ge.flags |= 2048, mn(
      9,
      { destroy: void 0 },
      pp.bind(null, n, a),
      null
    )), [l, r, e];
  }
  function pp(e, t) {
    e.action = t;
  }
  function r0(e) {
    var t = lt(), a = Ue;
    if (a !== null)
      return n0(t, a, e);
    lt(), t = t.memoizedState, a = lt();
    var l = a.queue.dispatch;
    return a.memoizedState = e, [t, l, !1];
  }
  function mn(e, t, a, l) {
    return e = { tag: e, create: a, deps: l, inst: t, next: null }, t = ge.updateQueue, t === null && (t = ci(), ge.updateQueue = t), a = t.lastEffect, a === null ? t.lastEffect = e.next = e : (l = a.next, a.next = e, e.next = l, t.lastEffect = e), e;
  }
  function i0() {
    return lt().memoizedState;
  }
  function fi(e, t, a, l) {
    var n = Mt();
    ge.flags |= e, n.memoizedState = mn(
      1 | t,
      { destroy: void 0 },
      a,
      l === void 0 ? null : l
    );
  }
  function di(e, t, a, l) {
    var n = lt();
    l = l === void 0 ? null : l;
    var r = n.memoizedState.inst;
    Ue !== null && l !== null && Ko(l, Ue.memoizedState.deps) ? n.memoizedState = mn(t, r, a, l) : (ge.flags |= e, n.memoizedState = mn(
      1 | t,
      r,
      a,
      l
    ));
  }
  function o0(e, t) {
    fi(8390656, 8, e, t);
  }
  function lc(e, t) {
    di(2048, 8, e, t);
  }
  function hp(e) {
    ge.flags |= 4;
    var t = ge.updateQueue;
    if (t === null)
      t = ci(), ge.updateQueue = t, t.events = [e];
    else {
      var a = t.events;
      a === null ? t.events = [e] : a.push(e);
    }
  }
  function c0(e) {
    var t = lt().memoizedState;
    return hp({ ref: t, nextImpl: e }), function() {
      if ((je & 2) !== 0) throw Error(o(440));
      return t.impl.apply(void 0, arguments);
    };
  }
  function s0(e, t) {
    return di(4, 2, e, t);
  }
  function u0(e, t) {
    return di(4, 4, e, t);
  }
  function f0(e, t) {
    if (typeof t == "function") {
      e = e();
      var a = t(e);
      return function() {
        typeof a == "function" ? a() : t(null);
      };
    }
    if (t != null)
      return e = e(), t.current = e, function() {
        t.current = null;
      };
  }
  function d0(e, t, a) {
    a = a != null ? a.concat([e]) : null, di(4, 4, f0.bind(null, t, e), a);
  }
  function nc() {
  }
  function m0(e, t) {
    var a = lt();
    t = t === void 0 ? null : t;
    var l = a.memoizedState;
    return t !== null && Ko(t, l[1]) ? l[0] : (a.memoizedState = [e, t], e);
  }
  function p0(e, t) {
    var a = lt();
    t = t === void 0 ? null : t;
    var l = a.memoizedState;
    if (t !== null && Ko(t, l[1]))
      return l[0];
    if (l = e(), kl) {
      oa(!0);
      try {
        e();
      } finally {
        oa(!1);
      }
    }
    return a.memoizedState = [l, t], l;
  }
  function rc(e, t, a) {
    return a === void 0 || (Aa & 1073741824) !== 0 && (Me & 261930) === 0 ? e.memoizedState = t : (e.memoizedState = a, e = h1(), ge.lanes |= e, Fa |= e, a);
  }
  function h0(e, t, a, l) {
    return jt(a, t) ? a : un.current !== null ? (e = rc(e, a, l), jt(e, t) || (it = !0), e) : (Aa & 42) === 0 || (Aa & 1073741824) !== 0 && (Me & 261930) === 0 ? (it = !0, e.memoizedState = a) : (e = h1(), ge.lanes |= e, Fa |= e, t);
  }
  function g0(e, t, a, l, n) {
    var r = q.p;
    q.p = r !== 0 && 8 > r ? r : 8;
    var c = z.T, d = {};
    z.T = d, cc(e, !1, t, a);
    try {
      var y = n(), M = z.S;
      if (M !== null && M(d, y), y !== null && typeof y == "object" && typeof y.then == "function") {
        var N = up(
          y,
          l
        );
        lr(
          e,
          t,
          N,
          Lt(e)
        );
      } else
        lr(
          e,
          t,
          l,
          Lt(e)
        );
    } catch (O) {
      lr(
        e,
        t,
        { then: function() {
        }, status: "rejected", reason: O },
        Lt()
      );
    } finally {
      q.p = r, c !== null && d.types !== null && (c.types = d.types), z.T = c;
    }
  }
  function gp() {
  }
  function ic(e, t, a, l) {
    if (e.tag !== 5) throw Error(o(476));
    var n = b0(e).queue;
    g0(
      e,
      n,
      t,
      ne,
      a === null ? gp : function() {
        return v0(e), a(l);
      }
    );
  }
  function b0(e) {
    var t = e.memoizedState;
    if (t !== null) return t;
    t = {
      memoizedState: ne,
      baseState: ne,
      baseQueue: null,
      queue: {
        pending: null,
        lanes: 0,
        dispatch: null,
        lastRenderedReducer: Ma,
        lastRenderedState: ne
      },
      next: null
    };
    var a = {};
    return t.next = {
      memoizedState: a,
      baseState: a,
      baseQueue: null,
      queue: {
        pending: null,
        lanes: 0,
        dispatch: null,
        lastRenderedReducer: Ma,
        lastRenderedState: a
      },
      next: null
    }, e.memoizedState = t, e = e.alternate, e !== null && (e.memoizedState = t), t;
  }
  function v0(e) {
    var t = b0(e);
    t.next === null && (t = e.alternate.memoizedState), lr(
      e,
      t.next.queue,
      {},
      Lt()
    );
  }
  function oc() {
    return ht(yr);
  }
  function x0() {
    return lt().memoizedState;
  }
  function y0() {
    return lt().memoizedState;
  }
  function bp(e) {
    for (var t = e.return; t !== null; ) {
      switch (t.tag) {
        case 24:
        case 3:
          var a = Lt();
          e = Ya(a);
          var l = Xa(t, e, a);
          l !== null && (_t(l, t, a), In(l, t, a)), t = { cache: Do() }, e.payload = t;
          return;
      }
      t = t.return;
    }
  }
  function vp(e, t, a) {
    var l = Lt();
    a = {
      lane: l,
      revertLane: 0,
      gesture: null,
      action: a,
      hasEagerState: !1,
      eagerState: null,
      next: null
    }, mi(e) ? S0(t, a) : (a = Mo(e, t, a, l), a !== null && (_t(a, e, l), E0(a, t, l)));
  }
  function w0(e, t, a) {
    var l = Lt();
    lr(e, t, a, l);
  }
  function lr(e, t, a, l) {
    var n = {
      lane: l,
      revertLane: 0,
      gesture: null,
      action: a,
      hasEagerState: !1,
      eagerState: null,
      next: null
    };
    if (mi(e)) S0(t, n);
    else {
      var r = e.alternate;
      if (e.lanes === 0 && (r === null || r.lanes === 0) && (r = t.lastRenderedReducer, r !== null))
        try {
          var c = t.lastRenderedState, d = r(c, a);
          if (n.hasEagerState = !0, n.eagerState = d, jt(d, c))
            return Kr(e, t, n, 0), Xe === null && Qr(), !1;
        } catch {
        }
      if (a = Mo(e, t, n, l), a !== null)
        return _t(a, e, l), E0(a, t, l), !0;
    }
    return !1;
  }
  function cc(e, t, a, l) {
    if (l = {
      lane: 2,
      revertLane: Uc(),
      gesture: null,
      action: l,
      hasEagerState: !1,
      eagerState: null,
      next: null
    }, mi(e)) {
      if (t) throw Error(o(479));
    } else
      t = Mo(
        e,
        a,
        l,
        2
      ), t !== null && _t(t, e, 2);
  }
  function mi(e) {
    var t = e.alternate;
    return e === ge || t !== null && t === ge;
  }
  function S0(e, t) {
    fn = ii = !0;
    var a = e.pending;
    a === null ? t.next = t : (t.next = a.next, a.next = t), e.pending = t;
  }
  function E0(e, t, a) {
    if ((a & 4194048) !== 0) {
      var l = t.lanes;
      l &= e.pendingLanes, a |= l, t.lanes = a, ga(e, a);
    }
  }
  var nr = {
    readContext: ht,
    use: si,
    useCallback: Pe,
    useContext: Pe,
    useEffect: Pe,
    useImperativeHandle: Pe,
    useLayoutEffect: Pe,
    useInsertionEffect: Pe,
    useMemo: Pe,
    useReducer: Pe,
    useRef: Pe,
    useState: Pe,
    useDebugValue: Pe,
    useDeferredValue: Pe,
    useTransition: Pe,
    useSyncExternalStore: Pe,
    useId: Pe,
    useHostTransitionStatus: Pe,
    useFormState: Pe,
    useActionState: Pe,
    useOptimistic: Pe,
    useMemoCache: Pe,
    useCacheRefresh: Pe
  };
  nr.useEffectEvent = Pe;
  var A0 = {
    readContext: ht,
    use: si,
    useCallback: function(e, t) {
      return Mt().memoizedState = [
        e,
        t === void 0 ? null : t
      ], e;
    },
    useContext: ht,
    useEffect: o0,
    useImperativeHandle: function(e, t, a) {
      a = a != null ? a.concat([e]) : null, fi(
        4194308,
        4,
        f0.bind(null, t, e),
        a
      );
    },
    useLayoutEffect: function(e, t) {
      return fi(4194308, 4, e, t);
    },
    useInsertionEffect: function(e, t) {
      fi(4, 2, e, t);
    },
    useMemo: function(e, t) {
      var a = Mt();
      t = t === void 0 ? null : t;
      var l = e();
      if (kl) {
        oa(!0);
        try {
          e();
        } finally {
          oa(!1);
        }
      }
      return a.memoizedState = [l, t], l;
    },
    useReducer: function(e, t, a) {
      var l = Mt();
      if (a !== void 0) {
        var n = a(t);
        if (kl) {
          oa(!0);
          try {
            a(t);
          } finally {
            oa(!1);
          }
        }
      } else n = t;
      return l.memoizedState = l.baseState = n, e = {
        pending: null,
        lanes: 0,
        dispatch: null,
        lastRenderedReducer: e,
        lastRenderedState: n
      }, l.queue = e, e = e.dispatch = vp.bind(
        null,
        ge,
        e
      ), [l.memoizedState, e];
    },
    useRef: function(e) {
      var t = Mt();
      return e = { current: e }, t.memoizedState = e;
    },
    useState: function(e) {
      e = tc(e);
      var t = e.queue, a = w0.bind(null, ge, t);
      return t.dispatch = a, [e.memoizedState, a];
    },
    useDebugValue: nc,
    useDeferredValue: function(e, t) {
      var a = Mt();
      return rc(a, e, t);
    },
    useTransition: function() {
      var e = tc(!1);
      return e = g0.bind(
        null,
        ge,
        e.queue,
        !0,
        !1
      ), Mt().memoizedState = e, [!1, e];
    },
    useSyncExternalStore: function(e, t, a) {
      var l = ge, n = Mt();
      if (ze) {
        if (a === void 0)
          throw Error(o(407));
        a = a();
      } else {
        if (a = t(), Xe === null)
          throw Error(o(349));
        (Me & 127) !== 0 || Xu(l, t, a);
      }
      n.memoizedState = a;
      var r = { value: a, getSnapshot: t };
      return n.queue = r, o0(Ku.bind(null, l, r, e), [
        e
      ]), l.flags |= 2048, mn(
        9,
        { destroy: void 0 },
        Qu.bind(
          null,
          l,
          r,
          a,
          t
        ),
        null
      ), a;
    },
    useId: function() {
      var e = Mt(), t = Xe.identifierPrefix;
      if (ze) {
        var a = ua, l = sa;
        a = (l & ~(1 << 32 - yt(l) - 1)).toString(32) + a, t = "_" + t + "R_" + a, a = oi++, 0 < a && (t += "H" + a.toString(32)), t += "_";
      } else
        a = fp++, t = "_" + t + "r_" + a.toString(32) + "_";
      return e.memoizedState = t;
    },
    useHostTransitionStatus: oc,
    useFormState: a0,
    useActionState: a0,
    useOptimistic: function(e) {
      var t = Mt();
      t.memoizedState = t.baseState = e;
      var a = {
        pending: null,
        lanes: 0,
        dispatch: null,
        lastRenderedReducer: null,
        lastRenderedState: null
      };
      return t.queue = a, t = cc.bind(
        null,
        ge,
        !0,
        a
      ), a.dispatch = t, [e, t];
    },
    useMemoCache: Io,
    useCacheRefresh: function() {
      return Mt().memoizedState = bp.bind(
        null,
        ge
      );
    },
    useEffectEvent: function(e) {
      var t = Mt(), a = { impl: e };
      return t.memoizedState = a, function() {
        if ((je & 2) !== 0)
          throw Error(o(440));
        return a.impl.apply(void 0, arguments);
      };
    }
  }, sc = {
    readContext: ht,
    use: si,
    useCallback: m0,
    useContext: ht,
    useEffect: lc,
    useImperativeHandle: d0,
    useInsertionEffect: s0,
    useLayoutEffect: u0,
    useMemo: p0,
    useReducer: ui,
    useRef: i0,
    useState: function() {
      return ui(Ma);
    },
    useDebugValue: nc,
    useDeferredValue: function(e, t) {
      var a = lt();
      return h0(
        a,
        Ue.memoizedState,
        e,
        t
      );
    },
    useTransition: function() {
      var e = ui(Ma)[0], t = lt().memoizedState;
      return [
        typeof e == "boolean" ? e : ar(e),
        t
      ];
    },
    useSyncExternalStore: Yu,
    useId: x0,
    useHostTransitionStatus: oc,
    useFormState: l0,
    useActionState: l0,
    useOptimistic: function(e, t) {
      var a = lt();
      return Fu(a, Ue, e, t);
    },
    useMemoCache: Io,
    useCacheRefresh: y0
  };
  sc.useEffectEvent = c0;
  var M0 = {
    readContext: ht,
    use: si,
    useCallback: m0,
    useContext: ht,
    useEffect: lc,
    useImperativeHandle: d0,
    useInsertionEffect: s0,
    useLayoutEffect: u0,
    useMemo: p0,
    useReducer: ec,
    useRef: i0,
    useState: function() {
      return ec(Ma);
    },
    useDebugValue: nc,
    useDeferredValue: function(e, t) {
      var a = lt();
      return Ue === null ? rc(a, e, t) : h0(
        a,
        Ue.memoizedState,
        e,
        t
      );
    },
    useTransition: function() {
      var e = ec(Ma)[0], t = lt().memoizedState;
      return [
        typeof e == "boolean" ? e : ar(e),
        t
      ];
    },
    useSyncExternalStore: Yu,
    useId: x0,
    useHostTransitionStatus: oc,
    useFormState: r0,
    useActionState: r0,
    useOptimistic: function(e, t) {
      var a = lt();
      return Ue !== null ? Fu(a, Ue, e, t) : (a.baseState = e, [e, a.queue.dispatch]);
    },
    useMemoCache: Io,
    useCacheRefresh: y0
  };
  M0.useEffectEvent = c0;
  function uc(e, t, a, l) {
    t = e.memoizedState, a = a(l, t), a = a == null ? t : C({}, t, a), e.memoizedState = a, e.lanes === 0 && (e.updateQueue.baseState = a);
  }
  var fc = {
    enqueueSetState: function(e, t, a) {
      e = e._reactInternals;
      var l = Lt(), n = Ya(l);
      n.payload = t, a != null && (n.callback = a), t = Xa(e, n, l), t !== null && (_t(t, e, l), In(t, e, l));
    },
    enqueueReplaceState: function(e, t, a) {
      e = e._reactInternals;
      var l = Lt(), n = Ya(l);
      n.tag = 1, n.payload = t, a != null && (n.callback = a), t = Xa(e, n, l), t !== null && (_t(t, e, l), In(t, e, l));
    },
    enqueueForceUpdate: function(e, t) {
      e = e._reactInternals;
      var a = Lt(), l = Ya(a);
      l.tag = 2, t != null && (l.callback = t), t = Xa(e, l, a), t !== null && (_t(t, e, a), In(t, e, a));
    }
  };
  function k0(e, t, a, l, n, r, c) {
    return e = e.stateNode, typeof e.shouldComponentUpdate == "function" ? e.shouldComponentUpdate(l, r, c) : t.prototype && t.prototype.isPureReactComponent ? !Yn(a, l) || !Yn(n, r) : !0;
  }
  function z0(e, t, a, l) {
    e = t.state, typeof t.componentWillReceiveProps == "function" && t.componentWillReceiveProps(a, l), typeof t.UNSAFE_componentWillReceiveProps == "function" && t.UNSAFE_componentWillReceiveProps(a, l), t.state !== e && fc.enqueueReplaceState(t, t.state, null);
  }
  function zl(e, t) {
    var a = t;
    if ("ref" in t) {
      a = {};
      for (var l in t)
        l !== "ref" && (a[l] = t[l]);
    }
    if (e = e.defaultProps) {
      a === t && (a = C({}, a));
      for (var n in e)
        a[n] === void 0 && (a[n] = e[n]);
    }
    return a;
  }
  function H0(e) {
    Xr(e);
  }
  function T0(e) {
    console.error(e);
  }
  function N0(e) {
    Xr(e);
  }
  function pi(e, t) {
    try {
      var a = e.onUncaughtError;
      a(t.value, { componentStack: t.stack });
    } catch (l) {
      setTimeout(function() {
        throw l;
      });
    }
  }
  function C0(e, t, a) {
    try {
      var l = e.onCaughtError;
      l(a.value, {
        componentStack: a.stack,
        errorBoundary: t.tag === 1 ? t.stateNode : null
      });
    } catch (n) {
      setTimeout(function() {
        throw n;
      });
    }
  }
  function dc(e, t, a) {
    return a = Ya(a), a.tag = 3, a.payload = { element: null }, a.callback = function() {
      pi(e, t);
    }, a;
  }
  function _0(e) {
    return e = Ya(e), e.tag = 3, e;
  }
  function j0(e, t, a, l) {
    var n = a.type.getDerivedStateFromError;
    if (typeof n == "function") {
      var r = l.value;
      e.payload = function() {
        return n(r);
      }, e.callback = function() {
        C0(t, a, l);
      };
    }
    var c = a.stateNode;
    c !== null && typeof c.componentDidCatch == "function" && (e.callback = function() {
      C0(t, a, l), typeof n != "function" && (Wa === null ? Wa = /* @__PURE__ */ new Set([this]) : Wa.add(this));
      var d = l.stack;
      this.componentDidCatch(l.value, {
        componentStack: d !== null ? d : ""
      });
    });
  }
  function xp(e, t, a, l, n) {
    if (a.flags |= 32768, l !== null && typeof l == "object" && typeof l.then == "function") {
      if (t = a.alternate, t !== null && nn(
        t,
        a,
        n,
        !0
      ), a = Zt.current, a !== null) {
        switch (a.tag) {
          case 31:
          case 13:
            return Ft === null ? ki() : a.alternate === null && et === 0 && (et = 3), a.flags &= -257, a.flags |= 65536, a.lanes = n, l === ti ? a.flags |= 16384 : (t = a.updateQueue, t === null ? a.updateQueue = /* @__PURE__ */ new Set([l]) : t.add(l), Rc(e, l, n)), !1;
          case 22:
            return a.flags |= 65536, l === ti ? a.flags |= 16384 : (t = a.updateQueue, t === null ? (t = {
              transitions: null,
              markerInstances: null,
              retryQueue: /* @__PURE__ */ new Set([l])
            }, a.updateQueue = t) : (a = t.retryQueue, a === null ? t.retryQueue = /* @__PURE__ */ new Set([l]) : a.add(l)), Rc(e, l, n)), !1;
        }
        throw Error(o(435, a.tag));
      }
      return Rc(e, l, n), ki(), !1;
    }
    if (ze)
      return t = Zt.current, t !== null ? ((t.flags & 65536) === 0 && (t.flags |= 256), t.flags |= 65536, t.lanes = n, l !== Co && (e = Error(o(422), { cause: l }), Kn(Qt(e, a)))) : (l !== Co && (t = Error(o(423), {
        cause: l
      }), Kn(
        Qt(t, a)
      )), e = e.current.alternate, e.flags |= 65536, n &= -n, e.lanes |= n, l = Qt(l, a), n = dc(
        e.stateNode,
        l,
        n
      ), Bo(e, n), et !== 4 && (et = 2)), !1;
    var r = Error(o(520), { cause: l });
    if (r = Qt(r, a), dr === null ? dr = [r] : dr.push(r), et !== 4 && (et = 2), t === null) return !0;
    l = Qt(l, a), a = t;
    do {
      switch (a.tag) {
        case 3:
          return a.flags |= 65536, e = n & -n, a.lanes |= e, e = dc(a.stateNode, l, e), Bo(a, e), !1;
        case 1:
          if (t = a.type, r = a.stateNode, (a.flags & 128) === 0 && (typeof t.getDerivedStateFromError == "function" || r !== null && typeof r.componentDidCatch == "function" && (Wa === null || !Wa.has(r))))
            return a.flags |= 65536, n &= -n, a.lanes |= n, n = _0(n), j0(
              n,
              e,
              a,
              l
            ), Bo(a, n), !1;
      }
      a = a.return;
    } while (a !== null);
    return !1;
  }
  var mc = Error(o(461)), it = !1;
  function gt(e, t, a, l) {
    t.child = e === null ? Ru(t, null, a, l) : Ml(
      t,
      e.child,
      a,
      l
    );
  }
  function O0(e, t, a, l, n) {
    a = a.render;
    var r = t.ref;
    if ("ref" in l) {
      var c = {};
      for (var d in l)
        d !== "ref" && (c[d] = l[d]);
    } else c = l;
    return wl(t), l = Jo(
      e,
      t,
      a,
      c,
      r,
      n
    ), d = $o(), e !== null && !it ? (Fo(e, t, n), ka(e, t, n)) : (ze && d && To(t), t.flags |= 1, gt(e, t, l, n), t.child);
  }
  function Z0(e, t, a, l, n) {
    if (e === null) {
      var r = a.type;
      return typeof r == "function" && !ko(r) && r.defaultProps === void 0 && a.compare === null ? (t.tag = 15, t.type = r, D0(
        e,
        t,
        r,
        l,
        n
      )) : (e = $r(
        a.type,
        null,
        l,
        t,
        t.mode,
        n
      ), e.ref = t.ref, e.return = t, t.child = e);
    }
    if (r = e.child, !wc(e, n)) {
      var c = r.memoizedProps;
      if (a = a.compare, a = a !== null ? a : Yn, a(c, l) && e.ref === t.ref)
        return ka(e, t, n);
    }
    return t.flags |= 1, e = ya(r, l), e.ref = t.ref, e.return = t, t.child = e;
  }
  function D0(e, t, a, l, n) {
    if (e !== null) {
      var r = e.memoizedProps;
      if (Yn(r, l) && e.ref === t.ref)
        if (it = !1, t.pendingProps = l = r, wc(e, n))
          (e.flags & 131072) !== 0 && (it = !0);
        else
          return t.lanes = e.lanes, ka(e, t, n);
    }
    return pc(
      e,
      t,
      a,
      l,
      n
    );
  }
  function R0(e, t, a, l) {
    var n = l.children, r = e !== null ? e.memoizedState : null;
    if (e === null && t.stateNode === null && (t.stateNode = {
      _visibility: 1,
      _pendingMarkers: null,
      _retryCache: null,
      _transitions: null
    }), l.mode === "hidden") {
      if ((t.flags & 128) !== 0) {
        if (r = r !== null ? r.baseLanes | a : a, e !== null) {
          for (l = t.child = e.child, n = 0; l !== null; )
            n = n | l.lanes | l.childLanes, l = l.sibling;
          l = n & ~r;
        } else l = 0, t.child = null;
        return V0(
          e,
          t,
          r,
          a,
          l
        );
      }
      if ((a & 536870912) !== 0)
        t.memoizedState = { baseLanes: 0, cachePool: null }, e !== null && Pr(
          t,
          r !== null ? r.cachePool : null
        ), r !== null ? Uu(t, r) : Yo(), qu(t);
      else
        return l = t.lanes = 536870912, V0(
          e,
          t,
          r !== null ? r.baseLanes | a : a,
          a,
          l
        );
    } else
      r !== null ? (Pr(t, r.cachePool), Uu(t, r), Ka(), t.memoizedState = null) : (e !== null && Pr(t, null), Yo(), Ka());
    return gt(e, t, n, a), t.child;
  }
  function rr(e, t) {
    return e !== null && e.tag === 22 || t.stateNode !== null || (t.stateNode = {
      _visibility: 1,
      _pendingMarkers: null,
      _retryCache: null,
      _transitions: null
    }), t.sibling;
  }
  function V0(e, t, a, l, n) {
    var r = Vo();
    return r = r === null ? null : { parent: nt._currentValue, pool: r }, t.memoizedState = {
      baseLanes: a,
      cachePool: r
    }, e !== null && Pr(t, null), Yo(), qu(t), e !== null && nn(e, t, l, !0), t.childLanes = n, null;
  }
  function hi(e, t) {
    return t = bi(
      { mode: t.mode, children: t.children },
      e.mode
    ), t.ref = e.ref, e.child = t, t.return = e, t;
  }
  function L0(e, t, a) {
    return Ml(t, e.child, null, a), e = hi(t, t.pendingProps), e.flags |= 2, Dt(t), t.memoizedState = null, e;
  }
  function yp(e, t, a) {
    var l = t.pendingProps, n = (t.flags & 128) !== 0;
    if (t.flags &= -129, e === null) {
      if (ze) {
        if (l.mode === "hidden")
          return e = hi(t, l), t.lanes = 536870912, rr(null, e);
        if (Qo(t), (e = Je) ? (e = W1(
          e,
          $t
        ), e = e !== null && e.data === "&" ? e : null, e !== null && (t.memoizedState = {
          dehydrated: e,
          treeContext: La !== null ? { id: sa, overflow: ua } : null,
          retryLane: 536870912,
          hydrationErrors: null
        }, a = Su(e), a.return = t, t.child = a, pt = t, Je = null)) : e = null, e === null) throw qa(t);
        return t.lanes = 536870912, null;
      }
      return hi(t, l);
    }
    var r = e.memoizedState;
    if (r !== null) {
      var c = r.dehydrated;
      if (Qo(t), n)
        if (t.flags & 256)
          t.flags &= -257, t = L0(
            e,
            t,
            a
          );
        else if (t.memoizedState !== null)
          t.child = e.child, t.flags |= 128, t = null;
        else throw Error(o(558));
      else if (it || nn(e, t, a, !1), n = (a & e.childLanes) !== 0, it || n) {
        if (l = Xe, l !== null && (c = Or(l, a), c !== 0 && c !== r.retryLane))
          throw r.retryLane = c, bl(e, c), _t(l, e, c), mc;
        ki(), t = L0(
          e,
          t,
          a
        );
      } else
        e = r.treeContext, Je = Wt(c.nextSibling), pt = t, ze = !0, Ua = null, $t = !1, e !== null && Mu(t, e), t = hi(t, l), t.flags |= 4096;
      return t;
    }
    return e = ya(e.child, {
      mode: l.mode,
      children: l.children
    }), e.ref = t.ref, t.child = e, e.return = t, e;
  }
  function gi(e, t) {
    var a = t.ref;
    if (a === null)
      e !== null && e.ref !== null && (t.flags |= 4194816);
    else {
      if (typeof a != "function" && typeof a != "object")
        throw Error(o(284));
      (e === null || e.ref !== a) && (t.flags |= 4194816);
    }
  }
  function pc(e, t, a, l, n) {
    return wl(t), a = Jo(
      e,
      t,
      a,
      l,
      void 0,
      n
    ), l = $o(), e !== null && !it ? (Fo(e, t, n), ka(e, t, n)) : (ze && l && To(t), t.flags |= 1, gt(e, t, a, n), t.child);
  }
  function U0(e, t, a, l, n, r) {
    return wl(t), t.updateQueue = null, a = Gu(
      t,
      l,
      a,
      n
    ), Bu(e), l = $o(), e !== null && !it ? (Fo(e, t, r), ka(e, t, r)) : (ze && l && To(t), t.flags |= 1, gt(e, t, a, r), t.child);
  }
  function q0(e, t, a, l, n) {
    if (wl(t), t.stateNode === null) {
      var r = en, c = a.contextType;
      typeof c == "object" && c !== null && (r = ht(c)), r = new a(l, r), t.memoizedState = r.state !== null && r.state !== void 0 ? r.state : null, r.updater = fc, t.stateNode = r, r._reactInternals = t, r = t.stateNode, r.props = l, r.state = t.memoizedState, r.refs = {}, Uo(t), c = a.contextType, r.context = typeof c == "object" && c !== null ? ht(c) : en, r.state = t.memoizedState, c = a.getDerivedStateFromProps, typeof c == "function" && (uc(
        t,
        a,
        c,
        l
      ), r.state = t.memoizedState), typeof a.getDerivedStateFromProps == "function" || typeof r.getSnapshotBeforeUpdate == "function" || typeof r.UNSAFE_componentWillMount != "function" && typeof r.componentWillMount != "function" || (c = r.state, typeof r.componentWillMount == "function" && r.componentWillMount(), typeof r.UNSAFE_componentWillMount == "function" && r.UNSAFE_componentWillMount(), c !== r.state && fc.enqueueReplaceState(r, r.state, null), er(t, l, r, n), Pn(), r.state = t.memoizedState), typeof r.componentDidMount == "function" && (t.flags |= 4194308), l = !0;
    } else if (e === null) {
      r = t.stateNode;
      var d = t.memoizedProps, y = zl(a, d);
      r.props = y;
      var M = r.context, N = a.contextType;
      c = en, typeof N == "object" && N !== null && (c = ht(N));
      var O = a.getDerivedStateFromProps;
      N = typeof O == "function" || typeof r.getSnapshotBeforeUpdate == "function", d = t.pendingProps !== d, N || typeof r.UNSAFE_componentWillReceiveProps != "function" && typeof r.componentWillReceiveProps != "function" || (d || M !== c) && z0(
        t,
        r,
        l,
        c
      ), Ga = !1;
      var k = t.memoizedState;
      r.state = k, er(t, l, r, n), Pn(), M = t.memoizedState, d || k !== M || Ga ? (typeof O == "function" && (uc(
        t,
        a,
        O,
        l
      ), M = t.memoizedState), (y = Ga || k0(
        t,
        a,
        y,
        l,
        k,
        M,
        c
      )) ? (N || typeof r.UNSAFE_componentWillMount != "function" && typeof r.componentWillMount != "function" || (typeof r.componentWillMount == "function" && r.componentWillMount(), typeof r.UNSAFE_componentWillMount == "function" && r.UNSAFE_componentWillMount()), typeof r.componentDidMount == "function" && (t.flags |= 4194308)) : (typeof r.componentDidMount == "function" && (t.flags |= 4194308), t.memoizedProps = l, t.memoizedState = M), r.props = l, r.state = M, r.context = c, l = y) : (typeof r.componentDidMount == "function" && (t.flags |= 4194308), l = !1);
    } else {
      r = t.stateNode, qo(e, t), c = t.memoizedProps, N = zl(a, c), r.props = N, O = t.pendingProps, k = r.context, M = a.contextType, y = en, typeof M == "object" && M !== null && (y = ht(M)), d = a.getDerivedStateFromProps, (M = typeof d == "function" || typeof r.getSnapshotBeforeUpdate == "function") || typeof r.UNSAFE_componentWillReceiveProps != "function" && typeof r.componentWillReceiveProps != "function" || (c !== O || k !== y) && z0(
        t,
        r,
        l,
        y
      ), Ga = !1, k = t.memoizedState, r.state = k, er(t, l, r, n), Pn();
      var H = t.memoizedState;
      c !== O || k !== H || Ga || e !== null && e.dependencies !== null && Wr(e.dependencies) ? (typeof d == "function" && (uc(
        t,
        a,
        d,
        l
      ), H = t.memoizedState), (N = Ga || k0(
        t,
        a,
        N,
        l,
        k,
        H,
        y
      ) || e !== null && e.dependencies !== null && Wr(e.dependencies)) ? (M || typeof r.UNSAFE_componentWillUpdate != "function" && typeof r.componentWillUpdate != "function" || (typeof r.componentWillUpdate == "function" && r.componentWillUpdate(l, H, y), typeof r.UNSAFE_componentWillUpdate == "function" && r.UNSAFE_componentWillUpdate(
        l,
        H,
        y
      )), typeof r.componentDidUpdate == "function" && (t.flags |= 4), typeof r.getSnapshotBeforeUpdate == "function" && (t.flags |= 1024)) : (typeof r.componentDidUpdate != "function" || c === e.memoizedProps && k === e.memoizedState || (t.flags |= 4), typeof r.getSnapshotBeforeUpdate != "function" || c === e.memoizedProps && k === e.memoizedState || (t.flags |= 1024), t.memoizedProps = l, t.memoizedState = H), r.props = l, r.state = H, r.context = y, l = N) : (typeof r.componentDidUpdate != "function" || c === e.memoizedProps && k === e.memoizedState || (t.flags |= 4), typeof r.getSnapshotBeforeUpdate != "function" || c === e.memoizedProps && k === e.memoizedState || (t.flags |= 1024), l = !1);
    }
    return r = l, gi(e, t), l = (t.flags & 128) !== 0, r || l ? (r = t.stateNode, a = l && typeof a.getDerivedStateFromError != "function" ? null : r.render(), t.flags |= 1, e !== null && l ? (t.child = Ml(
      t,
      e.child,
      null,
      n
    ), t.child = Ml(
      t,
      null,
      a,
      n
    )) : gt(e, t, a, n), t.memoizedState = r.state, e = t.child) : e = ka(
      e,
      t,
      n
    ), e;
  }
  function B0(e, t, a, l) {
    return xl(), t.flags |= 256, gt(e, t, a, l), t.child;
  }
  var hc = {
    dehydrated: null,
    treeContext: null,
    retryLane: 0,
    hydrationErrors: null
  };
  function gc(e) {
    return { baseLanes: e, cachePool: Cu() };
  }
  function bc(e, t, a) {
    return e = e !== null ? e.childLanes & ~a : 0, t && (e |= Vt), e;
  }
  function G0(e, t, a) {
    var l = t.pendingProps, n = !1, r = (t.flags & 128) !== 0, c;
    if ((c = r) || (c = e !== null && e.memoizedState === null ? !1 : (at.current & 2) !== 0), c && (n = !0, t.flags &= -129), c = (t.flags & 32) !== 0, t.flags &= -33, e === null) {
      if (ze) {
        if (n ? Qa(t) : Ka(), (e = Je) ? (e = W1(
          e,
          $t
        ), e = e !== null && e.data !== "&" ? e : null, e !== null && (t.memoizedState = {
          dehydrated: e,
          treeContext: La !== null ? { id: sa, overflow: ua } : null,
          retryLane: 536870912,
          hydrationErrors: null
        }, a = Su(e), a.return = t, t.child = a, pt = t, Je = null)) : e = null, e === null) throw qa(t);
        return Pc(e) ? t.lanes = 32 : t.lanes = 536870912, null;
      }
      var d = l.children;
      return l = l.fallback, n ? (Ka(), n = t.mode, d = bi(
        { mode: "hidden", children: d },
        n
      ), l = vl(
        l,
        n,
        a,
        null
      ), d.return = t, l.return = t, d.sibling = l, t.child = d, l = t.child, l.memoizedState = gc(a), l.childLanes = bc(
        e,
        c,
        a
      ), t.memoizedState = hc, rr(null, l)) : (Qa(t), vc(t, d));
    }
    var y = e.memoizedState;
    if (y !== null && (d = y.dehydrated, d !== null)) {
      if (r)
        t.flags & 256 ? (Qa(t), t.flags &= -257, t = xc(
          e,
          t,
          a
        )) : t.memoizedState !== null ? (Ka(), t.child = e.child, t.flags |= 128, t = null) : (Ka(), d = l.fallback, n = t.mode, l = bi(
          { mode: "visible", children: l.children },
          n
        ), d = vl(
          d,
          n,
          a,
          null
        ), d.flags |= 2, l.return = t, d.return = t, l.sibling = d, t.child = l, Ml(
          t,
          e.child,
          null,
          a
        ), l = t.child, l.memoizedState = gc(a), l.childLanes = bc(
          e,
          c,
          a
        ), t.memoizedState = hc, t = rr(null, l));
      else if (Qa(t), Pc(d)) {
        if (c = d.nextSibling && d.nextSibling.dataset, c) var M = c.dgst;
        c = M, l = Error(o(419)), l.stack = "", l.digest = c, Kn({ value: l, source: null, stack: null }), t = xc(
          e,
          t,
          a
        );
      } else if (it || nn(e, t, a, !1), c = (a & e.childLanes) !== 0, it || c) {
        if (c = Xe, c !== null && (l = Or(c, a), l !== 0 && l !== y.retryLane))
          throw y.retryLane = l, bl(e, l), _t(c, e, l), mc;
        Ic(d) || ki(), t = xc(
          e,
          t,
          a
        );
      } else
        Ic(d) ? (t.flags |= 192, t.child = e.child, t = null) : (e = y.treeContext, Je = Wt(
          d.nextSibling
        ), pt = t, ze = !0, Ua = null, $t = !1, e !== null && Mu(t, e), t = vc(
          t,
          l.children
        ), t.flags |= 4096);
      return t;
    }
    return n ? (Ka(), d = l.fallback, n = t.mode, y = e.child, M = y.sibling, l = ya(y, {
      mode: "hidden",
      children: l.children
    }), l.subtreeFlags = y.subtreeFlags & 65011712, M !== null ? d = ya(
      M,
      d
    ) : (d = vl(
      d,
      n,
      a,
      null
    ), d.flags |= 2), d.return = t, l.return = t, l.sibling = d, t.child = l, rr(null, l), l = t.child, d = e.child.memoizedState, d === null ? d = gc(a) : (n = d.cachePool, n !== null ? (y = nt._currentValue, n = n.parent !== y ? { parent: y, pool: y } : n) : n = Cu(), d = {
      baseLanes: d.baseLanes | a,
      cachePool: n
    }), l.memoizedState = d, l.childLanes = bc(
      e,
      c,
      a
    ), t.memoizedState = hc, rr(e.child, l)) : (Qa(t), a = e.child, e = a.sibling, a = ya(a, {
      mode: "visible",
      children: l.children
    }), a.return = t, a.sibling = null, e !== null && (c = t.deletions, c === null ? (t.deletions = [e], t.flags |= 16) : c.push(e)), t.child = a, t.memoizedState = null, a);
  }
  function vc(e, t) {
    return t = bi(
      { mode: "visible", children: t },
      e.mode
    ), t.return = e, e.child = t;
  }
  function bi(e, t) {
    return e = Ot(22, e, null, t), e.lanes = 0, e;
  }
  function xc(e, t, a) {
    return Ml(t, e.child, null, a), e = vc(
      t,
      t.pendingProps.children
    ), e.flags |= 2, t.memoizedState = null, e;
  }
  function Y0(e, t, a) {
    e.lanes |= t;
    var l = e.alternate;
    l !== null && (l.lanes |= t), Oo(e.return, t, a);
  }
  function yc(e, t, a, l, n, r) {
    var c = e.memoizedState;
    c === null ? e.memoizedState = {
      isBackwards: t,
      rendering: null,
      renderingStartTime: 0,
      last: l,
      tail: a,
      tailMode: n,
      treeForkCount: r
    } : (c.isBackwards = t, c.rendering = null, c.renderingStartTime = 0, c.last = l, c.tail = a, c.tailMode = n, c.treeForkCount = r);
  }
  function X0(e, t, a) {
    var l = t.pendingProps, n = l.revealOrder, r = l.tail;
    l = l.children;
    var c = at.current, d = (c & 2) !== 0;
    if (d ? (c = c & 1 | 2, t.flags |= 128) : c &= 1, Y(at, c), gt(e, t, l, a), l = ze ? Qn : 0, !d && e !== null && (e.flags & 128) !== 0)
      e: for (e = t.child; e !== null; ) {
        if (e.tag === 13)
          e.memoizedState !== null && Y0(e, a, t);
        else if (e.tag === 19)
          Y0(e, a, t);
        else if (e.child !== null) {
          e.child.return = e, e = e.child;
          continue;
        }
        if (e === t) break e;
        for (; e.sibling === null; ) {
          if (e.return === null || e.return === t)
            break e;
          e = e.return;
        }
        e.sibling.return = e.return, e = e.sibling;
      }
    switch (n) {
      case "forwards":
        for (a = t.child, n = null; a !== null; )
          e = a.alternate, e !== null && ri(e) === null && (n = a), a = a.sibling;
        a = n, a === null ? (n = t.child, t.child = null) : (n = a.sibling, a.sibling = null), yc(
          t,
          !1,
          n,
          a,
          r,
          l
        );
        break;
      case "backwards":
      case "unstable_legacy-backwards":
        for (a = null, n = t.child, t.child = null; n !== null; ) {
          if (e = n.alternate, e !== null && ri(e) === null) {
            t.child = n;
            break;
          }
          e = n.sibling, n.sibling = a, a = n, n = e;
        }
        yc(
          t,
          !0,
          a,
          null,
          r,
          l
        );
        break;
      case "together":
        yc(
          t,
          !1,
          null,
          null,
          void 0,
          l
        );
        break;
      default:
        t.memoizedState = null;
    }
    return t.child;
  }
  function ka(e, t, a) {
    if (e !== null && (t.dependencies = e.dependencies), Fa |= t.lanes, (a & t.childLanes) === 0)
      if (e !== null) {
        if (nn(
          e,
          t,
          a,
          !1
        ), (a & t.childLanes) === 0)
          return null;
      } else return null;
    if (e !== null && t.child !== e.child)
      throw Error(o(153));
    if (t.child !== null) {
      for (e = t.child, a = ya(e, e.pendingProps), t.child = a, a.return = t; e.sibling !== null; )
        e = e.sibling, a = a.sibling = ya(e, e.pendingProps), a.return = t;
      a.sibling = null;
    }
    return t.child;
  }
  function wc(e, t) {
    return (e.lanes & t) !== 0 ? !0 : (e = e.dependencies, !!(e !== null && Wr(e)));
  }
  function wp(e, t, a) {
    switch (t.tag) {
      case 3:
        Ge(t, t.stateNode.containerInfo), Ba(t, nt, e.memoizedState.cache), xl();
        break;
      case 27:
      case 5:
        Ut(t);
        break;
      case 4:
        Ge(t, t.stateNode.containerInfo);
        break;
      case 10:
        Ba(
          t,
          t.type,
          t.memoizedProps.value
        );
        break;
      case 31:
        if (t.memoizedState !== null)
          return t.flags |= 128, Qo(t), null;
        break;
      case 13:
        var l = t.memoizedState;
        if (l !== null)
          return l.dehydrated !== null ? (Qa(t), t.flags |= 128, null) : (a & t.child.childLanes) !== 0 ? G0(e, t, a) : (Qa(t), e = ka(
            e,
            t,
            a
          ), e !== null ? e.sibling : null);
        Qa(t);
        break;
      case 19:
        var n = (e.flags & 128) !== 0;
        if (l = (a & t.childLanes) !== 0, l || (nn(
          e,
          t,
          a,
          !1
        ), l = (a & t.childLanes) !== 0), n) {
          if (l)
            return X0(
              e,
              t,
              a
            );
          t.flags |= 128;
        }
        if (n = t.memoizedState, n !== null && (n.rendering = null, n.tail = null, n.lastEffect = null), Y(at, at.current), l) break;
        return null;
      case 22:
        return t.lanes = 0, R0(
          e,
          t,
          a,
          t.pendingProps
        );
      case 24:
        Ba(t, nt, e.memoizedState.cache);
    }
    return ka(e, t, a);
  }
  function Q0(e, t, a) {
    if (e !== null)
      if (e.memoizedProps !== t.pendingProps)
        it = !0;
      else {
        if (!wc(e, a) && (t.flags & 128) === 0)
          return it = !1, wp(
            e,
            t,
            a
          );
        it = (e.flags & 131072) !== 0;
      }
    else
      it = !1, ze && (t.flags & 1048576) !== 0 && Au(t, Qn, t.index);
    switch (t.lanes = 0, t.tag) {
      case 16:
        e: {
          var l = t.pendingProps;
          if (e = El(t.elementType), t.type = e, typeof e == "function")
            ko(e) ? (l = zl(e, l), t.tag = 1, t = q0(
              null,
              t,
              e,
              l,
              a
            )) : (t.tag = 0, t = pc(
              null,
              t,
              e,
              l,
              a
            ));
          else {
            if (e != null) {
              var n = e.$$typeof;
              if (n === Oe) {
                t.tag = 11, t = O0(
                  null,
                  t,
                  e,
                  l,
                  a
                );
                break e;
              } else if (n === K) {
                t.tag = 14, t = Z0(
                  null,
                  t,
                  e,
                  l,
                  a
                );
                break e;
              }
            }
            throw t = he(e) || e, Error(o(306, t, ""));
          }
        }
        return t;
      case 0:
        return pc(
          e,
          t,
          t.type,
          t.pendingProps,
          a
        );
      case 1:
        return l = t.type, n = zl(
          l,
          t.pendingProps
        ), q0(
          e,
          t,
          l,
          n,
          a
        );
      case 3:
        e: {
          if (Ge(
            t,
            t.stateNode.containerInfo
          ), e === null) throw Error(o(387));
          l = t.pendingProps;
          var r = t.memoizedState;
          n = r.element, qo(e, t), er(t, l, null, a);
          var c = t.memoizedState;
          if (l = c.cache, Ba(t, nt, l), l !== r.cache && Zo(
            t,
            [nt],
            a,
            !0
          ), Pn(), l = c.element, r.isDehydrated)
            if (r = {
              element: l,
              isDehydrated: !1,
              cache: c.cache
            }, t.updateQueue.baseState = r, t.memoizedState = r, t.flags & 256) {
              t = B0(
                e,
                t,
                l,
                a
              );
              break e;
            } else if (l !== n) {
              n = Qt(
                Error(o(424)),
                t
              ), Kn(n), t = B0(
                e,
                t,
                l,
                a
              );
              break e;
            } else
              for (e = t.stateNode.containerInfo, e.nodeType === 9 ? e = e.body : e = e.nodeName === "HTML" ? e.ownerDocument.body : e, Je = Wt(e.firstChild), pt = t, ze = !0, Ua = null, $t = !0, a = Ru(
                t,
                null,
                l,
                a
              ), t.child = a; a; )
                a.flags = a.flags & -3 | 4096, a = a.sibling;
          else {
            if (xl(), l === n) {
              t = ka(
                e,
                t,
                a
              );
              break e;
            }
            gt(e, t, l, a);
          }
          t = t.child;
        }
        return t;
      case 26:
        return gi(e, t), e === null ? (a = lf(
          t.type,
          null,
          t.pendingProps,
          null
        )) ? t.memoizedState = a : ze || (a = t.type, e = t.pendingProps, l = ji(
          le.current
        ).createElement(a), l[Ie] = t, l[mt] = e, bt(l, a, e), ft(l), t.stateNode = l) : t.memoizedState = lf(
          t.type,
          e.memoizedProps,
          t.pendingProps,
          e.memoizedState
        ), null;
      case 27:
        return Ut(t), e === null && ze && (l = t.stateNode = ef(
          t.type,
          t.pendingProps,
          le.current
        ), pt = t, $t = !0, n = Je, tl(t.type) ? (es = n, Je = Wt(l.firstChild)) : Je = n), gt(
          e,
          t,
          t.pendingProps.children,
          a
        ), gi(e, t), e === null && (t.flags |= 4194304), t.child;
      case 5:
        return e === null && ze && ((n = l = Je) && (l = Wp(
          l,
          t.type,
          t.pendingProps,
          $t
        ), l !== null ? (t.stateNode = l, pt = t, Je = Wt(l.firstChild), $t = !1, n = !0) : n = !1), n || qa(t)), Ut(t), n = t.type, r = t.pendingProps, c = e !== null ? e.memoizedProps : null, l = r.children, $c(n, r) ? l = null : c !== null && $c(n, c) && (t.flags |= 32), t.memoizedState !== null && (n = Jo(
          e,
          t,
          dp,
          null,
          null,
          a
        ), yr._currentValue = n), gi(e, t), gt(e, t, l, a), t.child;
      case 6:
        return e === null && ze && ((e = a = Je) && (a = Ip(
          a,
          t.pendingProps,
          $t
        ), a !== null ? (t.stateNode = a, pt = t, Je = null, e = !0) : e = !1), e || qa(t)), null;
      case 13:
        return G0(e, t, a);
      case 4:
        return Ge(
          t,
          t.stateNode.containerInfo
        ), l = t.pendingProps, e === null ? t.child = Ml(
          t,
          null,
          l,
          a
        ) : gt(e, t, l, a), t.child;
      case 11:
        return O0(
          e,
          t,
          t.type,
          t.pendingProps,
          a
        );
      case 7:
        return gt(
          e,
          t,
          t.pendingProps,
          a
        ), t.child;
      case 8:
        return gt(
          e,
          t,
          t.pendingProps.children,
          a
        ), t.child;
      case 12:
        return gt(
          e,
          t,
          t.pendingProps.children,
          a
        ), t.child;
      case 10:
        return l = t.pendingProps, Ba(t, t.type, l.value), gt(e, t, l.children, a), t.child;
      case 9:
        return n = t.type._context, l = t.pendingProps.children, wl(t), n = ht(n), l = l(n), t.flags |= 1, gt(e, t, l, a), t.child;
      case 14:
        return Z0(
          e,
          t,
          t.type,
          t.pendingProps,
          a
        );
      case 15:
        return D0(
          e,
          t,
          t.type,
          t.pendingProps,
          a
        );
      case 19:
        return X0(e, t, a);
      case 31:
        return yp(e, t, a);
      case 22:
        return R0(
          e,
          t,
          a,
          t.pendingProps
        );
      case 24:
        return wl(t), l = ht(nt), e === null ? (n = Vo(), n === null && (n = Xe, r = Do(), n.pooledCache = r, r.refCount++, r !== null && (n.pooledCacheLanes |= a), n = r), t.memoizedState = { parent: l, cache: n }, Uo(t), Ba(t, nt, n)) : ((e.lanes & a) !== 0 && (qo(e, t), er(t, null, null, a), Pn()), n = e.memoizedState, r = t.memoizedState, n.parent !== l ? (n = { parent: l, cache: l }, t.memoizedState = n, t.lanes === 0 && (t.memoizedState = t.updateQueue.baseState = n), Ba(t, nt, l)) : (l = r.cache, Ba(t, nt, l), l !== n.cache && Zo(
          t,
          [nt],
          a,
          !0
        ))), gt(
          e,
          t,
          t.pendingProps.children,
          a
        ), t.child;
      case 29:
        throw t.pendingProps;
    }
    throw Error(o(156, t.tag));
  }
  function za(e) {
    e.flags |= 4;
  }
  function Sc(e, t, a, l, n) {
    if ((t = (e.mode & 32) !== 0) && (t = !1), t) {
      if (e.flags |= 16777216, (n & 335544128) === n)
        if (e.stateNode.complete) e.flags |= 8192;
        else if (x1()) e.flags |= 8192;
        else
          throw Al = ti, Lo;
    } else e.flags &= -16777217;
  }
  function K0(e, t) {
    if (t.type !== "stylesheet" || (t.state.loading & 4) !== 0)
      e.flags &= -16777217;
    else if (e.flags |= 16777216, !sf(t))
      if (x1()) e.flags |= 8192;
      else
        throw Al = ti, Lo;
  }
  function vi(e, t) {
    t !== null && (e.flags |= 4), e.flags & 16384 && (t = e.tag !== 22 ? Bt() : 536870912, e.lanes |= t, bn |= t);
  }
  function ir(e, t) {
    if (!ze)
      switch (e.tailMode) {
        case "hidden":
          t = e.tail;
          for (var a = null; t !== null; )
            t.alternate !== null && (a = t), t = t.sibling;
          a === null ? e.tail = null : a.sibling = null;
          break;
        case "collapsed":
          a = e.tail;
          for (var l = null; a !== null; )
            a.alternate !== null && (l = a), a = a.sibling;
          l === null ? t || e.tail === null ? e.tail = null : e.tail.sibling = null : l.sibling = null;
      }
  }
  function $e(e) {
    var t = e.alternate !== null && e.alternate.child === e.child, a = 0, l = 0;
    if (t)
      for (var n = e.child; n !== null; )
        a |= n.lanes | n.childLanes, l |= n.subtreeFlags & 65011712, l |= n.flags & 65011712, n.return = e, n = n.sibling;
    else
      for (n = e.child; n !== null; )
        a |= n.lanes | n.childLanes, l |= n.subtreeFlags, l |= n.flags, n.return = e, n = n.sibling;
    return e.subtreeFlags |= l, e.childLanes = a, t;
  }
  function Sp(e, t, a) {
    var l = t.pendingProps;
    switch (No(t), t.tag) {
      case 16:
      case 15:
      case 0:
      case 11:
      case 7:
      case 8:
      case 12:
      case 9:
      case 14:
        return $e(t), null;
      case 1:
        return $e(t), null;
      case 3:
        return a = t.stateNode, l = null, e !== null && (l = e.memoizedState.cache), t.memoizedState.cache !== l && (t.flags |= 2048), Ea(nt), me(), a.pendingContext && (a.context = a.pendingContext, a.pendingContext = null), (e === null || e.child === null) && (ln(t) ? za(t) : e === null || e.memoizedState.isDehydrated && (t.flags & 256) === 0 || (t.flags |= 1024, _o())), $e(t), null;
      case 26:
        var n = t.type, r = t.memoizedState;
        return e === null ? (za(t), r !== null ? ($e(t), K0(t, r)) : ($e(t), Sc(
          t,
          n,
          null,
          l,
          a
        ))) : r ? r !== e.memoizedState ? (za(t), $e(t), K0(t, r)) : ($e(t), t.flags &= -16777217) : (e = e.memoizedProps, e !== l && za(t), $e(t), Sc(
          t,
          n,
          e,
          l,
          a
        )), null;
      case 27:
        if (Et(t), a = le.current, n = t.type, e !== null && t.stateNode != null)
          e.memoizedProps !== l && za(t);
        else {
          if (!l) {
            if (t.stateNode === null)
              throw Error(o(166));
            return $e(t), null;
          }
          e = F.current, ln(t) ? ku(t) : (e = ef(n, l, a), t.stateNode = e, za(t));
        }
        return $e(t), null;
      case 5:
        if (Et(t), n = t.type, e !== null && t.stateNode != null)
          e.memoizedProps !== l && za(t);
        else {
          if (!l) {
            if (t.stateNode === null)
              throw Error(o(166));
            return $e(t), null;
          }
          if (r = F.current, ln(t))
            ku(t);
          else {
            var c = ji(
              le.current
            );
            switch (r) {
              case 1:
                r = c.createElementNS(
                  "http://www.w3.org/2000/svg",
                  n
                );
                break;
              case 2:
                r = c.createElementNS(
                  "http://www.w3.org/1998/Math/MathML",
                  n
                );
                break;
              default:
                switch (n) {
                  case "svg":
                    r = c.createElementNS(
                      "http://www.w3.org/2000/svg",
                      n
                    );
                    break;
                  case "math":
                    r = c.createElementNS(
                      "http://www.w3.org/1998/Math/MathML",
                      n
                    );
                    break;
                  case "script":
                    r = c.createElement("div"), r.innerHTML = "<script><\/script>", r = r.removeChild(
                      r.firstChild
                    );
                    break;
                  case "select":
                    r = typeof l.is == "string" ? c.createElement("select", {
                      is: l.is
                    }) : c.createElement("select"), l.multiple ? r.multiple = !0 : l.size && (r.size = l.size);
                    break;
                  default:
                    r = typeof l.is == "string" ? c.createElement(n, { is: l.is }) : c.createElement(n);
                }
            }
            r[Ie] = t, r[mt] = l;
            e: for (c = t.child; c !== null; ) {
              if (c.tag === 5 || c.tag === 6)
                r.appendChild(c.stateNode);
              else if (c.tag !== 4 && c.tag !== 27 && c.child !== null) {
                c.child.return = c, c = c.child;
                continue;
              }
              if (c === t) break e;
              for (; c.sibling === null; ) {
                if (c.return === null || c.return === t)
                  break e;
                c = c.return;
              }
              c.sibling.return = c.return, c = c.sibling;
            }
            t.stateNode = r;
            e: switch (bt(r, n, l), n) {
              case "button":
              case "input":
              case "select":
              case "textarea":
                l = !!l.autoFocus;
                break e;
              case "img":
                l = !0;
                break e;
              default:
                l = !1;
            }
            l && za(t);
          }
        }
        return $e(t), Sc(
          t,
          t.type,
          e === null ? null : e.memoizedProps,
          t.pendingProps,
          a
        ), null;
      case 6:
        if (e && t.stateNode != null)
          e.memoizedProps !== l && za(t);
        else {
          if (typeof l != "string" && t.stateNode === null)
            throw Error(o(166));
          if (e = le.current, ln(t)) {
            if (e = t.stateNode, a = t.memoizedProps, l = null, n = pt, n !== null)
              switch (n.tag) {
                case 27:
                case 5:
                  l = n.memoizedProps;
              }
            e[Ie] = t, e = !!(e.nodeValue === a || l !== null && l.suppressHydrationWarning === !0 || G1(e.nodeValue, a)), e || qa(t, !0);
          } else
            e = ji(e).createTextNode(
              l
            ), e[Ie] = t, t.stateNode = e;
        }
        return $e(t), null;
      case 31:
        if (a = t.memoizedState, e === null || e.memoizedState !== null) {
          if (l = ln(t), a !== null) {
            if (e === null) {
              if (!l) throw Error(o(318));
              if (e = t.memoizedState, e = e !== null ? e.dehydrated : null, !e) throw Error(o(557));
              e[Ie] = t;
            } else
              xl(), (t.flags & 128) === 0 && (t.memoizedState = null), t.flags |= 4;
            $e(t), e = !1;
          } else
            a = _o(), e !== null && e.memoizedState !== null && (e.memoizedState.hydrationErrors = a), e = !0;
          if (!e)
            return t.flags & 256 ? (Dt(t), t) : (Dt(t), null);
          if ((t.flags & 128) !== 0)
            throw Error(o(558));
        }
        return $e(t), null;
      case 13:
        if (l = t.memoizedState, e === null || e.memoizedState !== null && e.memoizedState.dehydrated !== null) {
          if (n = ln(t), l !== null && l.dehydrated !== null) {
            if (e === null) {
              if (!n) throw Error(o(318));
              if (n = t.memoizedState, n = n !== null ? n.dehydrated : null, !n) throw Error(o(317));
              n[Ie] = t;
            } else
              xl(), (t.flags & 128) === 0 && (t.memoizedState = null), t.flags |= 4;
            $e(t), n = !1;
          } else
            n = _o(), e !== null && e.memoizedState !== null && (e.memoizedState.hydrationErrors = n), n = !0;
          if (!n)
            return t.flags & 256 ? (Dt(t), t) : (Dt(t), null);
        }
        return Dt(t), (t.flags & 128) !== 0 ? (t.lanes = a, t) : (a = l !== null, e = e !== null && e.memoizedState !== null, a && (l = t.child, n = null, l.alternate !== null && l.alternate.memoizedState !== null && l.alternate.memoizedState.cachePool !== null && (n = l.alternate.memoizedState.cachePool.pool), r = null, l.memoizedState !== null && l.memoizedState.cachePool !== null && (r = l.memoizedState.cachePool.pool), r !== n && (l.flags |= 2048)), a !== e && a && (t.child.flags |= 8192), vi(t, t.updateQueue), $e(t), null);
      case 4:
        return me(), e === null && Yc(t.stateNode.containerInfo), $e(t), null;
      case 10:
        return Ea(t.type), $e(t), null;
      case 19:
        if (_(at), l = t.memoizedState, l === null) return $e(t), null;
        if (n = (t.flags & 128) !== 0, r = l.rendering, r === null)
          if (n) ir(l, !1);
          else {
            if (et !== 0 || e !== null && (e.flags & 128) !== 0)
              for (e = t.child; e !== null; ) {
                if (r = ri(e), r !== null) {
                  for (t.flags |= 128, ir(l, !1), e = r.updateQueue, t.updateQueue = e, vi(t, e), t.subtreeFlags = 0, e = a, a = t.child; a !== null; )
                    wu(a, e), a = a.sibling;
                  return Y(
                    at,
                    at.current & 1 | 2
                  ), ze && wa(t, l.treeForkCount), t.child;
                }
                e = e.sibling;
              }
            l.tail !== null && Ke() > Ei && (t.flags |= 128, n = !0, ir(l, !1), t.lanes = 4194304);
          }
        else {
          if (!n)
            if (e = ri(r), e !== null) {
              if (t.flags |= 128, n = !0, e = e.updateQueue, t.updateQueue = e, vi(t, e), ir(l, !0), l.tail === null && l.tailMode === "hidden" && !r.alternate && !ze)
                return $e(t), null;
            } else
              2 * Ke() - l.renderingStartTime > Ei && a !== 536870912 && (t.flags |= 128, n = !0, ir(l, !1), t.lanes = 4194304);
          l.isBackwards ? (r.sibling = t.child, t.child = r) : (e = l.last, e !== null ? e.sibling = r : t.child = r, l.last = r);
        }
        return l.tail !== null ? (e = l.tail, l.rendering = e, l.tail = e.sibling, l.renderingStartTime = Ke(), e.sibling = null, a = at.current, Y(
          at,
          n ? a & 1 | 2 : a & 1
        ), ze && wa(t, l.treeForkCount), e) : ($e(t), null);
      case 22:
      case 23:
        return Dt(t), Xo(), l = t.memoizedState !== null, e !== null ? e.memoizedState !== null !== l && (t.flags |= 8192) : l && (t.flags |= 8192), l ? (a & 536870912) !== 0 && (t.flags & 128) === 0 && ($e(t), t.subtreeFlags & 6 && (t.flags |= 8192)) : $e(t), a = t.updateQueue, a !== null && vi(t, a.retryQueue), a = null, e !== null && e.memoizedState !== null && e.memoizedState.cachePool !== null && (a = e.memoizedState.cachePool.pool), l = null, t.memoizedState !== null && t.memoizedState.cachePool !== null && (l = t.memoizedState.cachePool.pool), l !== a && (t.flags |= 2048), e !== null && _(Sl), null;
      case 24:
        return a = null, e !== null && (a = e.memoizedState.cache), t.memoizedState.cache !== a && (t.flags |= 2048), Ea(nt), $e(t), null;
      case 25:
        return null;
      case 30:
        return null;
    }
    throw Error(o(156, t.tag));
  }
  function Ep(e, t) {
    switch (No(t), t.tag) {
      case 1:
        return e = t.flags, e & 65536 ? (t.flags = e & -65537 | 128, t) : null;
      case 3:
        return Ea(nt), me(), e = t.flags, (e & 65536) !== 0 && (e & 128) === 0 ? (t.flags = e & -65537 | 128, t) : null;
      case 26:
      case 27:
      case 5:
        return Et(t), null;
      case 31:
        if (t.memoizedState !== null) {
          if (Dt(t), t.alternate === null)
            throw Error(o(340));
          xl();
        }
        return e = t.flags, e & 65536 ? (t.flags = e & -65537 | 128, t) : null;
      case 13:
        if (Dt(t), e = t.memoizedState, e !== null && e.dehydrated !== null) {
          if (t.alternate === null)
            throw Error(o(340));
          xl();
        }
        return e = t.flags, e & 65536 ? (t.flags = e & -65537 | 128, t) : null;
      case 19:
        return _(at), null;
      case 4:
        return me(), null;
      case 10:
        return Ea(t.type), null;
      case 22:
      case 23:
        return Dt(t), Xo(), e !== null && _(Sl), e = t.flags, e & 65536 ? (t.flags = e & -65537 | 128, t) : null;
      case 24:
        return Ea(nt), null;
      case 25:
        return null;
      default:
        return null;
    }
  }
  function J0(e, t) {
    switch (No(t), t.tag) {
      case 3:
        Ea(nt), me();
        break;
      case 26:
      case 27:
      case 5:
        Et(t);
        break;
      case 4:
        me();
        break;
      case 31:
        t.memoizedState !== null && Dt(t);
        break;
      case 13:
        Dt(t);
        break;
      case 19:
        _(at);
        break;
      case 10:
        Ea(t.type);
        break;
      case 22:
      case 23:
        Dt(t), Xo(), e !== null && _(Sl);
        break;
      case 24:
        Ea(nt);
    }
  }
  function or(e, t) {
    try {
      var a = t.updateQueue, l = a !== null ? a.lastEffect : null;
      if (l !== null) {
        var n = l.next;
        a = n;
        do {
          if ((a.tag & e) === e) {
            l = void 0;
            var r = a.create, c = a.inst;
            l = r(), c.destroy = l;
          }
          a = a.next;
        } while (a !== n);
      }
    } catch (d) {
      Ve(t, t.return, d);
    }
  }
  function Ja(e, t, a) {
    try {
      var l = t.updateQueue, n = l !== null ? l.lastEffect : null;
      if (n !== null) {
        var r = n.next;
        l = r;
        do {
          if ((l.tag & e) === e) {
            var c = l.inst, d = c.destroy;
            if (d !== void 0) {
              c.destroy = void 0, n = t;
              var y = a, M = d;
              try {
                M();
              } catch (N) {
                Ve(
                  n,
                  y,
                  N
                );
              }
            }
          }
          l = l.next;
        } while (l !== r);
      }
    } catch (N) {
      Ve(t, t.return, N);
    }
  }
  function $0(e) {
    var t = e.updateQueue;
    if (t !== null) {
      var a = e.stateNode;
      try {
        Lu(t, a);
      } catch (l) {
        Ve(e, e.return, l);
      }
    }
  }
  function F0(e, t, a) {
    a.props = zl(
      e.type,
      e.memoizedProps
    ), a.state = e.memoizedState;
    try {
      a.componentWillUnmount();
    } catch (l) {
      Ve(e, t, l);
    }
  }
  function cr(e, t) {
    try {
      var a = e.ref;
      if (a !== null) {
        switch (e.tag) {
          case 26:
          case 27:
          case 5:
            var l = e.stateNode;
            break;
          case 30:
            l = e.stateNode;
            break;
          default:
            l = e.stateNode;
        }
        typeof a == "function" ? e.refCleanup = a(l) : a.current = l;
      }
    } catch (n) {
      Ve(e, t, n);
    }
  }
  function fa(e, t) {
    var a = e.ref, l = e.refCleanup;
    if (a !== null)
      if (typeof l == "function")
        try {
          l();
        } catch (n) {
          Ve(e, t, n);
        } finally {
          e.refCleanup = null, e = e.alternate, e != null && (e.refCleanup = null);
        }
      else if (typeof a == "function")
        try {
          a(null);
        } catch (n) {
          Ve(e, t, n);
        }
      else a.current = null;
  }
  function W0(e) {
    var t = e.type, a = e.memoizedProps, l = e.stateNode;
    try {
      e: switch (t) {
        case "button":
        case "input":
        case "select":
        case "textarea":
          a.autoFocus && l.focus();
          break e;
        case "img":
          a.src ? l.src = a.src : a.srcSet && (l.srcset = a.srcSet);
      }
    } catch (n) {
      Ve(e, e.return, n);
    }
  }
  function Ec(e, t, a) {
    try {
      var l = e.stateNode;
      Xp(l, e.type, a, t), l[mt] = t;
    } catch (n) {
      Ve(e, e.return, n);
    }
  }
  function I0(e) {
    return e.tag === 5 || e.tag === 3 || e.tag === 26 || e.tag === 27 && tl(e.type) || e.tag === 4;
  }
  function Ac(e) {
    e: for (; ; ) {
      for (; e.sibling === null; ) {
        if (e.return === null || I0(e.return)) return null;
        e = e.return;
      }
      for (e.sibling.return = e.return, e = e.sibling; e.tag !== 5 && e.tag !== 6 && e.tag !== 18; ) {
        if (e.tag === 27 && tl(e.type) || e.flags & 2 || e.child === null || e.tag === 4) continue e;
        e.child.return = e, e = e.child;
      }
      if (!(e.flags & 2)) return e.stateNode;
    }
  }
  function Mc(e, t, a) {
    var l = e.tag;
    if (l === 5 || l === 6)
      e = e.stateNode, t ? (a.nodeType === 9 ? a.body : a.nodeName === "HTML" ? a.ownerDocument.body : a).insertBefore(e, t) : (t = a.nodeType === 9 ? a.body : a.nodeName === "HTML" ? a.ownerDocument.body : a, t.appendChild(e), a = a._reactRootContainer, a != null || t.onclick !== null || (t.onclick = va));
    else if (l !== 4 && (l === 27 && tl(e.type) && (a = e.stateNode, t = null), e = e.child, e !== null))
      for (Mc(e, t, a), e = e.sibling; e !== null; )
        Mc(e, t, a), e = e.sibling;
  }
  function xi(e, t, a) {
    var l = e.tag;
    if (l === 5 || l === 6)
      e = e.stateNode, t ? a.insertBefore(e, t) : a.appendChild(e);
    else if (l !== 4 && (l === 27 && tl(e.type) && (a = e.stateNode), e = e.child, e !== null))
      for (xi(e, t, a), e = e.sibling; e !== null; )
        xi(e, t, a), e = e.sibling;
  }
  function P0(e) {
    var t = e.stateNode, a = e.memoizedProps;
    try {
      for (var l = e.type, n = t.attributes; n.length; )
        t.removeAttributeNode(n[0]);
      bt(t, l, a), t[Ie] = e, t[mt] = a;
    } catch (r) {
      Ve(e, e.return, r);
    }
  }
  var Ha = !1, ot = !1, kc = !1, e1 = typeof WeakSet == "function" ? WeakSet : Set, dt = null;
  function Ap(e, t) {
    if (e = e.containerInfo, Kc = Ui, e = du(e), xo(e)) {
      if ("selectionStart" in e)
        var a = {
          start: e.selectionStart,
          end: e.selectionEnd
        };
      else
        e: {
          a = (a = e.ownerDocument) && a.defaultView || window;
          var l = a.getSelection && a.getSelection();
          if (l && l.rangeCount !== 0) {
            a = l.anchorNode;
            var n = l.anchorOffset, r = l.focusNode;
            l = l.focusOffset;
            try {
              a.nodeType, r.nodeType;
            } catch {
              a = null;
              break e;
            }
            var c = 0, d = -1, y = -1, M = 0, N = 0, O = e, k = null;
            t: for (; ; ) {
              for (var H; O !== a || n !== 0 && O.nodeType !== 3 || (d = c + n), O !== r || l !== 0 && O.nodeType !== 3 || (y = c + l), O.nodeType === 3 && (c += O.nodeValue.length), (H = O.firstChild) !== null; )
                k = O, O = H;
              for (; ; ) {
                if (O === e) break t;
                if (k === a && ++M === n && (d = c), k === r && ++N === l && (y = c), (H = O.nextSibling) !== null) break;
                O = k, k = O.parentNode;
              }
              O = H;
            }
            a = d === -1 || y === -1 ? null : { start: d, end: y };
          } else a = null;
        }
      a = a || { start: 0, end: 0 };
    } else a = null;
    for (Jc = { focusedElem: e, selectionRange: a }, Ui = !1, dt = t; dt !== null; )
      if (t = dt, e = t.child, (t.subtreeFlags & 1028) !== 0 && e !== null)
        e.return = t, dt = e;
      else
        for (; dt !== null; ) {
          switch (t = dt, r = t.alternate, e = t.flags, t.tag) {
            case 0:
              if ((e & 4) !== 0 && (e = t.updateQueue, e = e !== null ? e.events : null, e !== null))
                for (a = 0; a < e.length; a++)
                  n = e[a], n.ref.impl = n.nextImpl;
              break;
            case 11:
            case 15:
              break;
            case 1:
              if ((e & 1024) !== 0 && r !== null) {
                e = void 0, a = t, n = r.memoizedProps, r = r.memoizedState, l = a.stateNode;
                try {
                  var P = zl(
                    a.type,
                    n
                  );
                  e = l.getSnapshotBeforeUpdate(
                    P,
                    r
                  ), l.__reactInternalSnapshotBeforeUpdate = e;
                } catch (ce) {
                  Ve(
                    a,
                    a.return,
                    ce
                  );
                }
              }
              break;
            case 3:
              if ((e & 1024) !== 0) {
                if (e = t.stateNode.containerInfo, a = e.nodeType, a === 9)
                  Wc(e);
                else if (a === 1)
                  switch (e.nodeName) {
                    case "HEAD":
                    case "HTML":
                    case "BODY":
                      Wc(e);
                      break;
                    default:
                      e.textContent = "";
                  }
              }
              break;
            case 5:
            case 26:
            case 27:
            case 6:
            case 4:
            case 17:
              break;
            default:
              if ((e & 1024) !== 0) throw Error(o(163));
          }
          if (e = t.sibling, e !== null) {
            e.return = t.return, dt = e;
            break;
          }
          dt = t.return;
        }
  }
  function t1(e, t, a) {
    var l = a.flags;
    switch (a.tag) {
      case 0:
      case 11:
      case 15:
        Na(e, a), l & 4 && or(5, a);
        break;
      case 1:
        if (Na(e, a), l & 4)
          if (e = a.stateNode, t === null)
            try {
              e.componentDidMount();
            } catch (c) {
              Ve(a, a.return, c);
            }
          else {
            var n = zl(
              a.type,
              t.memoizedProps
            );
            t = t.memoizedState;
            try {
              e.componentDidUpdate(
                n,
                t,
                e.__reactInternalSnapshotBeforeUpdate
              );
            } catch (c) {
              Ve(
                a,
                a.return,
                c
              );
            }
          }
        l & 64 && $0(a), l & 512 && cr(a, a.return);
        break;
      case 3:
        if (Na(e, a), l & 64 && (e = a.updateQueue, e !== null)) {
          if (t = null, a.child !== null)
            switch (a.child.tag) {
              case 27:
              case 5:
                t = a.child.stateNode;
                break;
              case 1:
                t = a.child.stateNode;
            }
          try {
            Lu(e, t);
          } catch (c) {
            Ve(a, a.return, c);
          }
        }
        break;
      case 27:
        t === null && l & 4 && P0(a);
      case 26:
      case 5:
        Na(e, a), t === null && l & 4 && W0(a), l & 512 && cr(a, a.return);
        break;
      case 12:
        Na(e, a);
        break;
      case 31:
        Na(e, a), l & 4 && n1(e, a);
        break;
      case 13:
        Na(e, a), l & 4 && r1(e, a), l & 64 && (e = a.memoizedState, e !== null && (e = e.dehydrated, e !== null && (a = jp.bind(
          null,
          a
        ), Pp(e, a))));
        break;
      case 22:
        if (l = a.memoizedState !== null || Ha, !l) {
          t = t !== null && t.memoizedState !== null || ot, n = Ha;
          var r = ot;
          Ha = l, (ot = t) && !r ? Ca(
            e,
            a,
            (a.subtreeFlags & 8772) !== 0
          ) : Na(e, a), Ha = n, ot = r;
        }
        break;
      case 30:
        break;
      default:
        Na(e, a);
    }
  }
  function a1(e) {
    var t = e.alternate;
    t !== null && (e.alternate = null, a1(t)), e.child = null, e.deletions = null, e.sibling = null, e.tag === 5 && (t = e.stateNode, t !== null && to(t)), e.stateNode = null, e.return = null, e.dependencies = null, e.memoizedProps = null, e.memoizedState = null, e.pendingProps = null, e.stateNode = null, e.updateQueue = null;
  }
  var We = null, Ht = !1;
  function Ta(e, t, a) {
    for (a = a.child; a !== null; )
      l1(e, t, a), a = a.sibling;
  }
  function l1(e, t, a) {
    if (xt && typeof xt.onCommitFiberUnmount == "function")
      try {
        xt.onCommitFiberUnmount(ha, a);
      } catch {
      }
    switch (a.tag) {
      case 26:
        ot || fa(a, t), Ta(
          e,
          t,
          a
        ), a.memoizedState ? a.memoizedState.count-- : a.stateNode && (a = a.stateNode, a.parentNode.removeChild(a));
        break;
      case 27:
        ot || fa(a, t);
        var l = We, n = Ht;
        tl(a.type) && (We = a.stateNode, Ht = !1), Ta(
          e,
          t,
          a
        ), br(a.stateNode), We = l, Ht = n;
        break;
      case 5:
        ot || fa(a, t);
      case 6:
        if (l = We, n = Ht, We = null, Ta(
          e,
          t,
          a
        ), We = l, Ht = n, We !== null)
          if (Ht)
            try {
              (We.nodeType === 9 ? We.body : We.nodeName === "HTML" ? We.ownerDocument.body : We).removeChild(a.stateNode);
            } catch (r) {
              Ve(
                a,
                t,
                r
              );
            }
          else
            try {
              We.removeChild(a.stateNode);
            } catch (r) {
              Ve(
                a,
                t,
                r
              );
            }
        break;
      case 18:
        We !== null && (Ht ? (e = We, $1(
          e.nodeType === 9 ? e.body : e.nodeName === "HTML" ? e.ownerDocument.body : e,
          a.stateNode
        ), Mn(e)) : $1(We, a.stateNode));
        break;
      case 4:
        l = We, n = Ht, We = a.stateNode.containerInfo, Ht = !0, Ta(
          e,
          t,
          a
        ), We = l, Ht = n;
        break;
      case 0:
      case 11:
      case 14:
      case 15:
        Ja(2, a, t), ot || Ja(4, a, t), Ta(
          e,
          t,
          a
        );
        break;
      case 1:
        ot || (fa(a, t), l = a.stateNode, typeof l.componentWillUnmount == "function" && F0(
          a,
          t,
          l
        )), Ta(
          e,
          t,
          a
        );
        break;
      case 21:
        Ta(
          e,
          t,
          a
        );
        break;
      case 22:
        ot = (l = ot) || a.memoizedState !== null, Ta(
          e,
          t,
          a
        ), ot = l;
        break;
      default:
        Ta(
          e,
          t,
          a
        );
    }
  }
  function n1(e, t) {
    if (t.memoizedState === null && (e = t.alternate, e !== null && (e = e.memoizedState, e !== null))) {
      e = e.dehydrated;
      try {
        Mn(e);
      } catch (a) {
        Ve(t, t.return, a);
      }
    }
  }
  function r1(e, t) {
    if (t.memoizedState === null && (e = t.alternate, e !== null && (e = e.memoizedState, e !== null && (e = e.dehydrated, e !== null))))
      try {
        Mn(e);
      } catch (a) {
        Ve(t, t.return, a);
      }
  }
  function Mp(e) {
    switch (e.tag) {
      case 31:
      case 13:
      case 19:
        var t = e.stateNode;
        return t === null && (t = e.stateNode = new e1()), t;
      case 22:
        return e = e.stateNode, t = e._retryCache, t === null && (t = e._retryCache = new e1()), t;
      default:
        throw Error(o(435, e.tag));
    }
  }
  function yi(e, t) {
    var a = Mp(e);
    t.forEach(function(l) {
      if (!a.has(l)) {
        a.add(l);
        var n = Op.bind(null, e, l);
        l.then(n, n);
      }
    });
  }
  function Tt(e, t) {
    var a = t.deletions;
    if (a !== null)
      for (var l = 0; l < a.length; l++) {
        var n = a[l], r = e, c = t, d = c;
        e: for (; d !== null; ) {
          switch (d.tag) {
            case 27:
              if (tl(d.type)) {
                We = d.stateNode, Ht = !1;
                break e;
              }
              break;
            case 5:
              We = d.stateNode, Ht = !1;
              break e;
            case 3:
            case 4:
              We = d.stateNode.containerInfo, Ht = !0;
              break e;
          }
          d = d.return;
        }
        if (We === null) throw Error(o(160));
        l1(r, c, n), We = null, Ht = !1, r = n.alternate, r !== null && (r.return = null), n.return = null;
      }
    if (t.subtreeFlags & 13886)
      for (t = t.child; t !== null; )
        i1(t, e), t = t.sibling;
  }
  var na = null;
  function i1(e, t) {
    var a = e.alternate, l = e.flags;
    switch (e.tag) {
      case 0:
      case 11:
      case 14:
      case 15:
        Tt(t, e), Nt(e), l & 4 && (Ja(3, e, e.return), or(3, e), Ja(5, e, e.return));
        break;
      case 1:
        Tt(t, e), Nt(e), l & 512 && (ot || a === null || fa(a, a.return)), l & 64 && Ha && (e = e.updateQueue, e !== null && (l = e.callbacks, l !== null && (a = e.shared.hiddenCallbacks, e.shared.hiddenCallbacks = a === null ? l : a.concat(l))));
        break;
      case 26:
        var n = na;
        if (Tt(t, e), Nt(e), l & 512 && (ot || a === null || fa(a, a.return)), l & 4) {
          var r = a !== null ? a.memoizedState : null;
          if (l = e.memoizedState, a === null)
            if (l === null)
              if (e.stateNode === null) {
                e: {
                  l = e.type, a = e.memoizedProps, n = n.ownerDocument || n;
                  t: switch (l) {
                    case "title":
                      r = n.getElementsByTagName("title")[0], (!r || r[Zn] || r[Ie] || r.namespaceURI === "http://www.w3.org/2000/svg" || r.hasAttribute("itemprop")) && (r = n.createElement(l), n.head.insertBefore(
                        r,
                        n.querySelector("head > title")
                      )), bt(r, l, a), r[Ie] = e, ft(r), l = r;
                      break e;
                    case "link":
                      var c = of(
                        "link",
                        "href",
                        n
                      ).get(l + (a.href || ""));
                      if (c) {
                        for (var d = 0; d < c.length; d++)
                          if (r = c[d], r.getAttribute("href") === (a.href == null || a.href === "" ? null : a.href) && r.getAttribute("rel") === (a.rel == null ? null : a.rel) && r.getAttribute("title") === (a.title == null ? null : a.title) && r.getAttribute("crossorigin") === (a.crossOrigin == null ? null : a.crossOrigin)) {
                            c.splice(d, 1);
                            break t;
                          }
                      }
                      r = n.createElement(l), bt(r, l, a), n.head.appendChild(r);
                      break;
                    case "meta":
                      if (c = of(
                        "meta",
                        "content",
                        n
                      ).get(l + (a.content || ""))) {
                        for (d = 0; d < c.length; d++)
                          if (r = c[d], r.getAttribute("content") === (a.content == null ? null : "" + a.content) && r.getAttribute("name") === (a.name == null ? null : a.name) && r.getAttribute("property") === (a.property == null ? null : a.property) && r.getAttribute("http-equiv") === (a.httpEquiv == null ? null : a.httpEquiv) && r.getAttribute("charset") === (a.charSet == null ? null : a.charSet)) {
                            c.splice(d, 1);
                            break t;
                          }
                      }
                      r = n.createElement(l), bt(r, l, a), n.head.appendChild(r);
                      break;
                    default:
                      throw Error(o(468, l));
                  }
                  r[Ie] = e, ft(r), l = r;
                }
                e.stateNode = l;
              } else
                cf(
                  n,
                  e.type,
                  e.stateNode
                );
            else
              e.stateNode = rf(
                n,
                l,
                e.memoizedProps
              );
          else
            r !== l ? (r === null ? a.stateNode !== null && (a = a.stateNode, a.parentNode.removeChild(a)) : r.count--, l === null ? cf(
              n,
              e.type,
              e.stateNode
            ) : rf(
              n,
              l,
              e.memoizedProps
            )) : l === null && e.stateNode !== null && Ec(
              e,
              e.memoizedProps,
              a.memoizedProps
            );
        }
        break;
      case 27:
        Tt(t, e), Nt(e), l & 512 && (ot || a === null || fa(a, a.return)), a !== null && l & 4 && Ec(
          e,
          e.memoizedProps,
          a.memoizedProps
        );
        break;
      case 5:
        if (Tt(t, e), Nt(e), l & 512 && (ot || a === null || fa(a, a.return)), e.flags & 32) {
          n = e.stateNode;
          try {
            Kl(n, "");
          } catch (P) {
            Ve(e, e.return, P);
          }
        }
        l & 4 && e.stateNode != null && (n = e.memoizedProps, Ec(
          e,
          n,
          a !== null ? a.memoizedProps : n
        )), l & 1024 && (kc = !0);
        break;
      case 6:
        if (Tt(t, e), Nt(e), l & 4) {
          if (e.stateNode === null)
            throw Error(o(162));
          l = e.memoizedProps, a = e.stateNode;
          try {
            a.nodeValue = l;
          } catch (P) {
            Ve(e, e.return, P);
          }
        }
        break;
      case 3:
        if (Di = null, n = na, na = Oi(t.containerInfo), Tt(t, e), na = n, Nt(e), l & 4 && a !== null && a.memoizedState.isDehydrated)
          try {
            Mn(t.containerInfo);
          } catch (P) {
            Ve(e, e.return, P);
          }
        kc && (kc = !1, o1(e));
        break;
      case 4:
        l = na, na = Oi(
          e.stateNode.containerInfo
        ), Tt(t, e), Nt(e), na = l;
        break;
      case 12:
        Tt(t, e), Nt(e);
        break;
      case 31:
        Tt(t, e), Nt(e), l & 4 && (l = e.updateQueue, l !== null && (e.updateQueue = null, yi(e, l)));
        break;
      case 13:
        Tt(t, e), Nt(e), e.child.flags & 8192 && e.memoizedState !== null != (a !== null && a.memoizedState !== null) && (Si = Ke()), l & 4 && (l = e.updateQueue, l !== null && (e.updateQueue = null, yi(e, l)));
        break;
      case 22:
        n = e.memoizedState !== null;
        var y = a !== null && a.memoizedState !== null, M = Ha, N = ot;
        if (Ha = M || n, ot = N || y, Tt(t, e), ot = N, Ha = M, Nt(e), l & 8192)
          e: for (t = e.stateNode, t._visibility = n ? t._visibility & -2 : t._visibility | 1, n && (a === null || y || Ha || ot || Hl(e)), a = null, t = e; ; ) {
            if (t.tag === 5 || t.tag === 26) {
              if (a === null) {
                y = a = t;
                try {
                  if (r = y.stateNode, n)
                    c = r.style, typeof c.setProperty == "function" ? c.setProperty("display", "none", "important") : c.display = "none";
                  else {
                    d = y.stateNode;
                    var O = y.memoizedProps.style, k = O != null && O.hasOwnProperty("display") ? O.display : null;
                    d.style.display = k == null || typeof k == "boolean" ? "" : ("" + k).trim();
                  }
                } catch (P) {
                  Ve(y, y.return, P);
                }
              }
            } else if (t.tag === 6) {
              if (a === null) {
                y = t;
                try {
                  y.stateNode.nodeValue = n ? "" : y.memoizedProps;
                } catch (P) {
                  Ve(y, y.return, P);
                }
              }
            } else if (t.tag === 18) {
              if (a === null) {
                y = t;
                try {
                  var H = y.stateNode;
                  n ? F1(H, !0) : F1(y.stateNode, !1);
                } catch (P) {
                  Ve(y, y.return, P);
                }
              }
            } else if ((t.tag !== 22 && t.tag !== 23 || t.memoizedState === null || t === e) && t.child !== null) {
              t.child.return = t, t = t.child;
              continue;
            }
            if (t === e) break e;
            for (; t.sibling === null; ) {
              if (t.return === null || t.return === e) break e;
              a === t && (a = null), t = t.return;
            }
            a === t && (a = null), t.sibling.return = t.return, t = t.sibling;
          }
        l & 4 && (l = e.updateQueue, l !== null && (a = l.retryQueue, a !== null && (l.retryQueue = null, yi(e, a))));
        break;
      case 19:
        Tt(t, e), Nt(e), l & 4 && (l = e.updateQueue, l !== null && (e.updateQueue = null, yi(e, l)));
        break;
      case 30:
        break;
      case 21:
        break;
      default:
        Tt(t, e), Nt(e);
    }
  }
  function Nt(e) {
    var t = e.flags;
    if (t & 2) {
      try {
        for (var a, l = e.return; l !== null; ) {
          if (I0(l)) {
            a = l;
            break;
          }
          l = l.return;
        }
        if (a == null) throw Error(o(160));
        switch (a.tag) {
          case 27:
            var n = a.stateNode, r = Ac(e);
            xi(e, r, n);
            break;
          case 5:
            var c = a.stateNode;
            a.flags & 32 && (Kl(c, ""), a.flags &= -33);
            var d = Ac(e);
            xi(e, d, c);
            break;
          case 3:
          case 4:
            var y = a.stateNode.containerInfo, M = Ac(e);
            Mc(
              e,
              M,
              y
            );
            break;
          default:
            throw Error(o(161));
        }
      } catch (N) {
        Ve(e, e.return, N);
      }
      e.flags &= -3;
    }
    t & 4096 && (e.flags &= -4097);
  }
  function o1(e) {
    if (e.subtreeFlags & 1024)
      for (e = e.child; e !== null; ) {
        var t = e;
        o1(t), t.tag === 5 && t.flags & 1024 && t.stateNode.reset(), e = e.sibling;
      }
  }
  function Na(e, t) {
    if (t.subtreeFlags & 8772)
      for (t = t.child; t !== null; )
        t1(e, t.alternate, t), t = t.sibling;
  }
  function Hl(e) {
    for (e = e.child; e !== null; ) {
      var t = e;
      switch (t.tag) {
        case 0:
        case 11:
        case 14:
        case 15:
          Ja(4, t, t.return), Hl(t);
          break;
        case 1:
          fa(t, t.return);
          var a = t.stateNode;
          typeof a.componentWillUnmount == "function" && F0(
            t,
            t.return,
            a
          ), Hl(t);
          break;
        case 27:
          br(t.stateNode);
        case 26:
        case 5:
          fa(t, t.return), Hl(t);
          break;
        case 22:
          t.memoizedState === null && Hl(t);
          break;
        case 30:
          Hl(t);
          break;
        default:
          Hl(t);
      }
      e = e.sibling;
    }
  }
  function Ca(e, t, a) {
    for (a = a && (t.subtreeFlags & 8772) !== 0, t = t.child; t !== null; ) {
      var l = t.alternate, n = e, r = t, c = r.flags;
      switch (r.tag) {
        case 0:
        case 11:
        case 15:
          Ca(
            n,
            r,
            a
          ), or(4, r);
          break;
        case 1:
          if (Ca(
            n,
            r,
            a
          ), l = r, n = l.stateNode, typeof n.componentDidMount == "function")
            try {
              n.componentDidMount();
            } catch (M) {
              Ve(l, l.return, M);
            }
          if (l = r, n = l.updateQueue, n !== null) {
            var d = l.stateNode;
            try {
              var y = n.shared.hiddenCallbacks;
              if (y !== null)
                for (n.shared.hiddenCallbacks = null, n = 0; n < y.length; n++)
                  Vu(y[n], d);
            } catch (M) {
              Ve(l, l.return, M);
            }
          }
          a && c & 64 && $0(r), cr(r, r.return);
          break;
        case 27:
          P0(r);
        case 26:
        case 5:
          Ca(
            n,
            r,
            a
          ), a && l === null && c & 4 && W0(r), cr(r, r.return);
          break;
        case 12:
          Ca(
            n,
            r,
            a
          );
          break;
        case 31:
          Ca(
            n,
            r,
            a
          ), a && c & 4 && n1(n, r);
          break;
        case 13:
          Ca(
            n,
            r,
            a
          ), a && c & 4 && r1(n, r);
          break;
        case 22:
          r.memoizedState === null && Ca(
            n,
            r,
            a
          ), cr(r, r.return);
          break;
        case 30:
          break;
        default:
          Ca(
            n,
            r,
            a
          );
      }
      t = t.sibling;
    }
  }
  function zc(e, t) {
    var a = null;
    e !== null && e.memoizedState !== null && e.memoizedState.cachePool !== null && (a = e.memoizedState.cachePool.pool), e = null, t.memoizedState !== null && t.memoizedState.cachePool !== null && (e = t.memoizedState.cachePool.pool), e !== a && (e != null && e.refCount++, a != null && Jn(a));
  }
  function Hc(e, t) {
    e = null, t.alternate !== null && (e = t.alternate.memoizedState.cache), t = t.memoizedState.cache, t !== e && (t.refCount++, e != null && Jn(e));
  }
  function ra(e, t, a, l) {
    if (t.subtreeFlags & 10256)
      for (t = t.child; t !== null; )
        c1(
          e,
          t,
          a,
          l
        ), t = t.sibling;
  }
  function c1(e, t, a, l) {
    var n = t.flags;
    switch (t.tag) {
      case 0:
      case 11:
      case 15:
        ra(
          e,
          t,
          a,
          l
        ), n & 2048 && or(9, t);
        break;
      case 1:
        ra(
          e,
          t,
          a,
          l
        );
        break;
      case 3:
        ra(
          e,
          t,
          a,
          l
        ), n & 2048 && (e = null, t.alternate !== null && (e = t.alternate.memoizedState.cache), t = t.memoizedState.cache, t !== e && (t.refCount++, e != null && Jn(e)));
        break;
      case 12:
        if (n & 2048) {
          ra(
            e,
            t,
            a,
            l
          ), e = t.stateNode;
          try {
            var r = t.memoizedProps, c = r.id, d = r.onPostCommit;
            typeof d == "function" && d(
              c,
              t.alternate === null ? "mount" : "update",
              e.passiveEffectDuration,
              -0
            );
          } catch (y) {
            Ve(t, t.return, y);
          }
        } else
          ra(
            e,
            t,
            a,
            l
          );
        break;
      case 31:
        ra(
          e,
          t,
          a,
          l
        );
        break;
      case 13:
        ra(
          e,
          t,
          a,
          l
        );
        break;
      case 23:
        break;
      case 22:
        r = t.stateNode, c = t.alternate, t.memoizedState !== null ? r._visibility & 2 ? ra(
          e,
          t,
          a,
          l
        ) : sr(e, t) : r._visibility & 2 ? ra(
          e,
          t,
          a,
          l
        ) : (r._visibility |= 2, pn(
          e,
          t,
          a,
          l,
          (t.subtreeFlags & 10256) !== 0 || !1
        )), n & 2048 && zc(c, t);
        break;
      case 24:
        ra(
          e,
          t,
          a,
          l
        ), n & 2048 && Hc(t.alternate, t);
        break;
      default:
        ra(
          e,
          t,
          a,
          l
        );
    }
  }
  function pn(e, t, a, l, n) {
    for (n = n && ((t.subtreeFlags & 10256) !== 0 || !1), t = t.child; t !== null; ) {
      var r = e, c = t, d = a, y = l, M = c.flags;
      switch (c.tag) {
        case 0:
        case 11:
        case 15:
          pn(
            r,
            c,
            d,
            y,
            n
          ), or(8, c);
          break;
        case 23:
          break;
        case 22:
          var N = c.stateNode;
          c.memoizedState !== null ? N._visibility & 2 ? pn(
            r,
            c,
            d,
            y,
            n
          ) : sr(
            r,
            c
          ) : (N._visibility |= 2, pn(
            r,
            c,
            d,
            y,
            n
          )), n && M & 2048 && zc(
            c.alternate,
            c
          );
          break;
        case 24:
          pn(
            r,
            c,
            d,
            y,
            n
          ), n && M & 2048 && Hc(c.alternate, c);
          break;
        default:
          pn(
            r,
            c,
            d,
            y,
            n
          );
      }
      t = t.sibling;
    }
  }
  function sr(e, t) {
    if (t.subtreeFlags & 10256)
      for (t = t.child; t !== null; ) {
        var a = e, l = t, n = l.flags;
        switch (l.tag) {
          case 22:
            sr(a, l), n & 2048 && zc(
              l.alternate,
              l
            );
            break;
          case 24:
            sr(a, l), n & 2048 && Hc(l.alternate, l);
            break;
          default:
            sr(a, l);
        }
        t = t.sibling;
      }
  }
  var ur = 8192;
  function hn(e, t, a) {
    if (e.subtreeFlags & ur)
      for (e = e.child; e !== null; )
        s1(
          e,
          t,
          a
        ), e = e.sibling;
  }
  function s1(e, t, a) {
    switch (e.tag) {
      case 26:
        hn(
          e,
          t,
          a
        ), e.flags & ur && e.memoizedState !== null && fh(
          a,
          na,
          e.memoizedState,
          e.memoizedProps
        );
        break;
      case 5:
        hn(
          e,
          t,
          a
        );
        break;
      case 3:
      case 4:
        var l = na;
        na = Oi(e.stateNode.containerInfo), hn(
          e,
          t,
          a
        ), na = l;
        break;
      case 22:
        e.memoizedState === null && (l = e.alternate, l !== null && l.memoizedState !== null ? (l = ur, ur = 16777216, hn(
          e,
          t,
          a
        ), ur = l) : hn(
          e,
          t,
          a
        ));
        break;
      default:
        hn(
          e,
          t,
          a
        );
    }
  }
  function u1(e) {
    var t = e.alternate;
    if (t !== null && (e = t.child, e !== null)) {
      t.child = null;
      do
        t = e.sibling, e.sibling = null, e = t;
      while (e !== null);
    }
  }
  function fr(e) {
    var t = e.deletions;
    if ((e.flags & 16) !== 0) {
      if (t !== null)
        for (var a = 0; a < t.length; a++) {
          var l = t[a];
          dt = l, d1(
            l,
            e
          );
        }
      u1(e);
    }
    if (e.subtreeFlags & 10256)
      for (e = e.child; e !== null; )
        f1(e), e = e.sibling;
  }
  function f1(e) {
    switch (e.tag) {
      case 0:
      case 11:
      case 15:
        fr(e), e.flags & 2048 && Ja(9, e, e.return);
        break;
      case 3:
        fr(e);
        break;
      case 12:
        fr(e);
        break;
      case 22:
        var t = e.stateNode;
        e.memoizedState !== null && t._visibility & 2 && (e.return === null || e.return.tag !== 13) ? (t._visibility &= -3, wi(e)) : fr(e);
        break;
      default:
        fr(e);
    }
  }
  function wi(e) {
    var t = e.deletions;
    if ((e.flags & 16) !== 0) {
      if (t !== null)
        for (var a = 0; a < t.length; a++) {
          var l = t[a];
          dt = l, d1(
            l,
            e
          );
        }
      u1(e);
    }
    for (e = e.child; e !== null; ) {
      switch (t = e, t.tag) {
        case 0:
        case 11:
        case 15:
          Ja(8, t, t.return), wi(t);
          break;
        case 22:
          a = t.stateNode, a._visibility & 2 && (a._visibility &= -3, wi(t));
          break;
        default:
          wi(t);
      }
      e = e.sibling;
    }
  }
  function d1(e, t) {
    for (; dt !== null; ) {
      var a = dt;
      switch (a.tag) {
        case 0:
        case 11:
        case 15:
          Ja(8, a, t);
          break;
        case 23:
        case 22:
          if (a.memoizedState !== null && a.memoizedState.cachePool !== null) {
            var l = a.memoizedState.cachePool.pool;
            l != null && l.refCount++;
          }
          break;
        case 24:
          Jn(a.memoizedState.cache);
      }
      if (l = a.child, l !== null) l.return = a, dt = l;
      else
        e: for (a = e; dt !== null; ) {
          l = dt;
          var n = l.sibling, r = l.return;
          if (a1(l), l === a) {
            dt = null;
            break e;
          }
          if (n !== null) {
            n.return = r, dt = n;
            break e;
          }
          dt = r;
        }
    }
  }
  var kp = {
    getCacheForType: function(e) {
      var t = ht(nt), a = t.data.get(e);
      return a === void 0 && (a = e(), t.data.set(e, a)), a;
    },
    cacheSignal: function() {
      return ht(nt).controller.signal;
    }
  }, zp = typeof WeakMap == "function" ? WeakMap : Map, je = 0, Xe = null, Ee = null, Me = 0, Re = 0, Rt = null, $a = !1, gn = !1, Tc = !1, _a = 0, et = 0, Fa = 0, Tl = 0, Nc = 0, Vt = 0, bn = 0, dr = null, Ct = null, Cc = !1, Si = 0, m1 = 0, Ei = 1 / 0, Ai = null, Wa = null, ct = 0, Ia = null, vn = null, ja = 0, _c = 0, jc = null, p1 = null, mr = 0, Oc = null;
  function Lt() {
    return (je & 2) !== 0 && Me !== 0 ? Me & -Me : z.T !== null ? Uc() : On();
  }
  function h1() {
    if (Vt === 0)
      if ((Me & 536870912) === 0 || ze) {
        var e = re;
        re <<= 1, (re & 3932160) === 0 && (re = 262144), Vt = e;
      } else Vt = 536870912;
    return e = Zt.current, e !== null && (e.flags |= 32), Vt;
  }
  function _t(e, t, a) {
    (e === Xe && (Re === 2 || Re === 9) || e.cancelPendingCommit !== null) && (xn(e, 0), Pa(
      e,
      Me,
      Vt,
      !1
    )), kt(e, a), ((je & 2) === 0 || e !== Xe) && (e === Xe && ((je & 2) === 0 && (Tl |= a), et === 4 && Pa(
      e,
      Me,
      Vt,
      !1
    )), da(e));
  }
  function g1(e, t, a) {
    if ((je & 6) !== 0) throw Error(o(327));
    var l = !a && (t & 127) === 0 && (t & e.expiredLanes) === 0 || Ye(e, t), n = l ? Np(e, t) : Dc(e, t, !0), r = l;
    do {
      if (n === 0) {
        gn && !l && Pa(e, t, 0, !1);
        break;
      } else {
        if (a = e.current.alternate, r && !Hp(a)) {
          n = Dc(e, t, !1), r = !1;
          continue;
        }
        if (n === 2) {
          if (r = t, e.errorRecoveryDisabledLanes & r)
            var c = 0;
          else
            c = e.pendingLanes & -536870913, c = c !== 0 ? c : c & 536870912 ? 536870912 : 0;
          if (c !== 0) {
            t = c;
            e: {
              var d = e;
              n = dr;
              var y = d.current.memoizedState.isDehydrated;
              if (y && (xn(d, c).flags |= 256), c = Dc(
                d,
                c,
                !1
              ), c !== 2) {
                if (Tc && !y) {
                  d.errorRecoveryDisabledLanes |= r, Tl |= r, n = 4;
                  break e;
                }
                r = Ct, Ct = n, r !== null && (Ct === null ? Ct = r : Ct.push.apply(
                  Ct,
                  r
                ));
              }
              n = c;
            }
            if (r = !1, n !== 2) continue;
          }
        }
        if (n === 1) {
          xn(e, 0), Pa(e, t, 0, !0);
          break;
        }
        e: {
          switch (l = e, r = n, r) {
            case 0:
            case 1:
              throw Error(o(345));
            case 4:
              if ((t & 4194048) !== t) break;
            case 6:
              Pa(
                l,
                t,
                Vt,
                !$a
              );
              break e;
            case 2:
              Ct = null;
              break;
            case 3:
            case 5:
              break;
            default:
              throw Error(o(329));
          }
          if ((t & 62914560) === t && (n = Si + 300 - Ke(), 10 < n)) {
            if (Pa(
              l,
              t,
              Vt,
              !$a
            ), ee(l, 0, !0) !== 0) break e;
            ja = t, l.timeoutHandle = K1(
              b1.bind(
                null,
                l,
                a,
                Ct,
                Ai,
                Cc,
                t,
                Vt,
                Tl,
                bn,
                $a,
                r,
                "Throttled",
                -0,
                0
              ),
              n
            );
            break e;
          }
          b1(
            l,
            a,
            Ct,
            Ai,
            Cc,
            t,
            Vt,
            Tl,
            bn,
            $a,
            r,
            null,
            -0,
            0
          );
        }
      }
      break;
    } while (!0);
    da(e);
  }
  function b1(e, t, a, l, n, r, c, d, y, M, N, O, k, H) {
    if (e.timeoutHandle = -1, O = t.subtreeFlags, O & 8192 || (O & 16785408) === 16785408) {
      O = {
        stylesheets: null,
        count: 0,
        imgCount: 0,
        imgBytes: 0,
        suspenseyImages: [],
        waitingForImages: !0,
        waitingForViewTransition: !1,
        unsuspend: va
      }, s1(
        t,
        r,
        O
      );
      var P = (r & 62914560) === r ? Si - Ke() : (r & 4194048) === r ? m1 - Ke() : 0;
      if (P = dh(
        O,
        P
      ), P !== null) {
        ja = r, e.cancelPendingCommit = P(
          M1.bind(
            null,
            e,
            t,
            r,
            a,
            l,
            n,
            c,
            d,
            y,
            N,
            O,
            null,
            k,
            H
          )
        ), Pa(e, r, c, !M);
        return;
      }
    }
    M1(
      e,
      t,
      r,
      a,
      l,
      n,
      c,
      d,
      y
    );
  }
  function Hp(e) {
    for (var t = e; ; ) {
      var a = t.tag;
      if ((a === 0 || a === 11 || a === 15) && t.flags & 16384 && (a = t.updateQueue, a !== null && (a = a.stores, a !== null)))
        for (var l = 0; l < a.length; l++) {
          var n = a[l], r = n.getSnapshot;
          n = n.value;
          try {
            if (!jt(r(), n)) return !1;
          } catch {
            return !1;
          }
        }
      if (a = t.child, t.subtreeFlags & 16384 && a !== null)
        a.return = t, t = a;
      else {
        if (t === e) break;
        for (; t.sibling === null; ) {
          if (t.return === null || t.return === e) return !0;
          t = t.return;
        }
        t.sibling.return = t.return, t = t.sibling;
      }
    }
    return !0;
  }
  function Pa(e, t, a, l) {
    t &= ~Nc, t &= ~Tl, e.suspendedLanes |= t, e.pingedLanes &= ~t, l && (e.warmLanes |= t), l = e.expirationTimes;
    for (var n = t; 0 < n; ) {
      var r = 31 - yt(n), c = 1 << r;
      l[r] = -1, n &= ~c;
    }
    a !== 0 && ca(e, a, t);
  }
  function Mi() {
    return (je & 6) === 0 ? (pr(0), !1) : !0;
  }
  function Zc() {
    if (Ee !== null) {
      if (Re === 0)
        var e = Ee.return;
      else
        e = Ee, Sa = yl = null, Wo(e), sn = null, Fn = 0, e = Ee;
      for (; e !== null; )
        J0(e.alternate, e), e = e.return;
      Ee = null;
    }
  }
  function xn(e, t) {
    var a = e.timeoutHandle;
    a !== -1 && (e.timeoutHandle = -1, Jp(a)), a = e.cancelPendingCommit, a !== null && (e.cancelPendingCommit = null, a()), ja = 0, Zc(), Xe = e, Ee = a = ya(e.current, null), Me = t, Re = 0, Rt = null, $a = !1, gn = Ye(e, t), Tc = !1, bn = Vt = Nc = Tl = Fa = et = 0, Ct = dr = null, Cc = !1, (t & 8) !== 0 && (t |= t & 32);
    var l = e.entangledLanes;
    if (l !== 0)
      for (e = e.entanglements, l &= t; 0 < l; ) {
        var n = 31 - yt(l), r = 1 << n;
        t |= e[n], l &= ~r;
      }
    return _a = t, Qr(), a;
  }
  function v1(e, t) {
    ge = null, z.H = nr, t === cn || t === ei ? (t = Ou(), Re = 3) : t === Lo ? (t = Ou(), Re = 4) : Re = t === mc ? 8 : t !== null && typeof t == "object" && typeof t.then == "function" ? 6 : 1, Rt = t, Ee === null && (et = 1, pi(
      e,
      Qt(t, e.current)
    ));
  }
  function x1() {
    var e = Zt.current;
    return e === null ? !0 : (Me & 4194048) === Me ? Ft === null : (Me & 62914560) === Me || (Me & 536870912) !== 0 ? e === Ft : !1;
  }
  function y1() {
    var e = z.H;
    return z.H = nr, e === null ? nr : e;
  }
  function w1() {
    var e = z.A;
    return z.A = kp, e;
  }
  function ki() {
    et = 4, $a || (Me & 4194048) !== Me && Zt.current !== null || (gn = !0), (Fa & 134217727) === 0 && (Tl & 134217727) === 0 || Xe === null || Pa(
      Xe,
      Me,
      Vt,
      !1
    );
  }
  function Dc(e, t, a) {
    var l = je;
    je |= 2;
    var n = y1(), r = w1();
    (Xe !== e || Me !== t) && (Ai = null, xn(e, t)), t = !1;
    var c = et;
    e: do
      try {
        if (Re !== 0 && Ee !== null) {
          var d = Ee, y = Rt;
          switch (Re) {
            case 8:
              Zc(), c = 6;
              break e;
            case 3:
            case 2:
            case 9:
            case 6:
              Zt.current === null && (t = !0);
              var M = Re;
              if (Re = 0, Rt = null, yn(e, d, y, M), a && gn) {
                c = 0;
                break e;
              }
              break;
            default:
              M = Re, Re = 0, Rt = null, yn(e, d, y, M);
          }
        }
        Tp(), c = et;
        break;
      } catch (N) {
        v1(e, N);
      }
    while (!0);
    return t && e.shellSuspendCounter++, Sa = yl = null, je = l, z.H = n, z.A = r, Ee === null && (Xe = null, Me = 0, Qr()), c;
  }
  function Tp() {
    for (; Ee !== null; ) S1(Ee);
  }
  function Np(e, t) {
    var a = je;
    je |= 2;
    var l = y1(), n = w1();
    Xe !== e || Me !== t ? (Ai = null, Ei = Ke() + 500, xn(e, t)) : gn = Ye(
      e,
      t
    );
    e: do
      try {
        if (Re !== 0 && Ee !== null) {
          t = Ee;
          var r = Rt;
          t: switch (Re) {
            case 1:
              Re = 0, Rt = null, yn(e, t, r, 1);
              break;
            case 2:
            case 9:
              if (_u(r)) {
                Re = 0, Rt = null, E1(t);
                break;
              }
              t = function() {
                Re !== 2 && Re !== 9 || Xe !== e || (Re = 7), da(e);
              }, r.then(t, t);
              break e;
            case 3:
              Re = 7;
              break e;
            case 4:
              Re = 5;
              break e;
            case 7:
              _u(r) ? (Re = 0, Rt = null, E1(t)) : (Re = 0, Rt = null, yn(e, t, r, 7));
              break;
            case 5:
              var c = null;
              switch (Ee.tag) {
                case 26:
                  c = Ee.memoizedState;
                case 5:
                case 27:
                  var d = Ee;
                  if (c ? sf(c) : d.stateNode.complete) {
                    Re = 0, Rt = null;
                    var y = d.sibling;
                    if (y !== null) Ee = y;
                    else {
                      var M = d.return;
                      M !== null ? (Ee = M, zi(M)) : Ee = null;
                    }
                    break t;
                  }
              }
              Re = 0, Rt = null, yn(e, t, r, 5);
              break;
            case 6:
              Re = 0, Rt = null, yn(e, t, r, 6);
              break;
            case 8:
              Zc(), et = 6;
              break e;
            default:
              throw Error(o(462));
          }
        }
        Cp();
        break;
      } catch (N) {
        v1(e, N);
      }
    while (!0);
    return Sa = yl = null, z.H = l, z.A = n, je = a, Ee !== null ? 0 : (Xe = null, Me = 0, Qr(), et);
  }
  function Cp() {
    for (; Ee !== null && !Cr(); )
      S1(Ee);
  }
  function S1(e) {
    var t = Q0(e.alternate, e, _a);
    e.memoizedProps = e.pendingProps, t === null ? zi(e) : Ee = t;
  }
  function E1(e) {
    var t = e, a = t.alternate;
    switch (t.tag) {
      case 15:
      case 0:
        t = U0(
          a,
          t,
          t.pendingProps,
          t.type,
          void 0,
          Me
        );
        break;
      case 11:
        t = U0(
          a,
          t,
          t.pendingProps,
          t.type.render,
          t.ref,
          Me
        );
        break;
      case 5:
        Wo(t);
      default:
        J0(a, t), t = Ee = wu(t, _a), t = Q0(a, t, _a);
    }
    e.memoizedProps = e.pendingProps, t === null ? zi(e) : Ee = t;
  }
  function yn(e, t, a, l) {
    Sa = yl = null, Wo(t), sn = null, Fn = 0;
    var n = t.return;
    try {
      if (xp(
        e,
        n,
        t,
        a,
        Me
      )) {
        et = 1, pi(
          e,
          Qt(a, e.current)
        ), Ee = null;
        return;
      }
    } catch (r) {
      if (n !== null) throw Ee = n, r;
      et = 1, pi(
        e,
        Qt(a, e.current)
      ), Ee = null;
      return;
    }
    t.flags & 32768 ? (ze || l === 1 ? e = !0 : gn || (Me & 536870912) !== 0 ? e = !1 : ($a = e = !0, (l === 2 || l === 9 || l === 3 || l === 6) && (l = Zt.current, l !== null && l.tag === 13 && (l.flags |= 16384))), A1(t, e)) : zi(t);
  }
  function zi(e) {
    var t = e;
    do {
      if ((t.flags & 32768) !== 0) {
        A1(
          t,
          $a
        );
        return;
      }
      e = t.return;
      var a = Sp(
        t.alternate,
        t,
        _a
      );
      if (a !== null) {
        Ee = a;
        return;
      }
      if (t = t.sibling, t !== null) {
        Ee = t;
        return;
      }
      Ee = t = e;
    } while (t !== null);
    et === 0 && (et = 5);
  }
  function A1(e, t) {
    do {
      var a = Ep(e.alternate, e);
      if (a !== null) {
        a.flags &= 32767, Ee = a;
        return;
      }
      if (a = e.return, a !== null && (a.flags |= 32768, a.subtreeFlags = 0, a.deletions = null), !t && (e = e.sibling, e !== null)) {
        Ee = e;
        return;
      }
      Ee = e = a;
    } while (e !== null);
    et = 6, Ee = null;
  }
  function M1(e, t, a, l, n, r, c, d, y) {
    e.cancelPendingCommit = null;
    do
      Hi();
    while (ct !== 0);
    if ((je & 6) !== 0) throw Error(o(327));
    if (t !== null) {
      if (t === e.current) throw Error(o(177));
      if (r = t.lanes | t.childLanes, r |= Ao, wt(
        e,
        a,
        r,
        c,
        d,
        y
      ), e === Xe && (Ee = Xe = null, Me = 0), vn = t, Ia = e, ja = a, _c = r, jc = n, p1 = l, (t.subtreeFlags & 10256) !== 0 || (t.flags & 10256) !== 0 ? (e.callbackNode = null, e.callbackPriority = 0, Zp(ea, function() {
        return N1(), null;
      })) : (e.callbackNode = null, e.callbackPriority = 0), l = (t.flags & 13878) !== 0, (t.subtreeFlags & 13878) !== 0 || l) {
        l = z.T, z.T = null, n = q.p, q.p = 2, c = je, je |= 4;
        try {
          Ap(e, t, a);
        } finally {
          je = c, q.p = n, z.T = l;
        }
      }
      ct = 1, k1(), z1(), H1();
    }
  }
  function k1() {
    if (ct === 1) {
      ct = 0;
      var e = Ia, t = vn, a = (t.flags & 13878) !== 0;
      if ((t.subtreeFlags & 13878) !== 0 || a) {
        a = z.T, z.T = null;
        var l = q.p;
        q.p = 2;
        var n = je;
        je |= 4;
        try {
          i1(t, e);
          var r = Jc, c = du(e.containerInfo), d = r.focusedElem, y = r.selectionRange;
          if (c !== d && d && d.ownerDocument && fu(
            d.ownerDocument.documentElement,
            d
          )) {
            if (y !== null && xo(d)) {
              var M = y.start, N = y.end;
              if (N === void 0 && (N = M), "selectionStart" in d)
                d.selectionStart = M, d.selectionEnd = Math.min(
                  N,
                  d.value.length
                );
              else {
                var O = d.ownerDocument || document, k = O && O.defaultView || window;
                if (k.getSelection) {
                  var H = k.getSelection(), P = d.textContent.length, ce = Math.min(y.start, P), Be = y.end === void 0 ? ce : Math.min(y.end, P);
                  !H.extend && ce > Be && (c = Be, Be = ce, ce = c);
                  var E = uu(
                    d,
                    ce
                  ), w = uu(
                    d,
                    Be
                  );
                  if (E && w && (H.rangeCount !== 1 || H.anchorNode !== E.node || H.anchorOffset !== E.offset || H.focusNode !== w.node || H.focusOffset !== w.offset)) {
                    var A = O.createRange();
                    A.setStart(E.node, E.offset), H.removeAllRanges(), ce > Be ? (H.addRange(A), H.extend(w.node, w.offset)) : (A.setEnd(w.node, w.offset), H.addRange(A));
                  }
                }
              }
            }
            for (O = [], H = d; H = H.parentNode; )
              H.nodeType === 1 && O.push({
                element: H,
                left: H.scrollLeft,
                top: H.scrollTop
              });
            for (typeof d.focus == "function" && d.focus(), d = 0; d < O.length; d++) {
              var j = O[d];
              j.element.scrollLeft = j.left, j.element.scrollTop = j.top;
            }
          }
          Ui = !!Kc, Jc = Kc = null;
        } finally {
          je = n, q.p = l, z.T = a;
        }
      }
      e.current = t, ct = 2;
    }
  }
  function z1() {
    if (ct === 2) {
      ct = 0;
      var e = Ia, t = vn, a = (t.flags & 8772) !== 0;
      if ((t.subtreeFlags & 8772) !== 0 || a) {
        a = z.T, z.T = null;
        var l = q.p;
        q.p = 2;
        var n = je;
        je |= 4;
        try {
          t1(e, t.alternate, t);
        } finally {
          je = n, q.p = l, z.T = a;
        }
      }
      ct = 3;
    }
  }
  function H1() {
    if (ct === 4 || ct === 3) {
      ct = 0, Ll();
      var e = Ia, t = vn, a = ja, l = p1;
      (t.subtreeFlags & 10256) !== 0 || (t.flags & 10256) !== 0 ? ct = 5 : (ct = 0, vn = Ia = null, T1(e, e.pendingLanes));
      var n = e.pendingLanes;
      if (n === 0 && (Wa = null), fl(a), t = t.stateNode, xt && typeof xt.onCommitFiberRoot == "function")
        try {
          xt.onCommitFiberRoot(
            ha,
            t,
            void 0,
            (t.current.flags & 128) === 128
          );
        } catch {
        }
      if (l !== null) {
        t = z.T, n = q.p, q.p = 2, z.T = null;
        try {
          for (var r = e.onRecoverableError, c = 0; c < l.length; c++) {
            var d = l[c];
            r(d.value, {
              componentStack: d.stack
            });
          }
        } finally {
          z.T = t, q.p = n;
        }
      }
      (ja & 3) !== 0 && Hi(), da(e), n = e.pendingLanes, (a & 261930) !== 0 && (n & 42) !== 0 ? e === Oc ? mr++ : (mr = 0, Oc = e) : mr = 0, pr(0);
    }
  }
  function T1(e, t) {
    (e.pooledCacheLanes &= t) === 0 && (t = e.pooledCache, t != null && (e.pooledCache = null, Jn(t)));
  }
  function Hi() {
    return k1(), z1(), H1(), N1();
  }
  function N1() {
    if (ct !== 5) return !1;
    var e = Ia, t = _c;
    _c = 0;
    var a = fl(ja), l = z.T, n = q.p;
    try {
      q.p = 32 > a ? 32 : a, z.T = null, a = jc, jc = null;
      var r = Ia, c = ja;
      if (ct = 0, vn = Ia = null, ja = 0, (je & 6) !== 0) throw Error(o(331));
      var d = je;
      if (je |= 4, f1(r.current), c1(
        r,
        r.current,
        c,
        a
      ), je = d, pr(0, !1), xt && typeof xt.onPostCommitFiberRoot == "function")
        try {
          xt.onPostCommitFiberRoot(ha, r);
        } catch {
        }
      return !0;
    } finally {
      q.p = n, z.T = l, T1(e, t);
    }
  }
  function C1(e, t, a) {
    t = Qt(a, t), t = dc(e.stateNode, t, 2), e = Xa(e, t, 2), e !== null && (kt(e, 2), da(e));
  }
  function Ve(e, t, a) {
    if (e.tag === 3)
      C1(e, e, a);
    else
      for (; t !== null; ) {
        if (t.tag === 3) {
          C1(
            t,
            e,
            a
          );
          break;
        } else if (t.tag === 1) {
          var l = t.stateNode;
          if (typeof t.type.getDerivedStateFromError == "function" || typeof l.componentDidCatch == "function" && (Wa === null || !Wa.has(l))) {
            e = Qt(a, e), a = _0(2), l = Xa(t, a, 2), l !== null && (j0(
              a,
              l,
              t,
              e
            ), kt(l, 2), da(l));
            break;
          }
        }
        t = t.return;
      }
  }
  function Rc(e, t, a) {
    var l = e.pingCache;
    if (l === null) {
      l = e.pingCache = new zp();
      var n = /* @__PURE__ */ new Set();
      l.set(t, n);
    } else
      n = l.get(t), n === void 0 && (n = /* @__PURE__ */ new Set(), l.set(t, n));
    n.has(a) || (Tc = !0, n.add(a), e = _p.bind(null, e, t, a), t.then(e, e));
  }
  function _p(e, t, a) {
    var l = e.pingCache;
    l !== null && l.delete(t), e.pingedLanes |= e.suspendedLanes & a, e.warmLanes &= ~a, Xe === e && (Me & a) === a && (et === 4 || et === 3 && (Me & 62914560) === Me && 300 > Ke() - Si ? (je & 2) === 0 && xn(e, 0) : Nc |= a, bn === Me && (bn = 0)), da(e);
  }
  function _1(e, t) {
    t === 0 && (t = Bt()), e = bl(e, t), e !== null && (kt(e, t), da(e));
  }
  function jp(e) {
    var t = e.memoizedState, a = 0;
    t !== null && (a = t.retryLane), _1(e, a);
  }
  function Op(e, t) {
    var a = 0;
    switch (e.tag) {
      case 31:
      case 13:
        var l = e.stateNode, n = e.memoizedState;
        n !== null && (a = n.retryLane);
        break;
      case 19:
        l = e.stateNode;
        break;
      case 22:
        l = e.stateNode._retryCache;
        break;
      default:
        throw Error(o(314));
    }
    l !== null && l.delete(t), _1(e, a);
  }
  function Zp(e, t) {
    return Rl(e, t);
  }
  var Ti = null, wn = null, Vc = !1, Ni = !1, Lc = !1, el = 0;
  function da(e) {
    e !== wn && e.next === null && (wn === null ? Ti = wn = e : wn = wn.next = e), Ni = !0, Vc || (Vc = !0, Rp());
  }
  function pr(e, t) {
    if (!Lc && Ni) {
      Lc = !0;
      do
        for (var a = !1, l = Ti; l !== null; ) {
          if (e !== 0) {
            var n = l.pendingLanes;
            if (n === 0) var r = 0;
            else {
              var c = l.suspendedLanes, d = l.pingedLanes;
              r = (1 << 31 - yt(42 | e) + 1) - 1, r &= n & ~(c & ~d), r = r & 201326741 ? r & 201326741 | 1 : r ? r | 2 : 0;
            }
            r !== 0 && (a = !0, D1(l, r));
          } else
            r = Me, r = ee(
              l,
              l === Xe ? r : 0,
              l.cancelPendingCommit !== null || l.timeoutHandle !== -1
            ), (r & 3) === 0 || Ye(l, r) || (a = !0, D1(l, r));
          l = l.next;
        }
      while (a);
      Lc = !1;
    }
  }
  function Dp() {
    j1();
  }
  function j1() {
    Ni = Vc = !1;
    var e = 0;
    el !== 0 && Kp() && (e = el);
    for (var t = Ke(), a = null, l = Ti; l !== null; ) {
      var n = l.next, r = O1(l, t);
      r === 0 ? (l.next = null, a === null ? Ti = n : a.next = n, n === null && (wn = a)) : (a = l, (e !== 0 || (r & 3) !== 0) && (Ni = !0)), l = n;
    }
    ct !== 0 && ct !== 5 || pr(e), el !== 0 && (el = 0);
  }
  function O1(e, t) {
    for (var a = e.suspendedLanes, l = e.pingedLanes, n = e.expirationTimes, r = e.pendingLanes & -62914561; 0 < r; ) {
      var c = 31 - yt(r), d = 1 << c, y = n[c];
      y === -1 ? ((d & a) === 0 || (d & l) !== 0) && (n[c] = De(d, t)) : y <= t && (e.expiredLanes |= d), r &= ~d;
    }
    if (t = Xe, a = Me, a = ee(
      e,
      e === t ? a : 0,
      e.cancelPendingCommit !== null || e.timeoutHandle !== -1
    ), l = e.callbackNode, a === 0 || e === t && (Re === 2 || Re === 9) || e.cancelPendingCommit !== null)
      return l !== null && l !== null && Vl(l), e.callbackNode = null, e.callbackPriority = 0;
    if ((a & 3) === 0 || Ye(e, a)) {
      if (t = a & -a, t === e.callbackPriority) return t;
      switch (l !== null && Vl(l), fl(a)) {
        case 2:
        case 8:
          a = Ul;
          break;
        case 32:
          a = ea;
          break;
        case 268435456:
          a = Cn;
          break;
        default:
          a = ea;
      }
      return l = Z1.bind(null, e), a = Rl(a, l), e.callbackPriority = t, e.callbackNode = a, t;
    }
    return l !== null && l !== null && Vl(l), e.callbackPriority = 2, e.callbackNode = null, 2;
  }
  function Z1(e, t) {
    if (ct !== 0 && ct !== 5)
      return e.callbackNode = null, e.callbackPriority = 0, null;
    var a = e.callbackNode;
    if (Hi() && e.callbackNode !== a)
      return null;
    var l = Me;
    return l = ee(
      e,
      e === Xe ? l : 0,
      e.cancelPendingCommit !== null || e.timeoutHandle !== -1
    ), l === 0 ? null : (g1(e, l, t), O1(e, Ke()), e.callbackNode != null && e.callbackNode === a ? Z1.bind(null, e) : null);
  }
  function D1(e, t) {
    if (Hi()) return null;
    g1(e, t, !0);
  }
  function Rp() {
    $p(function() {
      (je & 6) !== 0 ? Rl(
        _r,
        Dp
      ) : j1();
    });
  }
  function Uc() {
    if (el === 0) {
      var e = rn;
      e === 0 && (e = X, X <<= 1, (X & 261888) === 0 && (X = 256)), el = e;
    }
    return el;
  }
  function R1(e) {
    return e == null || typeof e == "symbol" || typeof e == "boolean" ? null : typeof e == "function" ? e : Vr("" + e);
  }
  function V1(e, t) {
    var a = t.ownerDocument.createElement("input");
    return a.name = t.name, a.value = t.value, e.id && a.setAttribute("form", e.id), t.parentNode.insertBefore(a, t), e = new FormData(e), a.parentNode.removeChild(a), e;
  }
  function Vp(e, t, a, l, n) {
    if (t === "submit" && a && a.stateNode === n) {
      var r = R1(
        (n[mt] || null).action
      ), c = l.submitter;
      c && (t = (t = c[mt] || null) ? R1(t.formAction) : c.getAttribute("formAction"), t !== null && (r = t, c = null));
      var d = new Br(
        "action",
        "action",
        null,
        l,
        n
      );
      e.push({
        event: d,
        listeners: [
          {
            instance: null,
            listener: function() {
              if (l.defaultPrevented) {
                if (el !== 0) {
                  var y = c ? V1(n, c) : new FormData(n);
                  ic(
                    a,
                    {
                      pending: !0,
                      data: y,
                      method: n.method,
                      action: r
                    },
                    null,
                    y
                  );
                }
              } else
                typeof r == "function" && (d.preventDefault(), y = c ? V1(n, c) : new FormData(n), ic(
                  a,
                  {
                    pending: !0,
                    data: y,
                    method: n.method,
                    action: r
                  },
                  r,
                  y
                ));
            },
            currentTarget: n
          }
        ]
      });
    }
  }
  for (var qc = 0; qc < Eo.length; qc++) {
    var Bc = Eo[qc], Lp = Bc.toLowerCase(), Up = Bc[0].toUpperCase() + Bc.slice(1);
    la(
      Lp,
      "on" + Up
    );
  }
  la(hu, "onAnimationEnd"), la(gu, "onAnimationIteration"), la(bu, "onAnimationStart"), la("dblclick", "onDoubleClick"), la("focusin", "onFocus"), la("focusout", "onBlur"), la(ap, "onTransitionRun"), la(lp, "onTransitionStart"), la(np, "onTransitionCancel"), la(vu, "onTransitionEnd"), Xl("onMouseEnter", ["mouseout", "mouseover"]), Xl("onMouseLeave", ["mouseout", "mouseover"]), Xl("onPointerEnter", ["pointerout", "pointerover"]), Xl("onPointerLeave", ["pointerout", "pointerover"]), ml(
    "onChange",
    "change click focusin focusout input keydown keyup selectionchange".split(" ")
  ), ml(
    "onSelect",
    "focusout contextmenu dragend focusin keydown keyup mousedown mouseup selectionchange".split(
      " "
    )
  ), ml("onBeforeInput", [
    "compositionend",
    "keypress",
    "textInput",
    "paste"
  ]), ml(
    "onCompositionEnd",
    "compositionend focusout keydown keypress keyup mousedown".split(" ")
  ), ml(
    "onCompositionStart",
    "compositionstart focusout keydown keypress keyup mousedown".split(" ")
  ), ml(
    "onCompositionUpdate",
    "compositionupdate focusout keydown keypress keyup mousedown".split(" ")
  );
  var hr = "abort canplay canplaythrough durationchange emptied encrypted ended error loadeddata loadedmetadata loadstart pause play playing progress ratechange resize seeked seeking stalled suspend timeupdate volumechange waiting".split(
    " "
  ), qp = new Set(
    "beforetoggle cancel close invalid load scroll scrollend toggle".split(" ").concat(hr)
  );
  function L1(e, t) {
    t = (t & 4) !== 0;
    for (var a = 0; a < e.length; a++) {
      var l = e[a], n = l.event;
      l = l.listeners;
      e: {
        var r = void 0;
        if (t)
          for (var c = l.length - 1; 0 <= c; c--) {
            var d = l[c], y = d.instance, M = d.currentTarget;
            if (d = d.listener, y !== r && n.isPropagationStopped())
              break e;
            r = d, n.currentTarget = M;
            try {
              r(n);
            } catch (N) {
              Xr(N);
            }
            n.currentTarget = null, r = y;
          }
        else
          for (c = 0; c < l.length; c++) {
            if (d = l[c], y = d.instance, M = d.currentTarget, d = d.listener, y !== r && n.isPropagationStopped())
              break e;
            r = d, n.currentTarget = M;
            try {
              r(n);
            } catch (N) {
              Xr(N);
            }
            n.currentTarget = null, r = y;
          }
      }
    }
  }
  function Ae(e, t) {
    var a = t[At];
    a === void 0 && (a = t[At] = /* @__PURE__ */ new Set());
    var l = e + "__bubble";
    a.has(l) || (U1(t, e, 2, !1), a.add(l));
  }
  function Gc(e, t, a) {
    var l = 0;
    t && (l |= 4), U1(
      a,
      e,
      l,
      t
    );
  }
  var Ci = "_reactListening" + Math.random().toString(36).slice(2);
  function Yc(e) {
    if (!e[Ci]) {
      e[Ci] = !0, js.forEach(function(a) {
        a !== "selectionchange" && (qp.has(a) || Gc(a, !1, e), Gc(a, !0, e));
      });
      var t = e.nodeType === 9 ? e : e.ownerDocument;
      t === null || t[Ci] || (t[Ci] = !0, Gc("selectionchange", !1, t));
    }
  }
  function U1(e, t, a, l) {
    switch (gf(t)) {
      case 2:
        var n = hh;
        break;
      case 8:
        n = gh;
        break;
      default:
        n = rs;
    }
    a = n.bind(
      null,
      t,
      a,
      e
    ), n = void 0, !so || t !== "touchstart" && t !== "touchmove" && t !== "wheel" || (n = !0), l ? n !== void 0 ? e.addEventListener(t, a, {
      capture: !0,
      passive: n
    }) : e.addEventListener(t, a, !0) : n !== void 0 ? e.addEventListener(t, a, {
      passive: n
    }) : e.addEventListener(t, a, !1);
  }
  function Xc(e, t, a, l, n) {
    var r = l;
    if ((t & 1) === 0 && (t & 2) === 0 && l !== null)
      e: for (; ; ) {
        if (l === null) return;
        var c = l.tag;
        if (c === 3 || c === 4) {
          var d = l.stateNode.containerInfo;
          if (d === n) break;
          if (c === 4)
            for (c = l.return; c !== null; ) {
              var y = c.tag;
              if ((y === 3 || y === 4) && c.stateNode.containerInfo === n)
                return;
              c = c.return;
            }
          for (; d !== null; ) {
            if (c = Bl(d), c === null) return;
            if (y = c.tag, y === 5 || y === 6 || y === 26 || y === 27) {
              l = r = c;
              continue e;
            }
            d = d.parentNode;
          }
        }
        l = l.return;
      }
    Xs(function() {
      var M = r, N = oo(a), O = [];
      e: {
        var k = xu.get(e);
        if (k !== void 0) {
          var H = Br, P = e;
          switch (e) {
            case "keypress":
              if (Ur(a) === 0) break e;
            case "keydown":
            case "keyup":
              H = Om;
              break;
            case "focusin":
              P = "focus", H = po;
              break;
            case "focusout":
              P = "blur", H = po;
              break;
            case "beforeblur":
            case "afterblur":
              H = po;
              break;
            case "click":
              if (a.button === 2) break e;
            case "auxclick":
            case "dblclick":
            case "mousedown":
            case "mousemove":
            case "mouseup":
            case "mouseout":
            case "mouseover":
            case "contextmenu":
              H = Js;
              break;
            case "drag":
            case "dragend":
            case "dragenter":
            case "dragexit":
            case "dragleave":
            case "dragover":
            case "dragstart":
            case "drop":
              H = Sm;
              break;
            case "touchcancel":
            case "touchend":
            case "touchmove":
            case "touchstart":
              H = Rm;
              break;
            case hu:
            case gu:
            case bu:
              H = Mm;
              break;
            case vu:
              H = Lm;
              break;
            case "scroll":
            case "scrollend":
              H = ym;
              break;
            case "wheel":
              H = qm;
              break;
            case "copy":
            case "cut":
            case "paste":
              H = zm;
              break;
            case "gotpointercapture":
            case "lostpointercapture":
            case "pointercancel":
            case "pointerdown":
            case "pointermove":
            case "pointerout":
            case "pointerover":
            case "pointerup":
              H = Fs;
              break;
            case "toggle":
            case "beforetoggle":
              H = Gm;
          }
          var ce = (t & 4) !== 0, Be = !ce && (e === "scroll" || e === "scrollend"), E = ce ? k !== null ? k + "Capture" : null : k;
          ce = [];
          for (var w = M, A; w !== null; ) {
            var j = w;
            if (A = j.stateNode, j = j.tag, j !== 5 && j !== 26 && j !== 27 || A === null || E === null || (j = Rn(w, E), j != null && ce.push(
              gr(w, j, A)
            )), Be) break;
            w = w.return;
          }
          0 < ce.length && (k = new H(
            k,
            P,
            null,
            a,
            N
          ), O.push({ event: k, listeners: ce }));
        }
      }
      if ((t & 7) === 0) {
        e: {
          if (k = e === "mouseover" || e === "pointerover", H = e === "mouseout" || e === "pointerout", k && a !== io && (P = a.relatedTarget || a.fromElement) && (Bl(P) || P[Te]))
            break e;
          if ((H || k) && (k = N.window === N ? N : (k = N.ownerDocument) ? k.defaultView || k.parentWindow : window, H ? (P = a.relatedTarget || a.toElement, H = M, P = P ? Bl(P) : null, P !== null && (Be = p(P), ce = P.tag, P !== Be || ce !== 5 && ce !== 27 && ce !== 6) && (P = null)) : (H = null, P = M), H !== P)) {
            if (ce = Js, j = "onMouseLeave", E = "onMouseEnter", w = "mouse", (e === "pointerout" || e === "pointerover") && (ce = Fs, j = "onPointerLeave", E = "onPointerEnter", w = "pointer"), Be = H == null ? k : Dn(H), A = P == null ? k : Dn(P), k = new ce(
              j,
              w + "leave",
              H,
              a,
              N
            ), k.target = Be, k.relatedTarget = A, j = null, Bl(N) === M && (ce = new ce(
              E,
              w + "enter",
              P,
              a,
              N
            ), ce.target = A, ce.relatedTarget = Be, j = ce), Be = j, H && P)
              t: {
                for (ce = Bp, E = H, w = P, A = 0, j = E; j; j = ce(j))
                  A++;
                j = 0;
                for (var oe = w; oe; oe = ce(oe))
                  j++;
                for (; 0 < A - j; )
                  E = ce(E), A--;
                for (; 0 < j - A; )
                  w = ce(w), j--;
                for (; A--; ) {
                  if (E === w || w !== null && E === w.alternate) {
                    ce = E;
                    break t;
                  }
                  E = ce(E), w = ce(w);
                }
                ce = null;
              }
            else ce = null;
            H !== null && q1(
              O,
              k,
              H,
              ce,
              !1
            ), P !== null && Be !== null && q1(
              O,
              Be,
              P,
              ce,
              !0
            );
          }
        }
        e: {
          if (k = M ? Dn(M) : window, H = k.nodeName && k.nodeName.toLowerCase(), H === "select" || H === "input" && k.type === "file")
            var Ce = nu;
          else if (au(k))
            if (ru)
              Ce = Pm;
            else {
              Ce = Wm;
              var ae = Fm;
            }
          else
            H = k.nodeName, !H || H.toLowerCase() !== "input" || k.type !== "checkbox" && k.type !== "radio" ? M && ro(M.elementType) && (Ce = nu) : Ce = Im;
          if (Ce && (Ce = Ce(e, M))) {
            lu(
              O,
              Ce,
              a,
              N
            );
            break e;
          }
          ae && ae(e, k, M), e === "focusout" && M && k.type === "number" && M.memoizedProps.value != null && no(k, "number", k.value);
        }
        switch (ae = M ? Dn(M) : window, e) {
          case "focusin":
            (au(ae) || ae.contentEditable === "true") && (Wl = ae, yo = M, Xn = null);
            break;
          case "focusout":
            Xn = yo = Wl = null;
            break;
          case "mousedown":
            wo = !0;
            break;
          case "contextmenu":
          case "mouseup":
          case "dragend":
            wo = !1, mu(O, a, N);
            break;
          case "selectionchange":
            if (tp) break;
          case "keydown":
          case "keyup":
            mu(O, a, N);
        }
        var ve;
        if (go)
          e: {
            switch (e) {
              case "compositionstart":
                var ke = "onCompositionStart";
                break e;
              case "compositionend":
                ke = "onCompositionEnd";
                break e;
              case "compositionupdate":
                ke = "onCompositionUpdate";
                break e;
            }
            ke = void 0;
          }
        else
          Fl ? eu(e, a) && (ke = "onCompositionEnd") : e === "keydown" && a.keyCode === 229 && (ke = "onCompositionStart");
        ke && (Ws && a.locale !== "ko" && (Fl || ke !== "onCompositionStart" ? ke === "onCompositionEnd" && Fl && (ve = Qs()) : (Va = N, uo = "value" in Va ? Va.value : Va.textContent, Fl = !0)), ae = _i(M, ke), 0 < ae.length && (ke = new $s(
          ke,
          e,
          null,
          a,
          N
        ), O.push({ event: ke, listeners: ae }), ve ? ke.data = ve : (ve = tu(a), ve !== null && (ke.data = ve)))), (ve = Xm ? Qm(e, a) : Km(e, a)) && (ke = _i(M, "onBeforeInput"), 0 < ke.length && (ae = new $s(
          "onBeforeInput",
          "beforeinput",
          null,
          a,
          N
        ), O.push({
          event: ae,
          listeners: ke
        }), ae.data = ve)), Vp(
          O,
          e,
          M,
          a,
          N
        );
      }
      L1(O, t);
    });
  }
  function gr(e, t, a) {
    return {
      instance: e,
      listener: t,
      currentTarget: a
    };
  }
  function _i(e, t) {
    for (var a = t + "Capture", l = []; e !== null; ) {
      var n = e, r = n.stateNode;
      if (n = n.tag, n !== 5 && n !== 26 && n !== 27 || r === null || (n = Rn(e, a), n != null && l.unshift(
        gr(e, n, r)
      ), n = Rn(e, t), n != null && l.push(
        gr(e, n, r)
      )), e.tag === 3) return l;
      e = e.return;
    }
    return [];
  }
  function Bp(e) {
    if (e === null) return null;
    do
      e = e.return;
    while (e && e.tag !== 5 && e.tag !== 27);
    return e || null;
  }
  function q1(e, t, a, l, n) {
    for (var r = t._reactName, c = []; a !== null && a !== l; ) {
      var d = a, y = d.alternate, M = d.stateNode;
      if (d = d.tag, y !== null && y === l) break;
      d !== 5 && d !== 26 && d !== 27 || M === null || (y = M, n ? (M = Rn(a, r), M != null && c.unshift(
        gr(a, M, y)
      )) : n || (M = Rn(a, r), M != null && c.push(
        gr(a, M, y)
      ))), a = a.return;
    }
    c.length !== 0 && e.push({ event: t, listeners: c });
  }
  var Gp = /\r\n?/g, Yp = /\u0000|\uFFFD/g;
  function B1(e) {
    return (typeof e == "string" ? e : "" + e).replace(Gp, `
`).replace(Yp, "");
  }
  function G1(e, t) {
    return t = B1(t), B1(e) === t;
  }
  function qe(e, t, a, l, n, r) {
    switch (a) {
      case "children":
        typeof l == "string" ? t === "body" || t === "textarea" && l === "" || Kl(e, l) : (typeof l == "number" || typeof l == "bigint") && t !== "body" && Kl(e, "" + l);
        break;
      case "className":
        Dr(e, "class", l);
        break;
      case "tabIndex":
        Dr(e, "tabindex", l);
        break;
      case "dir":
      case "role":
      case "viewBox":
      case "width":
      case "height":
        Dr(e, a, l);
        break;
      case "style":
        Gs(e, l, r);
        break;
      case "data":
        if (t !== "object") {
          Dr(e, "data", l);
          break;
        }
      case "src":
      case "href":
        if (l === "" && (t !== "a" || a !== "href")) {
          e.removeAttribute(a);
          break;
        }
        if (l == null || typeof l == "function" || typeof l == "symbol" || typeof l == "boolean") {
          e.removeAttribute(a);
          break;
        }
        l = Vr("" + l), e.setAttribute(a, l);
        break;
      case "action":
      case "formAction":
        if (typeof l == "function") {
          e.setAttribute(
            a,
            "javascript:throw new Error('A React form was unexpectedly submitted. If you called form.submit() manually, consider using form.requestSubmit() instead. If you\\'re trying to use event.stopPropagation() in a submit event handler, consider also calling event.preventDefault().')"
          );
          break;
        } else
          typeof r == "function" && (a === "formAction" ? (t !== "input" && qe(e, t, "name", n.name, n, null), qe(
            e,
            t,
            "formEncType",
            n.formEncType,
            n,
            null
          ), qe(
            e,
            t,
            "formMethod",
            n.formMethod,
            n,
            null
          ), qe(
            e,
            t,
            "formTarget",
            n.formTarget,
            n,
            null
          )) : (qe(e, t, "encType", n.encType, n, null), qe(e, t, "method", n.method, n, null), qe(e, t, "target", n.target, n, null)));
        if (l == null || typeof l == "symbol" || typeof l == "boolean") {
          e.removeAttribute(a);
          break;
        }
        l = Vr("" + l), e.setAttribute(a, l);
        break;
      case "onClick":
        l != null && (e.onclick = va);
        break;
      case "onScroll":
        l != null && Ae("scroll", e);
        break;
      case "onScrollEnd":
        l != null && Ae("scrollend", e);
        break;
      case "dangerouslySetInnerHTML":
        if (l != null) {
          if (typeof l != "object" || !("__html" in l))
            throw Error(o(61));
          if (a = l.__html, a != null) {
            if (n.children != null) throw Error(o(60));
            e.innerHTML = a;
          }
        }
        break;
      case "multiple":
        e.multiple = l && typeof l != "function" && typeof l != "symbol";
        break;
      case "muted":
        e.muted = l && typeof l != "function" && typeof l != "symbol";
        break;
      case "suppressContentEditableWarning":
      case "suppressHydrationWarning":
      case "defaultValue":
      case "defaultChecked":
      case "innerHTML":
      case "ref":
        break;
      case "autoFocus":
        break;
      case "xlinkHref":
        if (l == null || typeof l == "function" || typeof l == "boolean" || typeof l == "symbol") {
          e.removeAttribute("xlink:href");
          break;
        }
        a = Vr("" + l), e.setAttributeNS(
          "http://www.w3.org/1999/xlink",
          "xlink:href",
          a
        );
        break;
      case "contentEditable":
      case "spellCheck":
      case "draggable":
      case "value":
      case "autoReverse":
      case "externalResourcesRequired":
      case "focusable":
      case "preserveAlpha":
        l != null && typeof l != "function" && typeof l != "symbol" ? e.setAttribute(a, "" + l) : e.removeAttribute(a);
        break;
      case "inert":
      case "allowFullScreen":
      case "async":
      case "autoPlay":
      case "controls":
      case "default":
      case "defer":
      case "disabled":
      case "disablePictureInPicture":
      case "disableRemotePlayback":
      case "formNoValidate":
      case "hidden":
      case "loop":
      case "noModule":
      case "noValidate":
      case "open":
      case "playsInline":
      case "readOnly":
      case "required":
      case "reversed":
      case "scoped":
      case "seamless":
      case "itemScope":
        l && typeof l != "function" && typeof l != "symbol" ? e.setAttribute(a, "") : e.removeAttribute(a);
        break;
      case "capture":
      case "download":
        l === !0 ? e.setAttribute(a, "") : l !== !1 && l != null && typeof l != "function" && typeof l != "symbol" ? e.setAttribute(a, l) : e.removeAttribute(a);
        break;
      case "cols":
      case "rows":
      case "size":
      case "span":
        l != null && typeof l != "function" && typeof l != "symbol" && !isNaN(l) && 1 <= l ? e.setAttribute(a, l) : e.removeAttribute(a);
        break;
      case "rowSpan":
      case "start":
        l == null || typeof l == "function" || typeof l == "symbol" || isNaN(l) ? e.removeAttribute(a) : e.setAttribute(a, l);
        break;
      case "popover":
        Ae("beforetoggle", e), Ae("toggle", e), Zr(e, "popover", l);
        break;
      case "xlinkActuate":
        ba(
          e,
          "http://www.w3.org/1999/xlink",
          "xlink:actuate",
          l
        );
        break;
      case "xlinkArcrole":
        ba(
          e,
          "http://www.w3.org/1999/xlink",
          "xlink:arcrole",
          l
        );
        break;
      case "xlinkRole":
        ba(
          e,
          "http://www.w3.org/1999/xlink",
          "xlink:role",
          l
        );
        break;
      case "xlinkShow":
        ba(
          e,
          "http://www.w3.org/1999/xlink",
          "xlink:show",
          l
        );
        break;
      case "xlinkTitle":
        ba(
          e,
          "http://www.w3.org/1999/xlink",
          "xlink:title",
          l
        );
        break;
      case "xlinkType":
        ba(
          e,
          "http://www.w3.org/1999/xlink",
          "xlink:type",
          l
        );
        break;
      case "xmlBase":
        ba(
          e,
          "http://www.w3.org/XML/1998/namespace",
          "xml:base",
          l
        );
        break;
      case "xmlLang":
        ba(
          e,
          "http://www.w3.org/XML/1998/namespace",
          "xml:lang",
          l
        );
        break;
      case "xmlSpace":
        ba(
          e,
          "http://www.w3.org/XML/1998/namespace",
          "xml:space",
          l
        );
        break;
      case "is":
        Zr(e, "is", l);
        break;
      case "innerText":
      case "textContent":
        break;
      default:
        (!(2 < a.length) || a[0] !== "o" && a[0] !== "O" || a[1] !== "n" && a[1] !== "N") && (a = vm.get(a) || a, Zr(e, a, l));
    }
  }
  function Qc(e, t, a, l, n, r) {
    switch (a) {
      case "style":
        Gs(e, l, r);
        break;
      case "dangerouslySetInnerHTML":
        if (l != null) {
          if (typeof l != "object" || !("__html" in l))
            throw Error(o(61));
          if (a = l.__html, a != null) {
            if (n.children != null) throw Error(o(60));
            e.innerHTML = a;
          }
        }
        break;
      case "children":
        typeof l == "string" ? Kl(e, l) : (typeof l == "number" || typeof l == "bigint") && Kl(e, "" + l);
        break;
      case "onScroll":
        l != null && Ae("scroll", e);
        break;
      case "onScrollEnd":
        l != null && Ae("scrollend", e);
        break;
      case "onClick":
        l != null && (e.onclick = va);
        break;
      case "suppressContentEditableWarning":
      case "suppressHydrationWarning":
      case "innerHTML":
      case "ref":
        break;
      case "innerText":
      case "textContent":
        break;
      default:
        if (!Os.hasOwnProperty(a))
          e: {
            if (a[0] === "o" && a[1] === "n" && (n = a.endsWith("Capture"), t = a.slice(2, n ? a.length - 7 : void 0), r = e[mt] || null, r = r != null ? r[a] : null, typeof r == "function" && e.removeEventListener(t, r, n), typeof l == "function")) {
              typeof r != "function" && r !== null && (a in e ? e[a] = null : e.hasAttribute(a) && e.removeAttribute(a)), e.addEventListener(t, l, n);
              break e;
            }
            a in e ? e[a] = l : l === !0 ? e.setAttribute(a, "") : Zr(e, a, l);
          }
    }
  }
  function bt(e, t, a) {
    switch (t) {
      case "div":
      case "span":
      case "svg":
      case "path":
      case "a":
      case "g":
      case "p":
      case "li":
        break;
      case "img":
        Ae("error", e), Ae("load", e);
        var l = !1, n = !1, r;
        for (r in a)
          if (a.hasOwnProperty(r)) {
            var c = a[r];
            if (c != null)
              switch (r) {
                case "src":
                  l = !0;
                  break;
                case "srcSet":
                  n = !0;
                  break;
                case "children":
                case "dangerouslySetInnerHTML":
                  throw Error(o(137, t));
                default:
                  qe(e, t, r, c, a, null);
              }
          }
        n && qe(e, t, "srcSet", a.srcSet, a, null), l && qe(e, t, "src", a.src, a, null);
        return;
      case "input":
        Ae("invalid", e);
        var d = r = c = n = null, y = null, M = null;
        for (l in a)
          if (a.hasOwnProperty(l)) {
            var N = a[l];
            if (N != null)
              switch (l) {
                case "name":
                  n = N;
                  break;
                case "type":
                  c = N;
                  break;
                case "checked":
                  y = N;
                  break;
                case "defaultChecked":
                  M = N;
                  break;
                case "value":
                  r = N;
                  break;
                case "defaultValue":
                  d = N;
                  break;
                case "children":
                case "dangerouslySetInnerHTML":
                  if (N != null)
                    throw Error(o(137, t));
                  break;
                default:
                  qe(e, t, l, N, a, null);
              }
          }
        Ls(
          e,
          r,
          d,
          y,
          M,
          c,
          n,
          !1
        );
        return;
      case "select":
        Ae("invalid", e), l = c = r = null;
        for (n in a)
          if (a.hasOwnProperty(n) && (d = a[n], d != null))
            switch (n) {
              case "value":
                r = d;
                break;
              case "defaultValue":
                c = d;
                break;
              case "multiple":
                l = d;
              default:
                qe(e, t, n, d, a, null);
            }
        t = r, a = c, e.multiple = !!l, t != null ? Ql(e, !!l, t, !1) : a != null && Ql(e, !!l, a, !0);
        return;
      case "textarea":
        Ae("invalid", e), r = n = l = null;
        for (c in a)
          if (a.hasOwnProperty(c) && (d = a[c], d != null))
            switch (c) {
              case "value":
                l = d;
                break;
              case "defaultValue":
                n = d;
                break;
              case "children":
                r = d;
                break;
              case "dangerouslySetInnerHTML":
                if (d != null) throw Error(o(91));
                break;
              default:
                qe(e, t, c, d, a, null);
            }
        qs(e, l, n, r);
        return;
      case "option":
        for (y in a)
          a.hasOwnProperty(y) && (l = a[y], l != null) && (y === "selected" ? e.selected = l && typeof l != "function" && typeof l != "symbol" : qe(e, t, y, l, a, null));
        return;
      case "dialog":
        Ae("beforetoggle", e), Ae("toggle", e), Ae("cancel", e), Ae("close", e);
        break;
      case "iframe":
      case "object":
        Ae("load", e);
        break;
      case "video":
      case "audio":
        for (l = 0; l < hr.length; l++)
          Ae(hr[l], e);
        break;
      case "image":
        Ae("error", e), Ae("load", e);
        break;
      case "details":
        Ae("toggle", e);
        break;
      case "embed":
      case "source":
      case "link":
        Ae("error", e), Ae("load", e);
      case "area":
      case "base":
      case "br":
      case "col":
      case "hr":
      case "keygen":
      case "meta":
      case "param":
      case "track":
      case "wbr":
      case "menuitem":
        for (M in a)
          if (a.hasOwnProperty(M) && (l = a[M], l != null))
            switch (M) {
              case "children":
              case "dangerouslySetInnerHTML":
                throw Error(o(137, t));
              default:
                qe(e, t, M, l, a, null);
            }
        return;
      default:
        if (ro(t)) {
          for (N in a)
            a.hasOwnProperty(N) && (l = a[N], l !== void 0 && Qc(
              e,
              t,
              N,
              l,
              a,
              void 0
            ));
          return;
        }
    }
    for (d in a)
      a.hasOwnProperty(d) && (l = a[d], l != null && qe(e, t, d, l, a, null));
  }
  function Xp(e, t, a, l) {
    switch (t) {
      case "div":
      case "span":
      case "svg":
      case "path":
      case "a":
      case "g":
      case "p":
      case "li":
        break;
      case "input":
        var n = null, r = null, c = null, d = null, y = null, M = null, N = null;
        for (H in a) {
          var O = a[H];
          if (a.hasOwnProperty(H) && O != null)
            switch (H) {
              case "checked":
                break;
              case "value":
                break;
              case "defaultValue":
                y = O;
              default:
                l.hasOwnProperty(H) || qe(e, t, H, null, l, O);
            }
        }
        for (var k in l) {
          var H = l[k];
          if (O = a[k], l.hasOwnProperty(k) && (H != null || O != null))
            switch (k) {
              case "type":
                r = H;
                break;
              case "name":
                n = H;
                break;
              case "checked":
                M = H;
                break;
              case "defaultChecked":
                N = H;
                break;
              case "value":
                c = H;
                break;
              case "defaultValue":
                d = H;
                break;
              case "children":
              case "dangerouslySetInnerHTML":
                if (H != null)
                  throw Error(o(137, t));
                break;
              default:
                H !== O && qe(
                  e,
                  t,
                  k,
                  H,
                  l,
                  O
                );
            }
        }
        lo(
          e,
          c,
          d,
          y,
          M,
          N,
          r,
          n
        );
        return;
      case "select":
        H = c = d = k = null;
        for (r in a)
          if (y = a[r], a.hasOwnProperty(r) && y != null)
            switch (r) {
              case "value":
                break;
              case "multiple":
                H = y;
              default:
                l.hasOwnProperty(r) || qe(
                  e,
                  t,
                  r,
                  null,
                  l,
                  y
                );
            }
        for (n in l)
          if (r = l[n], y = a[n], l.hasOwnProperty(n) && (r != null || y != null))
            switch (n) {
              case "value":
                k = r;
                break;
              case "defaultValue":
                d = r;
                break;
              case "multiple":
                c = r;
              default:
                r !== y && qe(
                  e,
                  t,
                  n,
                  r,
                  l,
                  y
                );
            }
        t = d, a = c, l = H, k != null ? Ql(e, !!a, k, !1) : !!l != !!a && (t != null ? Ql(e, !!a, t, !0) : Ql(e, !!a, a ? [] : "", !1));
        return;
      case "textarea":
        H = k = null;
        for (d in a)
          if (n = a[d], a.hasOwnProperty(d) && n != null && !l.hasOwnProperty(d))
            switch (d) {
              case "value":
                break;
              case "children":
                break;
              default:
                qe(e, t, d, null, l, n);
            }
        for (c in l)
          if (n = l[c], r = a[c], l.hasOwnProperty(c) && (n != null || r != null))
            switch (c) {
              case "value":
                k = n;
                break;
              case "defaultValue":
                H = n;
                break;
              case "children":
                break;
              case "dangerouslySetInnerHTML":
                if (n != null) throw Error(o(91));
                break;
              default:
                n !== r && qe(e, t, c, n, l, r);
            }
        Us(e, k, H);
        return;
      case "option":
        for (var P in a)
          k = a[P], a.hasOwnProperty(P) && k != null && !l.hasOwnProperty(P) && (P === "selected" ? e.selected = !1 : qe(
            e,
            t,
            P,
            null,
            l,
            k
          ));
        for (y in l)
          k = l[y], H = a[y], l.hasOwnProperty(y) && k !== H && (k != null || H != null) && (y === "selected" ? e.selected = k && typeof k != "function" && typeof k != "symbol" : qe(
            e,
            t,
            y,
            k,
            l,
            H
          ));
        return;
      case "img":
      case "link":
      case "area":
      case "base":
      case "br":
      case "col":
      case "embed":
      case "hr":
      case "keygen":
      case "meta":
      case "param":
      case "source":
      case "track":
      case "wbr":
      case "menuitem":
        for (var ce in a)
          k = a[ce], a.hasOwnProperty(ce) && k != null && !l.hasOwnProperty(ce) && qe(e, t, ce, null, l, k);
        for (M in l)
          if (k = l[M], H = a[M], l.hasOwnProperty(M) && k !== H && (k != null || H != null))
            switch (M) {
              case "children":
              case "dangerouslySetInnerHTML":
                if (k != null)
                  throw Error(o(137, t));
                break;
              default:
                qe(
                  e,
                  t,
                  M,
                  k,
                  l,
                  H
                );
            }
        return;
      default:
        if (ro(t)) {
          for (var Be in a)
            k = a[Be], a.hasOwnProperty(Be) && k !== void 0 && !l.hasOwnProperty(Be) && Qc(
              e,
              t,
              Be,
              void 0,
              l,
              k
            );
          for (N in l)
            k = l[N], H = a[N], !l.hasOwnProperty(N) || k === H || k === void 0 && H === void 0 || Qc(
              e,
              t,
              N,
              k,
              l,
              H
            );
          return;
        }
    }
    for (var E in a)
      k = a[E], a.hasOwnProperty(E) && k != null && !l.hasOwnProperty(E) && qe(e, t, E, null, l, k);
    for (O in l)
      k = l[O], H = a[O], !l.hasOwnProperty(O) || k === H || k == null && H == null || qe(e, t, O, k, l, H);
  }
  function Y1(e) {
    switch (e) {
      case "css":
      case "script":
      case "font":
      case "img":
      case "image":
      case "input":
      case "link":
        return !0;
      default:
        return !1;
    }
  }
  function Qp() {
    if (typeof performance.getEntriesByType == "function") {
      for (var e = 0, t = 0, a = performance.getEntriesByType("resource"), l = 0; l < a.length; l++) {
        var n = a[l], r = n.transferSize, c = n.initiatorType, d = n.duration;
        if (r && d && Y1(c)) {
          for (c = 0, d = n.responseEnd, l += 1; l < a.length; l++) {
            var y = a[l], M = y.startTime;
            if (M > d) break;
            var N = y.transferSize, O = y.initiatorType;
            N && Y1(O) && (y = y.responseEnd, c += N * (y < d ? 1 : (d - M) / (y - M)));
          }
          if (--l, t += 8 * (r + c) / (n.duration / 1e3), e++, 10 < e) break;
        }
      }
      if (0 < e) return t / e / 1e6;
    }
    return navigator.connection && (e = navigator.connection.downlink, typeof e == "number") ? e : 5;
  }
  var Kc = null, Jc = null;
  function ji(e) {
    return e.nodeType === 9 ? e : e.ownerDocument;
  }
  function X1(e) {
    switch (e) {
      case "http://www.w3.org/2000/svg":
        return 1;
      case "http://www.w3.org/1998/Math/MathML":
        return 2;
      default:
        return 0;
    }
  }
  function Q1(e, t) {
    if (e === 0)
      switch (t) {
        case "svg":
          return 1;
        case "math":
          return 2;
        default:
          return 0;
      }
    return e === 1 && t === "foreignObject" ? 0 : e;
  }
  function $c(e, t) {
    return e === "textarea" || e === "noscript" || typeof t.children == "string" || typeof t.children == "number" || typeof t.children == "bigint" || typeof t.dangerouslySetInnerHTML == "object" && t.dangerouslySetInnerHTML !== null && t.dangerouslySetInnerHTML.__html != null;
  }
  var Fc = null;
  function Kp() {
    var e = window.event;
    return e && e.type === "popstate" ? e === Fc ? !1 : (Fc = e, !0) : (Fc = null, !1);
  }
  var K1 = typeof setTimeout == "function" ? setTimeout : void 0, Jp = typeof clearTimeout == "function" ? clearTimeout : void 0, J1 = typeof Promise == "function" ? Promise : void 0, $p = typeof queueMicrotask == "function" ? queueMicrotask : typeof J1 < "u" ? function(e) {
    return J1.resolve(null).then(e).catch(Fp);
  } : K1;
  function Fp(e) {
    setTimeout(function() {
      throw e;
    });
  }
  function tl(e) {
    return e === "head";
  }
  function $1(e, t) {
    var a = t, l = 0;
    do {
      var n = a.nextSibling;
      if (e.removeChild(a), n && n.nodeType === 8)
        if (a = n.data, a === "/$" || a === "/&") {
          if (l === 0) {
            e.removeChild(n), Mn(t);
            return;
          }
          l--;
        } else if (a === "$" || a === "$?" || a === "$~" || a === "$!" || a === "&")
          l++;
        else if (a === "html")
          br(e.ownerDocument.documentElement);
        else if (a === "head") {
          a = e.ownerDocument.head, br(a);
          for (var r = a.firstChild; r; ) {
            var c = r.nextSibling, d = r.nodeName;
            r[Zn] || d === "SCRIPT" || d === "STYLE" || d === "LINK" && r.rel.toLowerCase() === "stylesheet" || a.removeChild(r), r = c;
          }
        } else
          a === "body" && br(e.ownerDocument.body);
      a = n;
    } while (a);
    Mn(t);
  }
  function F1(e, t) {
    var a = e;
    e = 0;
    do {
      var l = a.nextSibling;
      if (a.nodeType === 1 ? t ? (a._stashedDisplay = a.style.display, a.style.display = "none") : (a.style.display = a._stashedDisplay || "", a.getAttribute("style") === "" && a.removeAttribute("style")) : a.nodeType === 3 && (t ? (a._stashedText = a.nodeValue, a.nodeValue = "") : a.nodeValue = a._stashedText || ""), l && l.nodeType === 8)
        if (a = l.data, a === "/$") {
          if (e === 0) break;
          e--;
        } else
          a !== "$" && a !== "$?" && a !== "$~" && a !== "$!" || e++;
      a = l;
    } while (a);
  }
  function Wc(e) {
    var t = e.firstChild;
    for (t && t.nodeType === 10 && (t = t.nextSibling); t; ) {
      var a = t;
      switch (t = t.nextSibling, a.nodeName) {
        case "HTML":
        case "HEAD":
        case "BODY":
          Wc(a), to(a);
          continue;
        case "SCRIPT":
        case "STYLE":
          continue;
        case "LINK":
          if (a.rel.toLowerCase() === "stylesheet") continue;
      }
      e.removeChild(a);
    }
  }
  function Wp(e, t, a, l) {
    for (; e.nodeType === 1; ) {
      var n = a;
      if (e.nodeName.toLowerCase() !== t.toLowerCase()) {
        if (!l && (e.nodeName !== "INPUT" || e.type !== "hidden"))
          break;
      } else if (l) {
        if (!e[Zn])
          switch (t) {
            case "meta":
              if (!e.hasAttribute("itemprop")) break;
              return e;
            case "link":
              if (r = e.getAttribute("rel"), r === "stylesheet" && e.hasAttribute("data-precedence"))
                break;
              if (r !== n.rel || e.getAttribute("href") !== (n.href == null || n.href === "" ? null : n.href) || e.getAttribute("crossorigin") !== (n.crossOrigin == null ? null : n.crossOrigin) || e.getAttribute("title") !== (n.title == null ? null : n.title))
                break;
              return e;
            case "style":
              if (e.hasAttribute("data-precedence")) break;
              return e;
            case "script":
              if (r = e.getAttribute("src"), (r !== (n.src == null ? null : n.src) || e.getAttribute("type") !== (n.type == null ? null : n.type) || e.getAttribute("crossorigin") !== (n.crossOrigin == null ? null : n.crossOrigin)) && r && e.hasAttribute("async") && !e.hasAttribute("itemprop"))
                break;
              return e;
            default:
              return e;
          }
      } else if (t === "input" && e.type === "hidden") {
        var r = n.name == null ? null : "" + n.name;
        if (n.type === "hidden" && e.getAttribute("name") === r)
          return e;
      } else return e;
      if (e = Wt(e.nextSibling), e === null) break;
    }
    return null;
  }
  function Ip(e, t, a) {
    if (t === "") return null;
    for (; e.nodeType !== 3; )
      if ((e.nodeType !== 1 || e.nodeName !== "INPUT" || e.type !== "hidden") && !a || (e = Wt(e.nextSibling), e === null)) return null;
    return e;
  }
  function W1(e, t) {
    for (; e.nodeType !== 8; )
      if ((e.nodeType !== 1 || e.nodeName !== "INPUT" || e.type !== "hidden") && !t || (e = Wt(e.nextSibling), e === null)) return null;
    return e;
  }
  function Ic(e) {
    return e.data === "$?" || e.data === "$~";
  }
  function Pc(e) {
    return e.data === "$!" || e.data === "$?" && e.ownerDocument.readyState !== "loading";
  }
  function Pp(e, t) {
    var a = e.ownerDocument;
    if (e.data === "$~") e._reactRetry = t;
    else if (e.data !== "$?" || a.readyState !== "loading")
      t();
    else {
      var l = function() {
        t(), a.removeEventListener("DOMContentLoaded", l);
      };
      a.addEventListener("DOMContentLoaded", l), e._reactRetry = l;
    }
  }
  function Wt(e) {
    for (; e != null; e = e.nextSibling) {
      var t = e.nodeType;
      if (t === 1 || t === 3) break;
      if (t === 8) {
        if (t = e.data, t === "$" || t === "$!" || t === "$?" || t === "$~" || t === "&" || t === "F!" || t === "F")
          break;
        if (t === "/$" || t === "/&") return null;
      }
    }
    return e;
  }
  var es = null;
  function I1(e) {
    e = e.nextSibling;
    for (var t = 0; e; ) {
      if (e.nodeType === 8) {
        var a = e.data;
        if (a === "/$" || a === "/&") {
          if (t === 0)
            return Wt(e.nextSibling);
          t--;
        } else
          a !== "$" && a !== "$!" && a !== "$?" && a !== "$~" && a !== "&" || t++;
      }
      e = e.nextSibling;
    }
    return null;
  }
  function P1(e) {
    e = e.previousSibling;
    for (var t = 0; e; ) {
      if (e.nodeType === 8) {
        var a = e.data;
        if (a === "$" || a === "$!" || a === "$?" || a === "$~" || a === "&") {
          if (t === 0) return e;
          t--;
        } else a !== "/$" && a !== "/&" || t++;
      }
      e = e.previousSibling;
    }
    return null;
  }
  function ef(e, t, a) {
    switch (t = ji(a), e) {
      case "html":
        if (e = t.documentElement, !e) throw Error(o(452));
        return e;
      case "head":
        if (e = t.head, !e) throw Error(o(453));
        return e;
      case "body":
        if (e = t.body, !e) throw Error(o(454));
        return e;
      default:
        throw Error(o(451));
    }
  }
  function br(e) {
    for (var t = e.attributes; t.length; )
      e.removeAttributeNode(t[0]);
    to(e);
  }
  var It = /* @__PURE__ */ new Map(), tf = /* @__PURE__ */ new Set();
  function Oi(e) {
    return typeof e.getRootNode == "function" ? e.getRootNode() : e.nodeType === 9 ? e : e.ownerDocument;
  }
  var Oa = q.d;
  q.d = {
    f: eh,
    r: th,
    D: ah,
    C: lh,
    L: nh,
    m: rh,
    X: oh,
    S: ih,
    M: ch
  };
  function eh() {
    var e = Oa.f(), t = Mi();
    return e || t;
  }
  function th(e) {
    var t = Gl(e);
    t !== null && t.tag === 5 && t.type === "form" ? v0(t) : Oa.r(e);
  }
  var Sn = typeof document > "u" ? null : document;
  function af(e, t, a) {
    var l = Sn;
    if (l && typeof t == "string" && t) {
      var n = Yt(t);
      n = 'link[rel="' + e + '"][href="' + n + '"]', typeof a == "string" && (n += '[crossorigin="' + a + '"]'), tf.has(n) || (tf.add(n), e = { rel: e, crossOrigin: a, href: t }, l.querySelector(n) === null && (t = l.createElement("link"), bt(t, "link", e), ft(t), l.head.appendChild(t)));
    }
  }
  function ah(e) {
    Oa.D(e), af("dns-prefetch", e, null);
  }
  function lh(e, t) {
    Oa.C(e, t), af("preconnect", e, t);
  }
  function nh(e, t, a) {
    Oa.L(e, t, a);
    var l = Sn;
    if (l && e && t) {
      var n = 'link[rel="preload"][as="' + Yt(t) + '"]';
      t === "image" && a && a.imageSrcSet ? (n += '[imagesrcset="' + Yt(
        a.imageSrcSet
      ) + '"]', typeof a.imageSizes == "string" && (n += '[imagesizes="' + Yt(
        a.imageSizes
      ) + '"]')) : n += '[href="' + Yt(e) + '"]';
      var r = n;
      switch (t) {
        case "style":
          r = En(e);
          break;
        case "script":
          r = An(e);
      }
      It.has(r) || (e = C(
        {
          rel: "preload",
          href: t === "image" && a && a.imageSrcSet ? void 0 : e,
          as: t
        },
        a
      ), It.set(r, e), l.querySelector(n) !== null || t === "style" && l.querySelector(vr(r)) || t === "script" && l.querySelector(xr(r)) || (t = l.createElement("link"), bt(t, "link", e), ft(t), l.head.appendChild(t)));
    }
  }
  function rh(e, t) {
    Oa.m(e, t);
    var a = Sn;
    if (a && e) {
      var l = t && typeof t.as == "string" ? t.as : "script", n = 'link[rel="modulepreload"][as="' + Yt(l) + '"][href="' + Yt(e) + '"]', r = n;
      switch (l) {
        case "audioworklet":
        case "paintworklet":
        case "serviceworker":
        case "sharedworker":
        case "worker":
        case "script":
          r = An(e);
      }
      if (!It.has(r) && (e = C({ rel: "modulepreload", href: e }, t), It.set(r, e), a.querySelector(n) === null)) {
        switch (l) {
          case "audioworklet":
          case "paintworklet":
          case "serviceworker":
          case "sharedworker":
          case "worker":
          case "script":
            if (a.querySelector(xr(r)))
              return;
        }
        l = a.createElement("link"), bt(l, "link", e), ft(l), a.head.appendChild(l);
      }
    }
  }
  function ih(e, t, a) {
    Oa.S(e, t, a);
    var l = Sn;
    if (l && e) {
      var n = Yl(l).hoistableStyles, r = En(e);
      t = t || "default";
      var c = n.get(r);
      if (!c) {
        var d = { loading: 0, preload: null };
        if (c = l.querySelector(
          vr(r)
        ))
          d.loading = 5;
        else {
          e = C(
            { rel: "stylesheet", href: e, "data-precedence": t },
            a
          ), (a = It.get(r)) && ts(e, a);
          var y = c = l.createElement("link");
          ft(y), bt(y, "link", e), y._p = new Promise(function(M, N) {
            y.onload = M, y.onerror = N;
          }), y.addEventListener("load", function() {
            d.loading |= 1;
          }), y.addEventListener("error", function() {
            d.loading |= 2;
          }), d.loading |= 4, Zi(c, t, l);
        }
        c = {
          type: "stylesheet",
          instance: c,
          count: 1,
          state: d
        }, n.set(r, c);
      }
    }
  }
  function oh(e, t) {
    Oa.X(e, t);
    var a = Sn;
    if (a && e) {
      var l = Yl(a).hoistableScripts, n = An(e), r = l.get(n);
      r || (r = a.querySelector(xr(n)), r || (e = C({ src: e, async: !0 }, t), (t = It.get(n)) && as(e, t), r = a.createElement("script"), ft(r), bt(r, "link", e), a.head.appendChild(r)), r = {
        type: "script",
        instance: r,
        count: 1,
        state: null
      }, l.set(n, r));
    }
  }
  function ch(e, t) {
    Oa.M(e, t);
    var a = Sn;
    if (a && e) {
      var l = Yl(a).hoistableScripts, n = An(e), r = l.get(n);
      r || (r = a.querySelector(xr(n)), r || (e = C({ src: e, async: !0, type: "module" }, t), (t = It.get(n)) && as(e, t), r = a.createElement("script"), ft(r), bt(r, "link", e), a.head.appendChild(r)), r = {
        type: "script",
        instance: r,
        count: 1,
        state: null
      }, l.set(n, r));
    }
  }
  function lf(e, t, a, l) {
    var n = (n = le.current) ? Oi(n) : null;
    if (!n) throw Error(o(446));
    switch (e) {
      case "meta":
      case "title":
        return null;
      case "style":
        return typeof a.precedence == "string" && typeof a.href == "string" ? (t = En(a.href), a = Yl(
          n
        ).hoistableStyles, l = a.get(t), l || (l = {
          type: "style",
          instance: null,
          count: 0,
          state: null
        }, a.set(t, l)), l) : { type: "void", instance: null, count: 0, state: null };
      case "link":
        if (a.rel === "stylesheet" && typeof a.href == "string" && typeof a.precedence == "string") {
          e = En(a.href);
          var r = Yl(
            n
          ).hoistableStyles, c = r.get(e);
          if (c || (n = n.ownerDocument || n, c = {
            type: "stylesheet",
            instance: null,
            count: 0,
            state: { loading: 0, preload: null }
          }, r.set(e, c), (r = n.querySelector(
            vr(e)
          )) && !r._p && (c.instance = r, c.state.loading = 5), It.has(e) || (a = {
            rel: "preload",
            as: "style",
            href: a.href,
            crossOrigin: a.crossOrigin,
            integrity: a.integrity,
            media: a.media,
            hrefLang: a.hrefLang,
            referrerPolicy: a.referrerPolicy
          }, It.set(e, a), r || sh(
            n,
            e,
            a,
            c.state
          ))), t && l === null)
            throw Error(o(528, ""));
          return c;
        }
        if (t && l !== null)
          throw Error(o(529, ""));
        return null;
      case "script":
        return t = a.async, a = a.src, typeof a == "string" && t && typeof t != "function" && typeof t != "symbol" ? (t = An(a), a = Yl(
          n
        ).hoistableScripts, l = a.get(t), l || (l = {
          type: "script",
          instance: null,
          count: 0,
          state: null
        }, a.set(t, l)), l) : { type: "void", instance: null, count: 0, state: null };
      default:
        throw Error(o(444, e));
    }
  }
  function En(e) {
    return 'href="' + Yt(e) + '"';
  }
  function vr(e) {
    return 'link[rel="stylesheet"][' + e + "]";
  }
  function nf(e) {
    return C({}, e, {
      "data-precedence": e.precedence,
      precedence: null
    });
  }
  function sh(e, t, a, l) {
    e.querySelector('link[rel="preload"][as="style"][' + t + "]") ? l.loading = 1 : (t = e.createElement("link"), l.preload = t, t.addEventListener("load", function() {
      return l.loading |= 1;
    }), t.addEventListener("error", function() {
      return l.loading |= 2;
    }), bt(t, "link", a), ft(t), e.head.appendChild(t));
  }
  function An(e) {
    return '[src="' + Yt(e) + '"]';
  }
  function xr(e) {
    return "script[async]" + e;
  }
  function rf(e, t, a) {
    if (t.count++, t.instance === null)
      switch (t.type) {
        case "style":
          var l = e.querySelector(
            'style[data-href~="' + Yt(a.href) + '"]'
          );
          if (l)
            return t.instance = l, ft(l), l;
          var n = C({}, a, {
            "data-href": a.href,
            "data-precedence": a.precedence,
            href: null,
            precedence: null
          });
          return l = (e.ownerDocument || e).createElement(
            "style"
          ), ft(l), bt(l, "style", n), Zi(l, a.precedence, e), t.instance = l;
        case "stylesheet":
          n = En(a.href);
          var r = e.querySelector(
            vr(n)
          );
          if (r)
            return t.state.loading |= 4, t.instance = r, ft(r), r;
          l = nf(a), (n = It.get(n)) && ts(l, n), r = (e.ownerDocument || e).createElement("link"), ft(r);
          var c = r;
          return c._p = new Promise(function(d, y) {
            c.onload = d, c.onerror = y;
          }), bt(r, "link", l), t.state.loading |= 4, Zi(r, a.precedence, e), t.instance = r;
        case "script":
          return r = An(a.src), (n = e.querySelector(
            xr(r)
          )) ? (t.instance = n, ft(n), n) : (l = a, (n = It.get(r)) && (l = C({}, a), as(l, n)), e = e.ownerDocument || e, n = e.createElement("script"), ft(n), bt(n, "link", l), e.head.appendChild(n), t.instance = n);
        case "void":
          return null;
        default:
          throw Error(o(443, t.type));
      }
    else
      t.type === "stylesheet" && (t.state.loading & 4) === 0 && (l = t.instance, t.state.loading |= 4, Zi(l, a.precedence, e));
    return t.instance;
  }
  function Zi(e, t, a) {
    for (var l = a.querySelectorAll(
      'link[rel="stylesheet"][data-precedence],style[data-precedence]'
    ), n = l.length ? l[l.length - 1] : null, r = n, c = 0; c < l.length; c++) {
      var d = l[c];
      if (d.dataset.precedence === t) r = d;
      else if (r !== n) break;
    }
    r ? r.parentNode.insertBefore(e, r.nextSibling) : (t = a.nodeType === 9 ? a.head : a, t.insertBefore(e, t.firstChild));
  }
  function ts(e, t) {
    e.crossOrigin == null && (e.crossOrigin = t.crossOrigin), e.referrerPolicy == null && (e.referrerPolicy = t.referrerPolicy), e.title == null && (e.title = t.title);
  }
  function as(e, t) {
    e.crossOrigin == null && (e.crossOrigin = t.crossOrigin), e.referrerPolicy == null && (e.referrerPolicy = t.referrerPolicy), e.integrity == null && (e.integrity = t.integrity);
  }
  var Di = null;
  function of(e, t, a) {
    if (Di === null) {
      var l = /* @__PURE__ */ new Map(), n = Di = /* @__PURE__ */ new Map();
      n.set(a, l);
    } else
      n = Di, l = n.get(a), l || (l = /* @__PURE__ */ new Map(), n.set(a, l));
    if (l.has(e)) return l;
    for (l.set(e, null), a = a.getElementsByTagName(e), n = 0; n < a.length; n++) {
      var r = a[n];
      if (!(r[Zn] || r[Ie] || e === "link" && r.getAttribute("rel") === "stylesheet") && r.namespaceURI !== "http://www.w3.org/2000/svg") {
        var c = r.getAttribute(t) || "";
        c = e + c;
        var d = l.get(c);
        d ? d.push(r) : l.set(c, [r]);
      }
    }
    return l;
  }
  function cf(e, t, a) {
    e = e.ownerDocument || e, e.head.insertBefore(
      a,
      t === "title" ? e.querySelector("head > title") : null
    );
  }
  function uh(e, t, a) {
    if (a === 1 || t.itemProp != null) return !1;
    switch (e) {
      case "meta":
      case "title":
        return !0;
      case "style":
        if (typeof t.precedence != "string" || typeof t.href != "string" || t.href === "")
          break;
        return !0;
      case "link":
        if (typeof t.rel != "string" || typeof t.href != "string" || t.href === "" || t.onLoad || t.onError)
          break;
        return t.rel === "stylesheet" ? (e = t.disabled, typeof t.precedence == "string" && e == null) : !0;
      case "script":
        if (t.async && typeof t.async != "function" && typeof t.async != "symbol" && !t.onLoad && !t.onError && t.src && typeof t.src == "string")
          return !0;
    }
    return !1;
  }
  function sf(e) {
    return !(e.type === "stylesheet" && (e.state.loading & 3) === 0);
  }
  function fh(e, t, a, l) {
    if (a.type === "stylesheet" && (typeof l.media != "string" || matchMedia(l.media).matches !== !1) && (a.state.loading & 4) === 0) {
      if (a.instance === null) {
        var n = En(l.href), r = t.querySelector(
          vr(n)
        );
        if (r) {
          t = r._p, t !== null && typeof t == "object" && typeof t.then == "function" && (e.count++, e = Ri.bind(e), t.then(e, e)), a.state.loading |= 4, a.instance = r, ft(r);
          return;
        }
        r = t.ownerDocument || t, l = nf(l), (n = It.get(n)) && ts(l, n), r = r.createElement("link"), ft(r);
        var c = r;
        c._p = new Promise(function(d, y) {
          c.onload = d, c.onerror = y;
        }), bt(r, "link", l), a.instance = r;
      }
      e.stylesheets === null && (e.stylesheets = /* @__PURE__ */ new Map()), e.stylesheets.set(a, t), (t = a.state.preload) && (a.state.loading & 3) === 0 && (e.count++, a = Ri.bind(e), t.addEventListener("load", a), t.addEventListener("error", a));
    }
  }
  var ls = 0;
  function dh(e, t) {
    return e.stylesheets && e.count === 0 && Li(e, e.stylesheets), 0 < e.count || 0 < e.imgCount ? function(a) {
      var l = setTimeout(function() {
        if (e.stylesheets && Li(e, e.stylesheets), e.unsuspend) {
          var r = e.unsuspend;
          e.unsuspend = null, r();
        }
      }, 6e4 + t);
      0 < e.imgBytes && ls === 0 && (ls = 62500 * Qp());
      var n = setTimeout(
        function() {
          if (e.waitingForImages = !1, e.count === 0 && (e.stylesheets && Li(e, e.stylesheets), e.unsuspend)) {
            var r = e.unsuspend;
            e.unsuspend = null, r();
          }
        },
        (e.imgBytes > ls ? 50 : 800) + t
      );
      return e.unsuspend = a, function() {
        e.unsuspend = null, clearTimeout(l), clearTimeout(n);
      };
    } : null;
  }
  function Ri() {
    if (this.count--, this.count === 0 && (this.imgCount === 0 || !this.waitingForImages)) {
      if (this.stylesheets) Li(this, this.stylesheets);
      else if (this.unsuspend) {
        var e = this.unsuspend;
        this.unsuspend = null, e();
      }
    }
  }
  var Vi = null;
  function Li(e, t) {
    e.stylesheets = null, e.unsuspend !== null && (e.count++, Vi = /* @__PURE__ */ new Map(), t.forEach(mh, e), Vi = null, Ri.call(e));
  }
  function mh(e, t) {
    if (!(t.state.loading & 4)) {
      var a = Vi.get(e);
      if (a) var l = a.get(null);
      else {
        a = /* @__PURE__ */ new Map(), Vi.set(e, a);
        for (var n = e.querySelectorAll(
          "link[data-precedence],style[data-precedence]"
        ), r = 0; r < n.length; r++) {
          var c = n[r];
          (c.nodeName === "LINK" || c.getAttribute("media") !== "not all") && (a.set(c.dataset.precedence, c), l = c);
        }
        l && a.set(null, l);
      }
      n = t.instance, c = n.getAttribute("data-precedence"), r = a.get(c) || l, r === l && a.set(null, n), a.set(c, n), this.count++, l = Ri.bind(this), n.addEventListener("load", l), n.addEventListener("error", l), r ? r.parentNode.insertBefore(n, r.nextSibling) : (e = e.nodeType === 9 ? e.head : e, e.insertBefore(n, e.firstChild)), t.state.loading |= 4;
    }
  }
  var yr = {
    $$typeof: be,
    Provider: null,
    Consumer: null,
    _currentValue: ne,
    _currentValue2: ne,
    _threadCount: 0
  };
  function ph(e, t, a, l, n, r, c, d, y) {
    this.tag = 1, this.containerInfo = e, this.pingCache = this.current = this.pendingChildren = null, this.timeoutHandle = -1, this.callbackNode = this.next = this.pendingContext = this.context = this.cancelPendingCommit = null, this.callbackPriority = 0, this.expirationTimes = ta(-1), this.entangledLanes = this.shellSuspendCounter = this.errorRecoveryDisabledLanes = this.expiredLanes = this.warmLanes = this.pingedLanes = this.suspendedLanes = this.pendingLanes = 0, this.entanglements = ta(0), this.hiddenUpdates = ta(null), this.identifierPrefix = l, this.onUncaughtError = n, this.onCaughtError = r, this.onRecoverableError = c, this.pooledCache = null, this.pooledCacheLanes = 0, this.formState = y, this.incompleteTransitions = /* @__PURE__ */ new Map();
  }
  function uf(e, t, a, l, n, r, c, d, y, M, N, O) {
    return e = new ph(
      e,
      t,
      a,
      c,
      y,
      M,
      N,
      O,
      d
    ), t = 1, r === !0 && (t |= 24), r = Ot(3, null, null, t), e.current = r, r.stateNode = e, t = Do(), t.refCount++, e.pooledCache = t, t.refCount++, r.memoizedState = {
      element: l,
      isDehydrated: a,
      cache: t
    }, Uo(r), e;
  }
  function ff(e) {
    return e ? (e = en, e) : en;
  }
  function df(e, t, a, l, n, r) {
    n = ff(n), l.context === null ? l.context = n : l.pendingContext = n, l = Ya(t), l.payload = { element: a }, r = r === void 0 ? null : r, r !== null && (l.callback = r), a = Xa(e, l, t), a !== null && (_t(a, e, t), In(a, e, t));
  }
  function mf(e, t) {
    if (e = e.memoizedState, e !== null && e.dehydrated !== null) {
      var a = e.retryLane;
      e.retryLane = a !== 0 && a < t ? a : t;
    }
  }
  function ns(e, t) {
    mf(e, t), (e = e.alternate) && mf(e, t);
  }
  function pf(e) {
    if (e.tag === 13 || e.tag === 31) {
      var t = bl(e, 67108864);
      t !== null && _t(t, e, 67108864), ns(e, 67108864);
    }
  }
  function hf(e) {
    if (e.tag === 13 || e.tag === 31) {
      var t = Lt();
      t = ul(t);
      var a = bl(e, t);
      a !== null && _t(a, e, t), ns(e, t);
    }
  }
  var Ui = !0;
  function hh(e, t, a, l) {
    var n = z.T;
    z.T = null;
    var r = q.p;
    try {
      q.p = 2, rs(e, t, a, l);
    } finally {
      q.p = r, z.T = n;
    }
  }
  function gh(e, t, a, l) {
    var n = z.T;
    z.T = null;
    var r = q.p;
    try {
      q.p = 8, rs(e, t, a, l);
    } finally {
      q.p = r, z.T = n;
    }
  }
  function rs(e, t, a, l) {
    if (Ui) {
      var n = is(l);
      if (n === null)
        Xc(
          e,
          t,
          l,
          qi,
          a
        ), bf(e, l);
      else if (vh(
        n,
        e,
        t,
        a,
        l
      ))
        l.stopPropagation();
      else if (bf(e, l), t & 4 && -1 < bh.indexOf(e)) {
        for (; n !== null; ) {
          var r = Gl(n);
          if (r !== null)
            switch (r.tag) {
              case 3:
                if (r = r.stateNode, r.current.memoizedState.isDehydrated) {
                  var c = Ze(r.pendingLanes);
                  if (c !== 0) {
                    var d = r;
                    for (d.pendingLanes |= 2, d.entangledLanes |= 2; c; ) {
                      var y = 1 << 31 - yt(c);
                      d.entanglements[1] |= y, c &= ~y;
                    }
                    da(r), (je & 6) === 0 && (Ei = Ke() + 500, pr(0));
                  }
                }
                break;
              case 31:
              case 13:
                d = bl(r, 2), d !== null && _t(d, r, 2), Mi(), ns(r, 2);
            }
          if (r = is(l), r === null && Xc(
            e,
            t,
            l,
            qi,
            a
          ), r === n) break;
          n = r;
        }
        n !== null && l.stopPropagation();
      } else
        Xc(
          e,
          t,
          l,
          null,
          a
        );
    }
  }
  function is(e) {
    return e = oo(e), os(e);
  }
  var qi = null;
  function os(e) {
    if (qi = null, e = Bl(e), e !== null) {
      var t = p(e);
      if (t === null) e = null;
      else {
        var a = t.tag;
        if (a === 13) {
          if (e = h(t), e !== null) return e;
          e = null;
        } else if (a === 31) {
          if (e = v(t), e !== null) return e;
          e = null;
        } else if (a === 3) {
          if (t.stateNode.current.memoizedState.isDehydrated)
            return t.tag === 3 ? t.stateNode.containerInfo : null;
          e = null;
        } else t !== e && (e = null);
      }
    }
    return qi = e, null;
  }
  function gf(e) {
    switch (e) {
      case "beforetoggle":
      case "cancel":
      case "click":
      case "close":
      case "contextmenu":
      case "copy":
      case "cut":
      case "auxclick":
      case "dblclick":
      case "dragend":
      case "dragstart":
      case "drop":
      case "focusin":
      case "focusout":
      case "input":
      case "invalid":
      case "keydown":
      case "keypress":
      case "keyup":
      case "mousedown":
      case "mouseup":
      case "paste":
      case "pause":
      case "play":
      case "pointercancel":
      case "pointerdown":
      case "pointerup":
      case "ratechange":
      case "reset":
      case "resize":
      case "seeked":
      case "submit":
      case "toggle":
      case "touchcancel":
      case "touchend":
      case "touchstart":
      case "volumechange":
      case "change":
      case "selectionchange":
      case "textInput":
      case "compositionstart":
      case "compositionend":
      case "compositionupdate":
      case "beforeblur":
      case "afterblur":
      case "beforeinput":
      case "blur":
      case "fullscreenchange":
      case "focus":
      case "hashchange":
      case "popstate":
      case "select":
      case "selectstart":
        return 2;
      case "drag":
      case "dragenter":
      case "dragexit":
      case "dragleave":
      case "dragover":
      case "mousemove":
      case "mouseout":
      case "mouseover":
      case "pointermove":
      case "pointerout":
      case "pointerover":
      case "scroll":
      case "touchmove":
      case "wheel":
      case "mouseenter":
      case "mouseleave":
      case "pointerenter":
      case "pointerleave":
        return 8;
      case "message":
        switch (Nn()) {
          case _r:
            return 2;
          case Ul:
            return 8;
          case ea:
          case jr:
            return 32;
          case Cn:
            return 268435456;
          default:
            return 32;
        }
      default:
        return 32;
    }
  }
  var cs = !1, al = null, ll = null, nl = null, wr = /* @__PURE__ */ new Map(), Sr = /* @__PURE__ */ new Map(), rl = [], bh = "mousedown mouseup touchcancel touchend touchstart auxclick dblclick pointercancel pointerdown pointerup dragend dragstart drop compositionend compositionstart keydown keypress keyup input textInput copy cut paste click change contextmenu reset".split(
    " "
  );
  function bf(e, t) {
    switch (e) {
      case "focusin":
      case "focusout":
        al = null;
        break;
      case "dragenter":
      case "dragleave":
        ll = null;
        break;
      case "mouseover":
      case "mouseout":
        nl = null;
        break;
      case "pointerover":
      case "pointerout":
        wr.delete(t.pointerId);
        break;
      case "gotpointercapture":
      case "lostpointercapture":
        Sr.delete(t.pointerId);
    }
  }
  function Er(e, t, a, l, n, r) {
    return e === null || e.nativeEvent !== r ? (e = {
      blockedOn: t,
      domEventName: a,
      eventSystemFlags: l,
      nativeEvent: r,
      targetContainers: [n]
    }, t !== null && (t = Gl(t), t !== null && pf(t)), e) : (e.eventSystemFlags |= l, t = e.targetContainers, n !== null && t.indexOf(n) === -1 && t.push(n), e);
  }
  function vh(e, t, a, l, n) {
    switch (t) {
      case "focusin":
        return al = Er(
          al,
          e,
          t,
          a,
          l,
          n
        ), !0;
      case "dragenter":
        return ll = Er(
          ll,
          e,
          t,
          a,
          l,
          n
        ), !0;
      case "mouseover":
        return nl = Er(
          nl,
          e,
          t,
          a,
          l,
          n
        ), !0;
      case "pointerover":
        var r = n.pointerId;
        return wr.set(
          r,
          Er(
            wr.get(r) || null,
            e,
            t,
            a,
            l,
            n
          )
        ), !0;
      case "gotpointercapture":
        return r = n.pointerId, Sr.set(
          r,
          Er(
            Sr.get(r) || null,
            e,
            t,
            a,
            l,
            n
          )
        ), !0;
    }
    return !1;
  }
  function vf(e) {
    var t = Bl(e.target);
    if (t !== null) {
      var a = p(t);
      if (a !== null) {
        if (t = a.tag, t === 13) {
          if (t = h(a), t !== null) {
            e.blockedOn = t, dl(e.priority, function() {
              hf(a);
            });
            return;
          }
        } else if (t === 31) {
          if (t = v(a), t !== null) {
            e.blockedOn = t, dl(e.priority, function() {
              hf(a);
            });
            return;
          }
        } else if (t === 3 && a.stateNode.current.memoizedState.isDehydrated) {
          e.blockedOn = a.tag === 3 ? a.stateNode.containerInfo : null;
          return;
        }
      }
    }
    e.blockedOn = null;
  }
  function Bi(e) {
    if (e.blockedOn !== null) return !1;
    for (var t = e.targetContainers; 0 < t.length; ) {
      var a = is(e.nativeEvent);
      if (a === null) {
        a = e.nativeEvent;
        var l = new a.constructor(
          a.type,
          a
        );
        io = l, a.target.dispatchEvent(l), io = null;
      } else
        return t = Gl(a), t !== null && pf(t), e.blockedOn = a, !1;
      t.shift();
    }
    return !0;
  }
  function xf(e, t, a) {
    Bi(e) && a.delete(t);
  }
  function xh() {
    cs = !1, al !== null && Bi(al) && (al = null), ll !== null && Bi(ll) && (ll = null), nl !== null && Bi(nl) && (nl = null), wr.forEach(xf), Sr.forEach(xf);
  }
  function Gi(e, t) {
    e.blockedOn === t && (e.blockedOn = null, cs || (cs = !0, i.unstable_scheduleCallback(
      i.unstable_NormalPriority,
      xh
    )));
  }
  var Yi = null;
  function yf(e) {
    Yi !== e && (Yi = e, i.unstable_scheduleCallback(
      i.unstable_NormalPriority,
      function() {
        Yi === e && (Yi = null);
        for (var t = 0; t < e.length; t += 3) {
          var a = e[t], l = e[t + 1], n = e[t + 2];
          if (typeof l != "function") {
            if (os(l || a) === null)
              continue;
            break;
          }
          var r = Gl(a);
          r !== null && (e.splice(t, 3), t -= 3, ic(
            r,
            {
              pending: !0,
              data: n,
              method: a.method,
              action: l
            },
            l,
            n
          ));
        }
      }
    ));
  }
  function Mn(e) {
    function t(y) {
      return Gi(y, e);
    }
    al !== null && Gi(al, e), ll !== null && Gi(ll, e), nl !== null && Gi(nl, e), wr.forEach(t), Sr.forEach(t);
    for (var a = 0; a < rl.length; a++) {
      var l = rl[a];
      l.blockedOn === e && (l.blockedOn = null);
    }
    for (; 0 < rl.length && (a = rl[0], a.blockedOn === null); )
      vf(a), a.blockedOn === null && rl.shift();
    if (a = (e.ownerDocument || e).$$reactFormReplay, a != null)
      for (l = 0; l < a.length; l += 3) {
        var n = a[l], r = a[l + 1], c = n[mt] || null;
        if (typeof r == "function")
          c || yf(a);
        else if (c) {
          var d = null;
          if (r && r.hasAttribute("formAction")) {
            if (n = r, c = r[mt] || null)
              d = c.formAction;
            else if (os(n) !== null) continue;
          } else d = c.action;
          typeof d == "function" ? a[l + 1] = d : (a.splice(l, 3), l -= 3), yf(a);
        }
      }
  }
  function wf() {
    function e(r) {
      r.canIntercept && r.info === "react-transition" && r.intercept({
        handler: function() {
          return new Promise(function(c) {
            return n = c;
          });
        },
        focusReset: "manual",
        scroll: "manual"
      });
    }
    function t() {
      n !== null && (n(), n = null), l || setTimeout(a, 20);
    }
    function a() {
      if (!l && !navigation.transition) {
        var r = navigation.currentEntry;
        r && r.url != null && navigation.navigate(r.url, {
          state: r.getState(),
          info: "react-transition",
          history: "replace"
        });
      }
    }
    if (typeof navigation == "object") {
      var l = !1, n = null;
      return navigation.addEventListener("navigate", e), navigation.addEventListener("navigatesuccess", t), navigation.addEventListener("navigateerror", t), setTimeout(a, 100), function() {
        l = !0, navigation.removeEventListener("navigate", e), navigation.removeEventListener("navigatesuccess", t), navigation.removeEventListener("navigateerror", t), n !== null && (n(), n = null);
      };
    }
  }
  function ss(e) {
    this._internalRoot = e;
  }
  Xi.prototype.render = ss.prototype.render = function(e) {
    var t = this._internalRoot;
    if (t === null) throw Error(o(409));
    var a = t.current, l = Lt();
    df(a, l, e, t, null, null);
  }, Xi.prototype.unmount = ss.prototype.unmount = function() {
    var e = this._internalRoot;
    if (e !== null) {
      this._internalRoot = null;
      var t = e.containerInfo;
      df(e.current, 2, null, e, null, null), Mi(), t[Te] = null;
    }
  };
  function Xi(e) {
    this._internalRoot = e;
  }
  Xi.prototype.unstable_scheduleHydration = function(e) {
    if (e) {
      var t = On();
      e = { blockedOn: null, target: e, priority: t };
      for (var a = 0; a < rl.length && t !== 0 && t < rl[a].priority; a++) ;
      rl.splice(a, 0, e), a === 0 && vf(e);
    }
  };
  var Sf = f.version;
  if (Sf !== "19.2.3")
    throw Error(
      o(
        527,
        Sf,
        "19.2.3"
      )
    );
  q.findDOMNode = function(e) {
    var t = e._reactInternals;
    if (t === void 0)
      throw typeof e.render == "function" ? Error(o(188)) : (e = Object.keys(e).join(","), Error(o(268, e)));
    return e = b(t), e = e !== null ? D(e) : null, e = e === null ? null : e.stateNode, e;
  };
  var yh = {
    bundleType: 0,
    version: "19.2.3",
    rendererPackageName: "react-dom",
    currentDispatcherRef: z,
    reconcilerVersion: "19.2.3"
  };
  if (typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ < "u") {
    var Qi = __REACT_DEVTOOLS_GLOBAL_HOOK__;
    if (!Qi.isDisabled && Qi.supportsFiber)
      try {
        ha = Qi.inject(
          yh
        ), xt = Qi;
      } catch {
      }
  }
  return Mr.createRoot = function(e, t) {
    if (!m(e)) throw Error(o(299));
    var a = !1, l = "", n = H0, r = T0, c = N0;
    return t != null && (t.unstable_strictMode === !0 && (a = !0), t.identifierPrefix !== void 0 && (l = t.identifierPrefix), t.onUncaughtError !== void 0 && (n = t.onUncaughtError), t.onCaughtError !== void 0 && (r = t.onCaughtError), t.onRecoverableError !== void 0 && (c = t.onRecoverableError)), t = uf(
      e,
      1,
      !1,
      null,
      null,
      a,
      l,
      null,
      n,
      r,
      c,
      wf
    ), e[Te] = t.current, Yc(e), new ss(t);
  }, Mr.hydrateRoot = function(e, t, a) {
    if (!m(e)) throw Error(o(299));
    var l = !1, n = "", r = H0, c = T0, d = N0, y = null;
    return a != null && (a.unstable_strictMode === !0 && (l = !0), a.identifierPrefix !== void 0 && (n = a.identifierPrefix), a.onUncaughtError !== void 0 && (r = a.onUncaughtError), a.onCaughtError !== void 0 && (c = a.onCaughtError), a.onRecoverableError !== void 0 && (d = a.onRecoverableError), a.formState !== void 0 && (y = a.formState)), t = uf(
      e,
      1,
      !0,
      t,
      a ?? null,
      l,
      n,
      y,
      r,
      c,
      d,
      wf
    ), t.context = ff(null), a = t.current, l = Lt(), l = ul(l), n = Ya(l), n.callback = null, Xa(a, n, l), a = l, t.current.lanes = a, kt(t, a), da(t), e[Te] = t.current, Yc(e), new Xi(t);
  }, Mr.version = "19.2.3", Mr;
}
var _f;
function Th() {
  if (_f) return ds.exports;
  _f = 1;
  function i() {
    if (!(typeof __REACT_DEVTOOLS_GLOBAL_HOOK__ > "u" || typeof __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE != "function"))
      try {
        __REACT_DEVTOOLS_GLOBAL_HOOK__.checkDCE(i);
      } catch (f) {
        console.error(f);
      }
  }
  return i(), ds.exports = Hh(), ds.exports;
}
var Nh = Th();
const Ch = /* @__PURE__ */ new Map([
  [
    "bold",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M228,48V96a12,12,0,0,1-12,12H168a12,12,0,0,1,0-24h19l-7.8-7.8a75.55,75.55,0,0,0-53.32-22.26h-.43A75.49,75.49,0,0,0,72.39,75.57,12,12,0,1,1,55.61,58.41a99.38,99.38,0,0,1,69.87-28.47H126A99.42,99.42,0,0,1,196.2,59.23L204,67V48a12,12,0,0,1,24,0ZM183.61,180.43a75.49,75.49,0,0,1-53.09,21.63h-.43A75.55,75.55,0,0,1,76.77,179.8L69,172H88a12,12,0,0,0,0-24H40a12,12,0,0,0-12,12v48a12,12,0,0,0,24,0V189l7.8,7.8A99.42,99.42,0,0,0,130,226.06h.56a99.38,99.38,0,0,0,69.87-28.47,12,12,0,0,0-16.78-17.16Z" }))
  ],
  [
    "duotone",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M216,128a88,88,0,1,1-88-88A88,88,0,0,1,216,128Z", opacity: "0.2" }), /* @__PURE__ */ s.createElement("path", { d: "M224,48V96a8,8,0,0,1-8,8H168a8,8,0,0,1,0-16h28.69L182.06,73.37a79.56,79.56,0,0,0-56.13-23.43h-.45A79.52,79.52,0,0,0,69.59,72.71,8,8,0,0,1,58.41,61.27a96,96,0,0,1,135,.79L208,76.69V48a8,8,0,0,1,16,0ZM186.41,183.29a80,80,0,0,1-112.47-.66L59.31,168H88a8,8,0,0,0,0-16H40a8,8,0,0,0-8,8v48a8,8,0,0,0,16,0V179.31l14.63,14.63A95.43,95.43,0,0,0,130,222.06h.53a95.36,95.36,0,0,0,67.07-27.33,8,8,0,0,0-11.18-11.44Z" }))
  ],
  [
    "fill",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M224,48V96a8,8,0,0,1-8,8H168a8,8,0,0,1-5.66-13.66L180.65,72a79.48,79.48,0,0,0-54.72-22.09h-.45A79.52,79.52,0,0,0,69.59,72.71,8,8,0,0,1,58.41,61.27,96,96,0,0,1,192,60.7l18.36-18.36A8,8,0,0,1,224,48ZM186.41,183.29A80,80,0,0,1,75.35,184l18.31-18.31A8,8,0,0,0,88,152H40a8,8,0,0,0-8,8v48a8,8,0,0,0,13.66,5.66L64,195.3a95.42,95.42,0,0,0,66,26.76h.53a95.36,95.36,0,0,0,67.07-27.33,8,8,0,0,0-11.18-11.44Z" }))
  ],
  [
    "light",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M222,48V96a6,6,0,0,1-6,6H168a6,6,0,0,1,0-12h33.52L183.47,72a81.51,81.51,0,0,0-57.53-24h-.46A81.5,81.5,0,0,0,68.19,71.28a6,6,0,1,1-8.38-8.58,93.38,93.38,0,0,1,65.67-26.76H126a93.45,93.45,0,0,1,66,27.53l18,18V48a6,6,0,0,1,12,0ZM187.81,184.72a81.5,81.5,0,0,1-57.29,23.34h-.46a81.51,81.51,0,0,1-57.53-24L54.48,166H88a6,6,0,0,0,0-12H40a6,6,0,0,0-6,6v48a6,6,0,0,0,12,0V174.48l18,18.05a93.45,93.45,0,0,0,66,27.53h.52a93.38,93.38,0,0,0,65.67-26.76,6,6,0,1,0-8.38-8.58Z" }))
  ],
  [
    "regular",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M224,48V96a8,8,0,0,1-8,8H168a8,8,0,0,1,0-16h28.69L182.06,73.37a79.56,79.56,0,0,0-56.13-23.43h-.45A79.52,79.52,0,0,0,69.59,72.71,8,8,0,0,1,58.41,61.27a96,96,0,0,1,135,.79L208,76.69V48a8,8,0,0,1,16,0ZM186.41,183.29a80,80,0,0,1-112.47-.66L59.31,168H88a8,8,0,0,0,0-16H40a8,8,0,0,0-8,8v48a8,8,0,0,0,16,0V179.31l14.63,14.63A95.43,95.43,0,0,0,130,222.06h.53a95.36,95.36,0,0,0,67.07-27.33,8,8,0,0,0-11.18-11.44Z" }))
  ],
  [
    "thin",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M220,48V96a4,4,0,0,1-4,4H168a4,4,0,0,1,0-8h38.34L184.89,70.54A84,84,0,0,0,66.8,69.85a4,4,0,1,1-5.6-5.72,92,92,0,0,1,129.34.76L212,86.34V48a4,4,0,0,1,8,0ZM189.2,186.15a83.44,83.44,0,0,1-58.68,23.91h-.47a83.52,83.52,0,0,1-58.94-24.6L49.66,164H88a4,4,0,0,0,0-8H40a4,4,0,0,0-4,4v48a4,4,0,0,0,8,0V169.66l21.46,21.45A91.43,91.43,0,0,0,130,218.06h.51a91.45,91.45,0,0,0,64.28-26.19,4,4,0,1,0-5.6-5.72Z" }))
  ]
]), _h = /* @__PURE__ */ new Map([
  [
    "bold",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M216.49,104.49l-80,80a12,12,0,0,1-17,0l-80-80a12,12,0,0,1,17-17L128,159l71.51-71.52a12,12,0,0,1,17,17Z" }))
  ],
  [
    "duotone",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M208,96l-80,80L48,96Z", opacity: "0.2" }), /* @__PURE__ */ s.createElement("path", { d: "M215.39,92.94A8,8,0,0,0,208,88H48a8,8,0,0,0-5.66,13.66l80,80a8,8,0,0,0,11.32,0l80-80A8,8,0,0,0,215.39,92.94ZM128,164.69,67.31,104H188.69Z" }))
  ],
  [
    "fill",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M213.66,101.66l-80,80a8,8,0,0,1-11.32,0l-80-80A8,8,0,0,1,48,88H208a8,8,0,0,1,5.66,13.66Z" }))
  ],
  [
    "light",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M212.24,100.24l-80,80a6,6,0,0,1-8.48,0l-80-80a6,6,0,0,1,8.48-8.48L128,167.51l75.76-75.75a6,6,0,0,1,8.48,8.48Z" }))
  ],
  [
    "regular",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M213.66,101.66l-80,80a8,8,0,0,1-11.32,0l-80-80A8,8,0,0,1,53.66,90.34L128,164.69l74.34-74.35a8,8,0,0,1,11.32,11.32Z" }))
  ],
  [
    "thin",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M210.83,98.83l-80,80a4,4,0,0,1-5.66,0l-80-80a4,4,0,0,1,5.66-5.66L128,170.34l77.17-77.17a4,4,0,1,1,5.66,5.66Z" }))
  ]
]), jh = /* @__PURE__ */ new Map([
  [
    "bold",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M232.49,80.49l-128,128a12,12,0,0,1-17,0l-56-56a12,12,0,1,1,17-17L96,183,215.51,63.51a12,12,0,0,1,17,17Z" }))
  ],
  [
    "duotone",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement(
      "path",
      {
        d: "M232,56V200a16,16,0,0,1-16,16H40a16,16,0,0,1-16-16V56A16,16,0,0,1,40,40H216A16,16,0,0,1,232,56Z",
        opacity: "0.2"
      }
    ), /* @__PURE__ */ s.createElement("path", { d: "M205.66,85.66l-96,96a8,8,0,0,1-11.32,0l-40-40a8,8,0,0,1,11.32-11.32L104,164.69l90.34-90.35a8,8,0,0,1,11.32,11.32Z" }))
  ],
  [
    "fill",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M216,40H40A16,16,0,0,0,24,56V200a16,16,0,0,0,16,16H216a16,16,0,0,0,16-16V56A16,16,0,0,0,216,40ZM205.66,85.66l-96,96a8,8,0,0,1-11.32,0l-40-40a8,8,0,0,1,11.32-11.32L104,164.69l90.34-90.35a8,8,0,0,1,11.32,11.32Z" }))
  ],
  [
    "light",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M228.24,76.24l-128,128a6,6,0,0,1-8.48,0l-56-56a6,6,0,0,1,8.48-8.48L96,191.51,219.76,67.76a6,6,0,0,1,8.48,8.48Z" }))
  ],
  [
    "regular",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M229.66,77.66l-128,128a8,8,0,0,1-11.32,0l-56-56a8,8,0,0,1,11.32-11.32L96,188.69,218.34,66.34a8,8,0,0,1,11.32,11.32Z" }))
  ],
  [
    "thin",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M226.83,74.83l-128,128a4,4,0,0,1-5.66,0l-56-56a4,4,0,0,1,5.66-5.66L96,194.34,221.17,69.17a4,4,0,1,1,5.66,5.66Z" }))
  ]
]), Oh = /* @__PURE__ */ new Map([
  [
    "bold",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M200,28H165.47a51.88,51.88,0,0,0-74.94,0H56A20,20,0,0,0,36,48V216a20,20,0,0,0,20,20H200a20,20,0,0,0,20-20V48A20,20,0,0,0,200,28ZM155.71,60H100.29a28,28,0,0,1,55.42,0ZM196,212H60V52H77.41A52.13,52.13,0,0,0,76,64v8A12,12,0,0,0,88,84h80a12,12,0,0,0,12-12V64a52.13,52.13,0,0,0-1.41-12H196Z" }))
  ],
  [
    "duotone",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement(
      "path",
      {
        d: "M208,48V216a8,8,0,0,1-8,8H56a8,8,0,0,1-8-8V48a8,8,0,0,1,8-8H96a39.83,39.83,0,0,0-8,24v8h80V64a39.83,39.83,0,0,0-8-24h40A8,8,0,0,1,208,48Z",
        opacity: "0.2"
      }
    ), /* @__PURE__ */ s.createElement("path", { d: "M200,32H163.74a47.92,47.92,0,0,0-71.48,0H56A16,16,0,0,0,40,48V216a16,16,0,0,0,16,16H200a16,16,0,0,0,16-16V48A16,16,0,0,0,200,32Zm-72,0a32,32,0,0,1,32,32H96A32,32,0,0,1,128,32Zm72,184H56V48H82.75A47.93,47.93,0,0,0,80,64v8a8,8,0,0,0,8,8h80a8,8,0,0,0,8-8V64a47.93,47.93,0,0,0-2.75-16H200Z" }))
  ],
  [
    "fill",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M200,32H163.74a47.92,47.92,0,0,0-71.48,0H56A16,16,0,0,0,40,48V216a16,16,0,0,0,16,16H200a16,16,0,0,0,16-16V48A16,16,0,0,0,200,32Zm-72,0a32,32,0,0,1,32,32H96A32,32,0,0,1,128,32Z" }))
  ],
  [
    "light",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M200,34H162.83a45.91,45.91,0,0,0-69.66,0H56A14,14,0,0,0,42,48V216a14,14,0,0,0,14,14H200a14,14,0,0,0,14-14V48A14,14,0,0,0,200,34Zm-72-4a34,34,0,0,1,34,34v2H94V64A34,34,0,0,1,128,30Zm74,186a2,2,0,0,1-2,2H56a2,2,0,0,1-2-2V48a2,2,0,0,1,2-2H85.67A45.77,45.77,0,0,0,82,64v8a6,6,0,0,0,6,6h80a6,6,0,0,0,6-6V64a45.77,45.77,0,0,0-3.67-18H200a2,2,0,0,1,2,2Z" }))
  ],
  [
    "regular",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M200,32H163.74a47.92,47.92,0,0,0-71.48,0H56A16,16,0,0,0,40,48V216a16,16,0,0,0,16,16H200a16,16,0,0,0,16-16V48A16,16,0,0,0,200,32Zm-72,0a32,32,0,0,1,32,32H96A32,32,0,0,1,128,32Zm72,184H56V48H82.75A47.93,47.93,0,0,0,80,64v8a8,8,0,0,0,8,8h80a8,8,0,0,0,8-8V64a47.93,47.93,0,0,0-2.75-16H200Z" }))
  ],
  [
    "thin",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M200,36H161.92a44,44,0,0,0-67.84,0H56A12,12,0,0,0,44,48V216a12,12,0,0,0,12,12H200a12,12,0,0,0,12-12V48A12,12,0,0,0,200,36Zm-72-8a36,36,0,0,1,36,36v4H92V64A36,36,0,0,1,128,28Zm76,188a4,4,0,0,1-4,4H56a4,4,0,0,1-4-4V48a4,4,0,0,1,4-4H88.83A43.71,43.71,0,0,0,84,64v8a4,4,0,0,0,4,4h80a4,4,0,0,0,4-4V64a43.71,43.71,0,0,0-4.83-20H200a4,4,0,0,1,4,4Z" }))
  ]
]), Zh = /* @__PURE__ */ new Map([
  [
    "bold",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M108,60A16,16,0,1,1,92,44,16,16,0,0,1,108,60Zm56,16a16,16,0,1,0-16-16A16,16,0,0,0,164,76ZM92,112a16,16,0,1,0,16,16A16,16,0,0,0,92,112Zm72,0a16,16,0,1,0,16,16A16,16,0,0,0,164,112ZM92,180a16,16,0,1,0,16,16A16,16,0,0,0,92,180Zm72,0a16,16,0,1,0,16,16A16,16,0,0,0,164,180Z" }))
  ],
  [
    "duotone",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement(
      "path",
      {
        d: "M208,32V224a16,16,0,0,1-16,16H64a16,16,0,0,1-16-16V32A16,16,0,0,1,64,16H192A16,16,0,0,1,208,32Z",
        opacity: "0.2"
      }
    ), /* @__PURE__ */ s.createElement("path", { d: "M104,60A12,12,0,1,1,92,48,12,12,0,0,1,104,60Zm60,12a12,12,0,1,0-12-12A12,12,0,0,0,164,72ZM92,116a12,12,0,1,0,12,12A12,12,0,0,0,92,116Zm72,0a12,12,0,1,0,12,12A12,12,0,0,0,164,116ZM92,184a12,12,0,1,0,12,12A12,12,0,0,0,92,184Zm72,0a12,12,0,1,0,12,12A12,12,0,0,0,164,184Z" }))
  ],
  [
    "fill",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M192,16H64A16,16,0,0,0,48,32V224a16,16,0,0,0,16,16H192a16,16,0,0,0,16-16V32A16,16,0,0,0,192,16ZM100,200a12,12,0,1,1,12-12A12,12,0,0,1,100,200Zm0-60a12,12,0,1,1,12-12A12,12,0,0,1,100,140Zm0-60a12,12,0,1,1,12-12A12,12,0,0,1,100,80Zm56,120a12,12,0,1,1,12-12A12,12,0,0,1,156,200Zm0-60a12,12,0,1,1,12-12A12,12,0,0,1,156,140Zm0-60a12,12,0,1,1,12-12A12,12,0,0,1,156,80Z" }))
  ],
  [
    "light",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M102,60A10,10,0,1,1,92,50,10,10,0,0,1,102,60Zm62,10a10,10,0,1,0-10-10A10,10,0,0,0,164,70ZM92,118a10,10,0,1,0,10,10A10,10,0,0,0,92,118Zm72,0a10,10,0,1,0,10,10A10,10,0,0,0,164,118ZM92,186a10,10,0,1,0,10,10A10,10,0,0,0,92,186Zm72,0a10,10,0,1,0,10,10A10,10,0,0,0,164,186Z" }))
  ],
  [
    "regular",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M104,60A12,12,0,1,1,92,48,12,12,0,0,1,104,60Zm60,12a12,12,0,1,0-12-12A12,12,0,0,0,164,72ZM92,116a12,12,0,1,0,12,12A12,12,0,0,0,92,116Zm72,0a12,12,0,1,0,12,12A12,12,0,0,0,164,116ZM92,184a12,12,0,1,0,12,12A12,12,0,0,0,92,184Zm72,0a12,12,0,1,0,12,12A12,12,0,0,0,164,184Z" }))
  ],
  [
    "thin",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M100,60a8,8,0,1,1-8-8A8,8,0,0,1,100,60Zm64,8a8,8,0,1,0-8-8A8,8,0,0,0,164,68ZM92,120a8,8,0,1,0,8,8A8,8,0,0,0,92,120Zm72,0a8,8,0,1,0,8,8A8,8,0,0,0,164,120ZM92,188a8,8,0,1,0,8,8A8,8,0,0,0,92,188Zm72,0a8,8,0,1,0,8,8A8,8,0,0,0,164,188Z" }))
  ]
]), Dh = /* @__PURE__ */ new Map([
  [
    "bold",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M251,123.13c-.37-.81-9.13-20.26-28.48-39.61C196.63,57.67,164,44,128,44S59.37,57.67,33.51,83.52C14.16,102.87,5.4,122.32,5,123.13a12.08,12.08,0,0,0,0,9.75c.37.82,9.13,20.26,28.49,39.61C59.37,198.34,92,212,128,212s68.63-13.66,94.48-39.51c19.36-19.35,28.12-38.79,28.49-39.61A12.08,12.08,0,0,0,251,123.13Zm-46.06,33C183.47,177.27,157.59,188,128,188s-55.47-10.73-76.91-31.88A130.36,130.36,0,0,1,29.52,128,130.45,130.45,0,0,1,51.09,99.89C72.54,78.73,98.41,68,128,68s55.46,10.73,76.91,31.89A130.36,130.36,0,0,1,226.48,128,130.45,130.45,0,0,1,204.91,156.12ZM128,84a44,44,0,1,0,44,44A44.05,44.05,0,0,0,128,84Zm0,64a20,20,0,1,1,20-20A20,20,0,0,1,128,148Z" }))
  ],
  [
    "duotone",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement(
      "path",
      {
        d: "M128,56C48,56,16,128,16,128s32,72,112,72,112-72,112-72S208,56,128,56Zm0,112a40,40,0,1,1,40-40A40,40,0,0,1,128,168Z",
        opacity: "0.2"
      }
    ), /* @__PURE__ */ s.createElement("path", { d: "M247.31,124.76c-.35-.79-8.82-19.58-27.65-38.41C194.57,61.26,162.88,48,128,48S61.43,61.26,36.34,86.35C17.51,105.18,9,124,8.69,124.76a8,8,0,0,0,0,6.5c.35.79,8.82,19.57,27.65,38.4C61.43,194.74,93.12,208,128,208s66.57-13.26,91.66-38.34c18.83-18.83,27.3-37.61,27.65-38.4A8,8,0,0,0,247.31,124.76ZM128,192c-30.78,0-57.67-11.19-79.93-33.25A133.47,133.47,0,0,1,25,128,133.33,133.33,0,0,1,48.07,97.25C70.33,75.19,97.22,64,128,64s57.67,11.19,79.93,33.25A133.46,133.46,0,0,1,231.05,128C223.84,141.46,192.43,192,128,192Zm0-112a48,48,0,1,0,48,48A48.05,48.05,0,0,0,128,80Zm0,80a32,32,0,1,1,32-32A32,32,0,0,1,128,160Z" }))
  ],
  [
    "fill",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M247.31,124.76c-.35-.79-8.82-19.58-27.65-38.41C194.57,61.26,162.88,48,128,48S61.43,61.26,36.34,86.35C17.51,105.18,9,124,8.69,124.76a8,8,0,0,0,0,6.5c.35.79,8.82,19.57,27.65,38.4C61.43,194.74,93.12,208,128,208s66.57-13.26,91.66-38.34c18.83-18.83,27.3-37.61,27.65-38.4A8,8,0,0,0,247.31,124.76ZM128,168a40,40,0,1,1,40-40A40,40,0,0,1,128,168Z" }))
  ],
  [
    "light",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M245.48,125.57c-.34-.78-8.66-19.23-27.24-37.81C201,70.54,171.38,50,128,50S55,70.54,37.76,87.76c-18.58,18.58-26.9,37-27.24,37.81a6,6,0,0,0,0,4.88c.34.77,8.66,19.22,27.24,37.8C55,185.47,84.62,206,128,206s73-20.53,90.24-37.75c18.58-18.58,26.9-37,27.24-37.8A6,6,0,0,0,245.48,125.57ZM128,194c-31.38,0-58.78-11.42-81.45-33.93A134.77,134.77,0,0,1,22.69,128,134.56,134.56,0,0,1,46.55,95.94C69.22,73.42,96.62,62,128,62s58.78,11.42,81.45,33.94A134.56,134.56,0,0,1,233.31,128C226.94,140.21,195,194,128,194Zm0-112a46,46,0,1,0,46,46A46.06,46.06,0,0,0,128,82Zm0,80a34,34,0,1,1,34-34A34,34,0,0,1,128,162Z" }))
  ],
  [
    "regular",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M247.31,124.76c-.35-.79-8.82-19.58-27.65-38.41C194.57,61.26,162.88,48,128,48S61.43,61.26,36.34,86.35C17.51,105.18,9,124,8.69,124.76a8,8,0,0,0,0,6.5c.35.79,8.82,19.57,27.65,38.4C61.43,194.74,93.12,208,128,208s66.57-13.26,91.66-38.34c18.83-18.83,27.3-37.61,27.65-38.4A8,8,0,0,0,247.31,124.76ZM128,192c-30.78,0-57.67-11.19-79.93-33.25A133.47,133.47,0,0,1,25,128,133.33,133.33,0,0,1,48.07,97.25C70.33,75.19,97.22,64,128,64s57.67,11.19,79.93,33.25A133.46,133.46,0,0,1,231.05,128C223.84,141.46,192.43,192,128,192Zm0-112a48,48,0,1,0,48,48A48.05,48.05,0,0,0,128,80Zm0,80a32,32,0,1,1,32-32A32,32,0,0,1,128,160Z" }))
  ],
  [
    "thin",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M243.66,126.38c-.34-.76-8.52-18.89-26.83-37.2C199.87,72.22,170.7,52,128,52S56.13,72.22,39.17,89.18c-18.31,18.31-26.49,36.44-26.83,37.2a4.08,4.08,0,0,0,0,3.25c.34.77,8.52,18.89,26.83,37.2,17,17,46.14,37.17,88.83,37.17s71.87-20.21,88.83-37.17c18.31-18.31,26.49-36.43,26.83-37.2A4.08,4.08,0,0,0,243.66,126.38Zm-32.7,35c-23.07,23-51,34.62-83,34.62s-59.89-11.65-83-34.62A135.71,135.71,0,0,1,20.44,128,135.69,135.69,0,0,1,45,94.62C68.11,71.65,96,60,128,60s59.89,11.65,83,34.62A135.79,135.79,0,0,1,235.56,128,135.71,135.71,0,0,1,211,161.38ZM128,84a44,44,0,1,0,44,44A44.05,44.05,0,0,0,128,84Zm0,80a36,36,0,1,1,36-36A36,36,0,0,1,128,164Z" }))
  ]
]), Rh = /* @__PURE__ */ new Map([
  [
    "bold",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M176.69,48.72a225,225,0,0,0-42.52-35,12,12,0,0,0-12.34,0,225,225,0,0,0-42.52,35C51,78.47,36,111.42,36,144a92,92,0,0,0,184,0C220,111.42,205,78.47,176.69,48.72ZM100,184c0-13.33,5.53-26.26,16.45-38.45A93,93,0,0,1,128,134.72a93,93,0,0,1,11.55,10.83C150.47,157.74,156,170.67,156,184a28,28,0,0,1-56,0Zm79.84,3.94c.09-1.3.16-2.61.16-3.94,0-46.26-44-73.17-45.83-74.29a12,12,0,0,0-12.34,0C120,110.83,76,137.74,76,184c0,1.33.07,2.64.16,3.94A67.68,67.68,0,0,1,60,144c0-26.52,12.21-52.86,36.28-78.3A213.07,213.07,0,0,1,128,38.39C145.82,50.86,196,90.71,196,144A67.68,67.68,0,0,1,179.84,187.94Z" }))
  ],
  [
    "duotone",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement(
      "path",
      {
        d: "M208,144A80,80,0,0,1,130.06,224,40,40,0,0,0,168,184c0-40-40-64-40-64s-40,24-40,64A40,40,0,0,0,125.94,224,80,80,0,0,1,48,144c0-72,80-120,80-120S208,72,208,144Z",
        opacity: "0.2"
      }
    ), /* @__PURE__ */ s.createElement("path", { d: "M173.79,51.48a221.25,221.25,0,0,0-41.67-34.34,8,8,0,0,0-8.24,0A221.25,221.25,0,0,0,82.21,51.48C54.59,80.48,40,112.47,40,144a88,88,0,0,0,176,0C216,112.47,201.41,80.48,173.79,51.48ZM96,184c0-27.67,22.53-47.28,32-54.3,9.48,7,32,26.63,32,54.3a32,32,0,0,1-64,0Zm77.27,15.93A47.8,47.8,0,0,0,176,184c0-44-42.09-69.79-43.88-70.86a8,8,0,0,0-8.24,0C122.09,114.21,80,140,80,184a47.8,47.8,0,0,0,2.73,15.93A71.88,71.88,0,0,1,56,144c0-34.41,20.4-63.15,37.52-81.19A216.21,216.21,0,0,1,128,33.54a215.77,215.77,0,0,1,34.48,29.27C193.49,95.5,200,125,200,144A71.88,71.88,0,0,1,173.27,199.93Z" }))
  ],
  [
    "fill",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M173.79,51.48a221.25,221.25,0,0,0-41.67-34.34,8,8,0,0,0-8.24,0A221.25,221.25,0,0,0,82.21,51.48C54.59,80.48,40,112.47,40,144a88,88,0,0,0,176,0C216,112.47,201.41,80.48,173.79,51.48ZM96,184c0-27.67,22.53-47.28,32-54.3,9.48,7,32,26.63,32,54.3a32,32,0,0,1-64,0Z" }))
  ],
  [
    "light",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M172.34,52.86a218.34,218.34,0,0,0-41.25-34,6,6,0,0,0-6.18,0,218.34,218.34,0,0,0-41.25,34C56.4,81.48,42,113,42,144a86,86,0,0,0,172,0C214,113,199.6,81.48,172.34,52.86ZM94,184c0-29.8,25.11-50.41,34-56.78,8.91,6.35,34,26.87,34,56.78a34.05,34.05,0,0,1-32.25,34c-.59,0-1.16,0-1.75,0s-1.16,0-1.75,0A34.05,34.05,0,0,1,94,184Zm74.42,21.94A45.68,45.68,0,0,0,174,184c0-42.9-41.16-68.09-42.91-69.14a6,6,0,0,0-6.18,0C123.16,115.91,82,141.1,82,184a45.68,45.68,0,0,0,5.58,21.94A74,74,0,0,1,54,144c0-59.83,59.62-103.26,74-112.86,14.39,9.6,74,53,74,112.86A74,74,0,0,1,168.42,205.94Z" }))
  ],
  [
    "regular",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M173.79,51.48a221.25,221.25,0,0,0-41.67-34.34,8,8,0,0,0-8.24,0A221.25,221.25,0,0,0,82.21,51.48C54.59,80.48,40,112.47,40,144a88,88,0,0,0,176,0C216,112.47,201.41,80.48,173.79,51.48ZM96,184c0-27.67,22.53-47.28,32-54.3,9.48,7,32,26.63,32,54.3a32,32,0,0,1-64,0Zm77.27,15.93A47.8,47.8,0,0,0,176,184c0-44-42.09-69.79-43.88-70.86a8,8,0,0,0-8.24,0C122.09,114.21,80,140,80,184a47.8,47.8,0,0,0,2.73,15.93A71.88,71.88,0,0,1,56,144c0-34.41,20.4-63.15,37.52-81.19A216.21,216.21,0,0,1,128,33.54a215.77,215.77,0,0,1,34.48,29.27C193.49,95.5,200,125,200,144A71.88,71.88,0,0,1,173.27,199.93Z" }))
  ],
  [
    "thin",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M170.9,54.24a216.79,216.79,0,0,0-40.84-33.67,4,4,0,0,0-4.12,0A216.79,216.79,0,0,0,85.1,54.24C58.21,82.48,44,113.51,44,144a84,84,0,0,0,168,0C212,113.51,197.79,82.48,170.9,54.24ZM92,184c0-32.11,28.07-53.75,36-59.21,7.93,5.47,36,27.1,36,59.21a36,36,0,0,1-72,0Zm69.94,28A43.82,43.82,0,0,0,172,184c0-41.78-40.23-66.4-41.94-67.43a4,4,0,0,0-4.12,0C124.23,117.6,84,142.22,84,184a43.82,43.82,0,0,0,10.06,28A76.07,76.07,0,0,1,52,144c0-62.48,63.64-107.17,76-115.26,12.36,8.09,76,52.78,76,115.26A76.07,76.07,0,0,1,161.94,212Z" }))
  ]
]), Vh = /* @__PURE__ */ new Map([
  [
    "bold",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M234.29,47.91A20,20,0,0,0,216,36H40A20,20,0,0,0,25.2,69.45l.12.14L92,140.75V216a20,20,0,0,0,31.1,16.64l32-21.33A20,20,0,0,0,164,194.66V140.75l66.67-71.16.12-.14A20,20,0,0,0,234.29,47.91Zm-91,79.89A12,12,0,0,0,140,136v56.52l-24,16V136a12,12,0,0,0-3.25-8.2L49.23,60H206.77Z" }))
  ],
  [
    "duotone",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement(
      "path",
      {
        d: "M221.9,61.38,152,136v58.65a8,8,0,0,1-3.56,6.66l-32,21.33A8,8,0,0,1,104,216V136L34.1,61.38A8,8,0,0,1,40,48H216A8,8,0,0,1,221.9,61.38Z",
        opacity: "0.2"
      }
    ), /* @__PURE__ */ s.createElement("path", { d: "M230.6,49.53A15.81,15.81,0,0,0,216,40H40A16,16,0,0,0,28.19,66.76l.08.09L96,139.17V216a16,16,0,0,0,24.87,13.32l32-21.34A16,16,0,0,0,160,194.66V139.17l67.74-72.32.08-.09A15.8,15.8,0,0,0,230.6,49.53ZM40,56h0Zm106.18,74.58A8,8,0,0,0,144,136v58.66L112,216V136a8,8,0,0,0-2.16-5.47L40,56H216Z" }))
  ],
  [
    "fill",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M227.81,66.76l-.08.09L160,139.17v55.49A16,16,0,0,1,152.87,208l-32,21.34A16,16,0,0,1,96,216V139.17L28.27,66.85l-.08-.09A16,16,0,0,1,40,40H216a16,16,0,0,1,11.84,26.76Z" }))
  ],
  [
    "light",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M228.77,50.34A13.8,13.8,0,0,0,216,42H40A14,14,0,0,0,29.67,65.42l.06.07L98,138.38V216a14,14,0,0,0,21.77,11.64l32-21.33A14,14,0,0,0,158,194.66V138.38l68.33-73A13.82,13.82,0,0,0,228.77,50.34Zm-11.26,6.94L147.62,131.9A6,6,0,0,0,146,136v58.66a2,2,0,0,1-.89,1.67l-32,21.33A2,2,0,0,1,110,216V136a6,6,0,0,0-1.62-4.1L38.53,57.32A2,2,0,0,1,40,54H216a1.9,1.9,0,0,1,1.83,1.19A1.86,1.86,0,0,1,217.51,57.28Z" }))
  ],
  [
    "regular",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M230.6,49.53A15.81,15.81,0,0,0,216,40H40A16,16,0,0,0,28.19,66.76l.08.09L96,139.17V216a16,16,0,0,0,24.87,13.32l32-21.34A16,16,0,0,0,160,194.66V139.17l67.74-72.32.08-.09A15.8,15.8,0,0,0,230.6,49.53ZM40,56h0Zm106.18,74.58A8,8,0,0,0,144,136v58.66L112,216V136a8,8,0,0,0-2.16-5.47L40,56H216Z" }))
  ],
  [
    "thin",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M227,51.15A11.85,11.85,0,0,0,216,44H40a12,12,0,0,0-8.88,20.07l.05.05L100,137.59V216a12,12,0,0,0,18.66,10l32-21.33a12,12,0,0,0,5.35-10V137.59l68.86-73.52A11.85,11.85,0,0,0,227,51.15Zm-8,7.5-69.9,74.62A4,4,0,0,0,148,136v58.65a4,4,0,0,1-1.78,3.33l-32,21.33A4,4,0,0,1,108,216V136a4,4,0,0,0-1.08-2.74L37.05,58.67A4,4,0,0,1,40,52H216a4,4,0,0,1,3,6.65Z" }))
  ]
]), Lh = /* @__PURE__ */ new Map([
  [
    "bold",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M144,96a16,16,0,1,1,16,16A16,16,0,0,1,144,96Zm92-40V200a20,20,0,0,1-20,20H40a20,20,0,0,1-20-20V56A20,20,0,0,1,40,36H216A20,20,0,0,1,236,56ZM44,60v79.72l33.86-33.86a20,20,0,0,1,28.28,0L147.31,147l17.18-17.17a20,20,0,0,1,28.28,0L212,149.09V60Zm0,136H162.34L92,125.66l-48,48Zm168,0V183l-33.37-33.37L164.28,164l32,32Z" }))
  ],
  [
    "duotone",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement(
      "path",
      {
        d: "M224,56V178.06l-39.72-39.72a8,8,0,0,0-11.31,0L147.31,164,97.66,114.34a8,8,0,0,0-11.32,0L32,168.69V56a8,8,0,0,1,8-8H216A8,8,0,0,1,224,56Z",
        opacity: "0.2"
      }
    ), /* @__PURE__ */ s.createElement("path", { d: "M216,40H40A16,16,0,0,0,24,56V200a16,16,0,0,0,16,16H216a16,16,0,0,0,16-16V56A16,16,0,0,0,216,40Zm0,16V158.75l-26.07-26.06a16,16,0,0,0-22.63,0l-20,20-44-44a16,16,0,0,0-22.62,0L40,149.37V56ZM40,172l52-52,80,80H40Zm176,28H194.63l-36-36,20-20L216,181.38V200ZM144,100a12,12,0,1,1,12,12A12,12,0,0,1,144,100Z" }))
  ],
  [
    "fill",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M216,40H40A16,16,0,0,0,24,56V200a16,16,0,0,0,16,16H216a16,16,0,0,0,16-16V56A16,16,0,0,0,216,40ZM156,88a12,12,0,1,1-12,12A12,12,0,0,1,156,88Zm60,112H40V160.69l46.34-46.35a8,8,0,0,1,11.32,0h0L165,181.66a8,8,0,0,0,11.32-11.32l-17.66-17.65L173,138.34a8,8,0,0,1,11.31,0L216,170.07V200Z" }))
  ],
  [
    "light",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M216,42H40A14,14,0,0,0,26,56V200a14,14,0,0,0,14,14H216a14,14,0,0,0,14-14V56A14,14,0,0,0,216,42ZM40,54H216a2,2,0,0,1,2,2V163.57L188.53,134.1a14,14,0,0,0-19.8,0l-21.42,21.42L101.9,110.1a14,14,0,0,0-19.8,0L38,154.2V56A2,2,0,0,1,40,54ZM38,200V171.17l52.58-52.58a2,2,0,0,1,2.84,0L176.83,202H40A2,2,0,0,1,38,200Zm178,2H193.8l-38-38,21.41-21.42a2,2,0,0,1,2.83,0l38,38V200A2,2,0,0,1,216,202ZM146,100a10,10,0,1,1,10,10A10,10,0,0,1,146,100Z" }))
  ],
  [
    "regular",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M216,40H40A16,16,0,0,0,24,56V200a16,16,0,0,0,16,16H216a16,16,0,0,0,16-16V56A16,16,0,0,0,216,40Zm0,16V158.75l-26.07-26.06a16,16,0,0,0-22.63,0l-20,20-44-44a16,16,0,0,0-22.62,0L40,149.37V56ZM40,172l52-52,80,80H40Zm176,28H194.63l-36-36,20-20L216,181.38V200ZM144,100a12,12,0,1,1,12,12A12,12,0,0,1,144,100Z" }))
  ],
  [
    "thin",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M216,44H40A12,12,0,0,0,28,56V200a12,12,0,0,0,12,12H216a12,12,0,0,0,12-12V56A12,12,0,0,0,216,44ZM40,52H216a4,4,0,0,1,4,4V168.4l-32.89-32.89a12,12,0,0,0-17,0l-22.83,22.83-46.82-46.83a12,12,0,0,0-17,0L36,159V56A4,4,0,0,1,40,52ZM36,200V170.34l53.17-53.17a4,4,0,0,1,5.66,0L181.66,204H40A4,4,0,0,1,36,200Zm180,4H193l-40-40,22.83-22.83a4,4,0,0,1,5.66,0L220,179.71V200A4,4,0,0,1,216,204ZM148,100a8,8,0,1,1,8,8A8,8,0,0,1,148,100Z" }))
  ]
]), Uh = /* @__PURE__ */ new Map([
  [
    "bold",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M232.49,215.51,185,168a92.12,92.12,0,1,0-17,17l47.53,47.54a12,12,0,0,0,17-17ZM44,112a68,68,0,1,1,68,68A68.07,68.07,0,0,1,44,112Z" }))
  ],
  [
    "duotone",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M192,112a80,80,0,1,1-80-80A80,80,0,0,1,192,112Z", opacity: "0.2" }), /* @__PURE__ */ s.createElement("path", { d: "M229.66,218.34,179.6,168.28a88.21,88.21,0,1,0-11.32,11.31l50.06,50.07a8,8,0,0,0,11.32-11.32ZM40,112a72,72,0,1,1,72,72A72.08,72.08,0,0,1,40,112Z" }))
  ],
  [
    "fill",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M168,112a56,56,0,1,1-56-56A56,56,0,0,1,168,112Zm61.66,117.66a8,8,0,0,1-11.32,0l-50.06-50.07a88,88,0,1,1,11.32-11.31l50.06,50.06A8,8,0,0,1,229.66,229.66ZM112,184a72,72,0,1,0-72-72A72.08,72.08,0,0,0,112,184Z" }))
  ],
  [
    "light",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M228.24,219.76l-51.38-51.38a86.15,86.15,0,1,0-8.48,8.48l51.38,51.38a6,6,0,0,0,8.48-8.48ZM38,112a74,74,0,1,1,74,74A74.09,74.09,0,0,1,38,112Z" }))
  ],
  [
    "regular",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M229.66,218.34l-50.07-50.06a88.11,88.11,0,1,0-11.31,11.31l50.06,50.07a8,8,0,0,0,11.32-11.32ZM40,112a72,72,0,1,1,72,72A72.08,72.08,0,0,1,40,112Z" }))
  ],
  [
    "thin",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M226.83,221.17l-52.7-52.7a84.1,84.1,0,1,0-5.66,5.66l52.7,52.7a4,4,0,0,0,5.66-5.66ZM36,112a76,76,0,1,1,76,76A76.08,76.08,0,0,1,36,112Z" }))
  ]
]), qh = /* @__PURE__ */ new Map([
  [
    "bold",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M180,72V60H89l48.4,60.5a12,12,0,0,1,0,15L89,196h91V184a12,12,0,0,1,24,0v24a12,12,0,0,1-12,12H64a12,12,0,0,1-9.37-19.5l58-72.5-58-72.5A12,12,0,0,1,64,36H192a12,12,0,0,1,12,12V72a12,12,0,0,1-24,0Z" }))
  ],
  [
    "duotone",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M192,48V208H64l64-80L64,48Z", opacity: "0.2" }), /* @__PURE__ */ s.createElement("path", { d: "M184,72V56H80.65l53.6,67a8,8,0,0,1,0,10l-53.6,67H184V184a8,8,0,0,1,16,0v24a8,8,0,0,1-8,8H64a8,8,0,0,1-6.25-13l60-75-60-75A8,8,0,0,1,64,40H192a8,8,0,0,1,8,8V72a8,8,0,0,1-16,0Z" }))
  ],
  [
    "fill",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M200,24H56A16,16,0,0,0,40,40V216a16,16,0,0,0,16,16H200a16,16,0,0,0,16-16V40A16,16,0,0,0,200,24ZM184,88a8,8,0,0,1-16,0V72H96l38.4,51.2a8,8,0,0,1,0,9.6L96,184h72V168a8,8,0,0,1,16,0v24a8,8,0,0,1-8,8H80a8,8,0,0,1-6.4-12.8L118,128,73.6,68.8A8,8,0,0,1,80,56h96a8,8,0,0,1,8,8Z" }))
  ],
  [
    "light",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M186,72V54H76.48l56.21,70.25a6,6,0,0,1,0,7.5L76.48,202H186V184a6,6,0,0,1,12,0v24a6,6,0,0,1-6,6H64a6,6,0,0,1-4.69-9.75l61-76.25-61-76.25A6,6,0,0,1,64,42H192a6,6,0,0,1,6,6V72a6,6,0,0,1-12,0Z" }))
  ],
  [
    "regular",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M184,72V56H80.65l53.6,67a8,8,0,0,1,0,10l-53.6,67H184V184a8,8,0,0,1,16,0v24a8,8,0,0,1-8,8H64a8,8,0,0,1-6.25-13l60-75-60-75A8,8,0,0,1,64,40H192a8,8,0,0,1,8,8V72a8,8,0,0,1-16,0Z" }))
  ],
  [
    "thin",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M188,72V52H72.32l58.8,73.5a4,4,0,0,1,0,5L72.32,204H188V184a4,4,0,0,1,8,0v24a4,4,0,0,1-4,4H64a4,4,0,0,1-3.12-6.5l62-77.5-62-77.5A4,4,0,0,1,64,44H192a4,4,0,0,1,4,4V72a4,4,0,0,1-8,0Z" }))
  ]
]), Bh = /* @__PURE__ */ new Map([
  [
    "bold",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M128,128a12,12,0,0,1-12,12H48a12,12,0,0,1,0-24h68A12,12,0,0,1,128,128ZM48,76H180a12,12,0,0,0,0-24H48a12,12,0,0,0,0,24Zm52,104H48a12,12,0,0,0,0,24h52a12,12,0,0,0,0-24Zm132.49-20.49a12,12,0,0,0-17,0L196,179V112a12,12,0,0,0-24,0v67l-19.51-19.52a12,12,0,0,0-17,17l40,40a12,12,0,0,0,17,0l40-40A12,12,0,0,0,232.49,159.51Z" }))
  ],
  [
    "duotone",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M224,80v88l-24,24H48V64H208A16,16,0,0,1,224,80Z", opacity: "0.2" }), /* @__PURE__ */ s.createElement("path", { d: "M128,128a8,8,0,0,1-8,8H48a8,8,0,0,1,0-16h72A8,8,0,0,1,128,128ZM48,72H184a8,8,0,0,0,0-16H48a8,8,0,0,0,0,16Zm56,112H48a8,8,0,0,0,0,16h56a8,8,0,0,0,0-16Zm125.66-21.66a8,8,0,0,0-11.32,0L192,188.69V112a8,8,0,0,0-16,0v76.69l-26.34-26.35a8,8,0,0,0-11.32,11.32l40,40a8,8,0,0,0,11.32,0l40-40A8,8,0,0,0,229.66,162.34Z" }))
  ],
  [
    "fill",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M208,32H48A16,16,0,0,0,32,48V208a16,16,0,0,0,16,16H208a16,16,0,0,0,16-16V48A16,16,0,0,0,208,32ZM72,72h96a8,8,0,0,1,0,16H72a8,8,0,0,1,0-16Zm40,112H72a8,8,0,0,1,0-16h40a8,8,0,0,1,0,16Zm8-48H72a8,8,0,0,1,0-16h48a8,8,0,0,1,0,16Zm77.66,29.66-24,24a8,8,0,0,1-11.32,0l-24-24a8,8,0,0,1,11.32-11.32L160,164.69V120a8,8,0,0,1,16,0v44.69l10.34-10.35a8,8,0,0,1,11.32,11.32Z" }))
  ],
  [
    "light",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M126,128a6,6,0,0,1-6,6H48a6,6,0,0,1,0-12h72A6,6,0,0,1,126,128ZM48,70H184a6,6,0,0,0,0-12H48a6,6,0,0,0,0,12Zm56,116H48a6,6,0,0,0,0,12h56a6,6,0,0,0,0-12Zm124.24-22.24a6,6,0,0,0-8.48,0L190,193.51V112a6,6,0,0,0-12,0v81.51l-29.76-29.75a6,6,0,0,0-8.48,8.48l40,40a6,6,0,0,0,8.48,0l40-40A6,6,0,0,0,228.24,163.76Z" }))
  ],
  [
    "regular",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M128,128a8,8,0,0,1-8,8H48a8,8,0,0,1,0-16h72A8,8,0,0,1,128,128ZM48,72H184a8,8,0,0,0,0-16H48a8,8,0,0,0,0,16Zm56,112H48a8,8,0,0,0,0,16h56a8,8,0,0,0,0-16Zm125.66-21.66a8,8,0,0,0-11.32,0L192,188.69V112a8,8,0,0,0-16,0v76.69l-26.34-26.35a8,8,0,0,0-11.32,11.32l40,40a8,8,0,0,0,11.32,0l40-40A8,8,0,0,0,229.66,162.34Z" }))
  ],
  [
    "thin",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M124,128a4,4,0,0,1-4,4H48a4,4,0,0,1,0-8h72A4,4,0,0,1,124,128ZM48,68H184a4,4,0,0,0,0-8H48a4,4,0,0,0,0,8Zm56,120H48a4,4,0,0,0,0,8h56a4,4,0,0,0,0-8Zm122.83-22.83a4,4,0,0,0-5.66,0L188,198.34V112a4,4,0,0,0-8,0v86.34l-33.17-33.17a4,4,0,0,0-5.66,5.66l40,40a4,4,0,0,0,5.66,0l40-40A4,4,0,0,0,226.83,165.17Z" }))
  ]
]), Gh = /* @__PURE__ */ new Map([
  [
    "bold",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M36,128a12,12,0,0,1,12-12h68a12,12,0,0,1,0,24H48A12,12,0,0,1,36,128ZM48,76h52a12,12,0,0,0,0-24H48a12,12,0,0,0,0,24ZM180,180H48a12,12,0,0,0,0,24H180a12,12,0,0,0,0-24ZM232.49,79.51l-40-40a12,12,0,0,0-17,0l-40,40a12,12,0,0,0,17,17L172,77v67a12,12,0,0,0,24,0V77l19.51,19.52a12,12,0,0,0,17-17Z" }))
  ],
  [
    "duotone",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M224,88v88a16,16,0,0,1-16,16H48V64H200Z", opacity: "0.2" }), /* @__PURE__ */ s.createElement("path", { d: "M40,128a8,8,0,0,1,8-8h72a8,8,0,0,1,0,16H48A8,8,0,0,1,40,128Zm8-56h56a8,8,0,0,0,0-16H48a8,8,0,0,0,0,16ZM184,184H48a8,8,0,0,0,0,16H184a8,8,0,0,0,0-16ZM229.66,82.34l-40-40a8,8,0,0,0-11.32,0l-40,40a8,8,0,0,0,11.32,11.32L176,67.31V144a8,8,0,0,0,16,0V67.31l26.34,26.35a8,8,0,0,0,11.32-11.32Z" }))
  ],
  [
    "fill",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M208,32H48A16,16,0,0,0,32,48V208a16,16,0,0,0,16,16H208a16,16,0,0,0,16-16V48A16,16,0,0,0,208,32ZM72,72h40a8,8,0,0,1,0,16H72a8,8,0,0,1,0-16Zm0,48h48a8,8,0,0,1,0,16H72a8,8,0,0,1,0-16Zm96,64H72a8,8,0,0,1,0-16h96a8,8,0,0,1,0,16Zm29.66-82.34a8,8,0,0,1-11.32,0L176,91.31V136a8,8,0,0,1-16,0V91.31l-10.34,10.35a8,8,0,0,1-11.32-11.32l24-24a8,8,0,0,1,11.32,0l24,24A8,8,0,0,1,197.66,101.66Z" }))
  ],
  [
    "light",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M42,128a6,6,0,0,1,6-6h72a6,6,0,0,1,0,12H48A6,6,0,0,1,42,128Zm6-58h56a6,6,0,0,0,0-12H48a6,6,0,0,0,0,12ZM184,186H48a6,6,0,0,0,0,12H184a6,6,0,0,0,0-12ZM228.24,83.76l-40-40a6,6,0,0,0-8.48,0l-40,40a6,6,0,0,0,8.48,8.48L178,62.49V144a6,6,0,0,0,12,0V62.49l29.76,29.75a6,6,0,0,0,8.48-8.48Z" }))
  ],
  [
    "regular",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M40,128a8,8,0,0,1,8-8h72a8,8,0,0,1,0,16H48A8,8,0,0,1,40,128Zm8-56h56a8,8,0,0,0,0-16H48a8,8,0,0,0,0,16ZM184,184H48a8,8,0,0,0,0,16H184a8,8,0,0,0,0-16ZM229.66,82.34l-40-40a8,8,0,0,0-11.32,0l-40,40a8,8,0,0,0,11.32,11.32L176,67.31V144a8,8,0,0,0,16,0V67.31l26.34,26.35a8,8,0,0,0,11.32-11.32Z" }))
  ],
  [
    "thin",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M44,128a4,4,0,0,1,4-4h72a4,4,0,0,1,0,8H48A4,4,0,0,1,44,128Zm4-60h56a4,4,0,0,0,0-8H48a4,4,0,0,0,0,8ZM184,188H48a4,4,0,0,0,0,8H184a4,4,0,0,0,0-8ZM226.83,85.17l-40-40a4,4,0,0,0-5.66,0l-40,40a4,4,0,0,0,5.66,5.66L180,57.66V144a4,4,0,0,0,8,0V57.66l33.17,33.17a4,4,0,1,0,5.66-5.66Z" }))
  ]
]), Yh = /* @__PURE__ */ new Map([
  [
    "bold",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M10.05,110.42l112,64a12,12,0,0,0,11.9,0l112-64a12,12,0,0,0,0-20.84l-112-64a12,12,0,0,0-11.9,0l-112,64a12,12,0,0,0,0,20.84Zm118-60.6L215.81,100,128,150.18,40.19,100Zm122.42,92.23A12,12,0,0,1,246,158.42l-112,64a12,12,0,0,1-11.9,0l-112-64A12,12,0,1,1,22,137.58l106,60.6,106-60.6A12,12,0,0,1,250.42,142.05Z" }))
  ],
  [
    "duotone",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M240,104,128,168,16,104,128,40Z", opacity: "0.2" }), /* @__PURE__ */ s.createElement("path", { d: "M12,111l112,64a8,8,0,0,0,7.94,0l112-64a8,8,0,0,0,0-13.9l-112-64a8,8,0,0,0-7.94,0l-112,64A8,8,0,0,0,12,111ZM128,49.21,223.87,104,128,158.79,32.13,104ZM247,140A8,8,0,0,1,244,151L132,215a8,8,0,0,1-7.94,0L12,151A8,8,0,1,1,20,137.05l108,61.74,108-61.74A8,8,0,0,1,247,140Z" }))
  ],
  [
    "fill",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M12,111l112,64a8,8,0,0,0,7.94,0l112-64a8,8,0,0,0,0-13.9l-112-64a8,8,0,0,0-7.94,0l-112,64A8,8,0,0,0,12,111Z" }), /* @__PURE__ */ s.createElement("path", { d: "M236,137.05,128,198.79,20,137.05A8,8,0,1,0,12,151l112,64a8,8,0,0,0,7.94,0l112-64a8,8,0,1,0-7.94-13.9Z" }))
  ],
  [
    "light",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M13,109.21l112,64a6,6,0,0,0,6,0l112-64a6,6,0,0,0,0-10.42l-112-64a6,6,0,0,0-6,0l-112,64a6,6,0,0,0,0,10.42Zm115-62.3L227.91,104,128,161.09,28.09,104ZM245.21,141a6,6,0,0,1-2.23,8.19l-112,64a6,6,0,0,1-6,0l-112-64a6,6,0,0,1,6-10.42l109,62.3,109-62.3A6,6,0,0,1,245.21,141Z" }))
  ],
  [
    "regular",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M12,111l112,64a8,8,0,0,0,7.94,0l112-64a8,8,0,0,0,0-13.9l-112-64a8,8,0,0,0-7.94,0l-112,64A8,8,0,0,0,12,111ZM128,49.21,223.87,104,128,158.79,32.13,104ZM246.94,140A8,8,0,0,1,244,151L132,215a8,8,0,0,1-7.94,0L12,151A8,8,0,0,1,20,137.05l108,61.74,108-61.74A8,8,0,0,1,246.94,140Z" }))
  ],
  [
    "thin",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M14,107.47l112,64a4,4,0,0,0,4,0l112-64a4,4,0,0,0,0-6.94l-112-64a4,4,0,0,0-4,0l-112,64a4,4,0,0,0,0,6.94ZM128,44.61,231.94,104,128,163.39,24.06,104ZM243.47,142a4,4,0,0,1-1.49,5.45l-112,64a4,4,0,0,1-4,0l-112-64a4,4,0,0,1,4-6.94l110,62.86,110-62.86A4,4,0,0,1,243.47,142Z" }))
  ]
]), Xh = /* @__PURE__ */ new Map([
  [
    "bold",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M224,44H32A12,12,0,0,0,20,56V192a20,20,0,0,0,20,20H216a20,20,0,0,0,20-20V56A12,12,0,0,0,224,44ZM44,116H76v24H44Zm56,0H212v24H100ZM212,68V92H44V68ZM44,164H76v24H44Zm56,24V164H212v24Z" }))
  ],
  [
    "duotone",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M88,104v96H32V104Z", opacity: "0.2" }), /* @__PURE__ */ s.createElement("path", { d: "M224,48H32a8,8,0,0,0-8,8V192a16,16,0,0,0,16,16H216a16,16,0,0,0,16-16V56A8,8,0,0,0,224,48ZM40,112H80v32H40Zm56,0H216v32H96ZM216,64V96H40V64ZM40,160H80v32H40Zm176,32H96V160H216v32Z" }))
  ],
  [
    "fill",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M224,48H32a8,8,0,0,0-8,8V192a16,16,0,0,0,16,16H216a16,16,0,0,0,16-16V56A8,8,0,0,0,224,48ZM40,112H80v32H40Zm56,0H216v32H96ZM40,160H80v32H40Zm176,32H96V160H216v32Z" }))
  ],
  [
    "light",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M224,50H32a6,6,0,0,0-6,6V192a14,14,0,0,0,14,14H216a14,14,0,0,0,14-14V56A6,6,0,0,0,224,50ZM38,110H82v36H38Zm56,0H218v36H94ZM218,62V98H38V62ZM38,192V158H82v36H40A2,2,0,0,1,38,192Zm178,2H94V158H218v34A2,2,0,0,1,216,194Z" }))
  ],
  [
    "regular",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M224,48H32a8,8,0,0,0-8,8V192a16,16,0,0,0,16,16H216a16,16,0,0,0,16-16V56A8,8,0,0,0,224,48ZM40,112H80v32H40Zm56,0H216v32H96ZM216,64V96H40V64ZM40,160H80v32H40Zm176,32H96V160H216v32Z" }))
  ],
  [
    "thin",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M224,52H32a4,4,0,0,0-4,4V192a12,12,0,0,0,12,12H216a12,12,0,0,0,12-12V56A4,4,0,0,0,224,52ZM36,108H84v40H36Zm56,0H220v40H92ZM220,60v40H36V60ZM36,192V156H84v40H40A4,4,0,0,1,36,192Zm180,4H92V156H220v36A4,4,0,0,1,216,196Z" }))
  ]
]), Qh = /* @__PURE__ */ new Map([
  [
    "bold",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M160,116h48a20,20,0,0,0,20-20V48a20,20,0,0,0-20-20H160a20,20,0,0,0-20,20V60H128a28,28,0,0,0-28,28v28H76v-4A20,20,0,0,0,56,92H24A20,20,0,0,0,4,112v32a20,20,0,0,0,20,20H56a20,20,0,0,0,20-20v-4h24v28a28,28,0,0,0,28,28h12v12a20,20,0,0,0,20,20h48a20,20,0,0,0,20-20V160a20,20,0,0,0-20-20H160a20,20,0,0,0-20,20v12H128a4,4,0,0,1-4-4V88a4,4,0,0,1,4-4h12V96A20,20,0,0,0,160,116ZM52,140H28V116H52Zm112,24h40v40H164Zm0-112h40V92H164Z" }))
  ],
  [
    "duotone",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement(
      "path",
      {
        d: "M64,112v32a8,8,0,0,1-8,8H24a8,8,0,0,1-8-8V112a8,8,0,0,1,8-8H56A8,8,0,0,1,64,112ZM208,40H160a8,8,0,0,0-8,8V96a8,8,0,0,0,8,8h48a8,8,0,0,0,8-8V48A8,8,0,0,0,208,40Zm0,112H160a8,8,0,0,0-8,8v48a8,8,0,0,0,8,8h48a8,8,0,0,0,8-8V160A8,8,0,0,0,208,152Z",
        opacity: "0.2"
      }
    ), /* @__PURE__ */ s.createElement("path", { d: "M160,112h48a16,16,0,0,0,16-16V48a16,16,0,0,0-16-16H160a16,16,0,0,0-16,16V64H128a24,24,0,0,0-24,24v32H72v-8A16,16,0,0,0,56,96H24A16,16,0,0,0,8,112v32a16,16,0,0,0,16,16H56a16,16,0,0,0,16-16v-8h32v32a24,24,0,0,0,24,24h16v16a16,16,0,0,0,16,16h48a16,16,0,0,0,16-16V160a16,16,0,0,0-16-16H160a16,16,0,0,0-16,16v16H128a8,8,0,0,1-8-8V88a8,8,0,0,1,8-8h16V96A16,16,0,0,0,160,112ZM56,144H24V112H56v32Zm104,16h48v48H160Zm0-112h48V96H160Z" }))
  ],
  [
    "fill",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M144,96V80H128a8,8,0,0,0-8,8v80a8,8,0,0,0,8,8h16V160a16,16,0,0,1,16-16h48a16,16,0,0,1,16,16v48a16,16,0,0,1-16,16H160a16,16,0,0,1-16-16V192H128a24,24,0,0,1-24-24V136H72v8a16,16,0,0,1-16,16H24A16,16,0,0,1,8,144V112A16,16,0,0,1,24,96H56a16,16,0,0,1,16,16v8h32V88a24,24,0,0,1,24-24h16V48a16,16,0,0,1,16-16h48a16,16,0,0,1,16,16V96a16,16,0,0,1-16,16H160A16,16,0,0,1,144,96Z" }))
  ],
  [
    "light",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M160,110h48a14,14,0,0,0,14-14V48a14,14,0,0,0-14-14H160a14,14,0,0,0-14,14V66H128a22,22,0,0,0-22,22v34H70V112A14,14,0,0,0,56,98H24a14,14,0,0,0-14,14v32a14,14,0,0,0,14,14H56a14,14,0,0,0,14-14V134h36v34a22,22,0,0,0,22,22h18v18a14,14,0,0,0,14,14h48a14,14,0,0,0,14-14V160a14,14,0,0,0-14-14H160a14,14,0,0,0-14,14v18H128a10,10,0,0,1-10-10V88a10,10,0,0,1,10-10h18V96A14,14,0,0,0,160,110ZM58,144a2,2,0,0,1-2,2H24a2,2,0,0,1-2-2V112a2,2,0,0,1,2-2H56a2,2,0,0,1,2,2Zm100,16a2,2,0,0,1,2-2h48a2,2,0,0,1,2,2v48a2,2,0,0,1-2,2H160a2,2,0,0,1-2-2Zm0-112a2,2,0,0,1,2-2h48a2,2,0,0,1,2,2V96a2,2,0,0,1-2,2H160a2,2,0,0,1-2-2Z" }))
  ],
  [
    "regular",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M160,112h48a16,16,0,0,0,16-16V48a16,16,0,0,0-16-16H160a16,16,0,0,0-16,16V64H128a24,24,0,0,0-24,24v32H72v-8A16,16,0,0,0,56,96H24A16,16,0,0,0,8,112v32a16,16,0,0,0,16,16H56a16,16,0,0,0,16-16v-8h32v32a24,24,0,0,0,24,24h16v16a16,16,0,0,0,16,16h48a16,16,0,0,0,16-16V160a16,16,0,0,0-16-16H160a16,16,0,0,0-16,16v16H128a8,8,0,0,1-8-8V88a8,8,0,0,1,8-8h16V96A16,16,0,0,0,160,112ZM56,144H24V112H56v32Zm104,16h48v48H160Zm0-112h48V96H160Z" }))
  ],
  [
    "thin",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M160,108h48a12,12,0,0,0,12-12V48a12,12,0,0,0-12-12H160a12,12,0,0,0-12,12V68H128a20,20,0,0,0-20,20v36H68V112a12,12,0,0,0-12-12H24a12,12,0,0,0-12,12v32a12,12,0,0,0,12,12H56a12,12,0,0,0,12-12V132h40v36a20,20,0,0,0,20,20h20v20a12,12,0,0,0,12,12h48a12,12,0,0,0,12-12V160a12,12,0,0,0-12-12H160a12,12,0,0,0-12,12v20H128a12,12,0,0,1-12-12V88a12,12,0,0,1,12-12h20V96A12,12,0,0,0,160,108ZM60,144a4,4,0,0,1-4,4H24a4,4,0,0,1-4-4V112a4,4,0,0,1,4-4H56a4,4,0,0,1,4,4Zm96,16a4,4,0,0,1,4-4h48a4,4,0,0,1,4,4v48a4,4,0,0,1-4,4H160a4,4,0,0,1-4-4Zm0-112a4,4,0,0,1,4-4h48a4,4,0,0,1,4,4V96a4,4,0,0,1-4,4H160a4,4,0,0,1-4-4Z" }))
  ]
]), Kh = /* @__PURE__ */ new Map([
  [
    "bold",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M244,56v64a12,12,0,0,1-24,0V85l-75.51,75.52a12,12,0,0,1-17,0L96,129,32.49,192.49a12,12,0,0,1-17-17l72-72a12,12,0,0,1,17,0L136,135l67-67H168a12,12,0,0,1,0-24h64A12,12,0,0,1,244,56Z" }))
  ],
  [
    "duotone",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M232,56v64L168,56Z", opacity: "0.2" }), /* @__PURE__ */ s.createElement("path", { d: "M232,48H168a8,8,0,0,0-5.66,13.66L188.69,88,136,140.69l-34.34-34.35a8,8,0,0,0-11.32,0l-72,72a8,8,0,0,0,11.32,11.32L96,123.31l34.34,34.35a8,8,0,0,0,11.32,0L200,99.31l26.34,26.35A8,8,0,0,0,240,120V56A8,8,0,0,0,232,48Zm-8,52.69L187.31,64H224Z" }))
  ],
  [
    "fill",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M240,56v64a8,8,0,0,1-13.66,5.66L200,99.31l-58.34,58.35a8,8,0,0,1-11.32,0L96,123.31,29.66,189.66a8,8,0,0,1-11.32-11.32l72-72a8,8,0,0,1,11.32,0L136,140.69,188.69,88,162.34,61.66A8,8,0,0,1,168,48h64A8,8,0,0,1,240,56Z" }))
  ],
  [
    "light",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M238,56v64a6,6,0,0,1-12,0V70.48l-85.76,85.76a6,6,0,0,1-8.48,0L96,120.49,28.24,188.24a6,6,0,0,1-8.48-8.48l72-72a6,6,0,0,1,8.48,0L136,143.51,217.52,62H168a6,6,0,0,1,0-12h64A6,6,0,0,1,238,56Z" }))
  ],
  [
    "regular",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M240,56v64a8,8,0,0,1-16,0V75.31l-82.34,82.35a8,8,0,0,1-11.32,0L96,123.31,29.66,189.66a8,8,0,0,1-11.32-11.32l72-72a8,8,0,0,1,11.32,0L136,140.69,212.69,64H168a8,8,0,0,1,0-16h64A8,8,0,0,1,240,56Z" }))
  ],
  [
    "thin",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M236,56v64a4,4,0,0,1-8,0V65.66l-89.17,89.17a4,4,0,0,1-5.66,0L96,117.66,26.83,186.83a4,4,0,0,1-5.66-5.66l72-72a4,4,0,0,1,5.66,0L136,146.34,222.34,60H168a4,4,0,0,1,0-8h64A4,4,0,0,1,236,56Z" }))
  ]
]), Jh = /* @__PURE__ */ new Map([
  [
    "bold",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M208.49,191.51a12,12,0,0,1-17,17L128,145,64.49,208.49a12,12,0,0,1-17-17L111,128,47.51,64.49a12,12,0,0,1,17-17L128,111l63.51-63.52a12,12,0,0,1,17,17L145,128Z" }))
  ],
  [
    "duotone",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement(
      "path",
      {
        d: "M216,56V200a16,16,0,0,1-16,16H56a16,16,0,0,1-16-16V56A16,16,0,0,1,56,40H200A16,16,0,0,1,216,56Z",
        opacity: "0.2"
      }
    ), /* @__PURE__ */ s.createElement("path", { d: "M205.66,194.34a8,8,0,0,1-11.32,11.32L128,139.31,61.66,205.66a8,8,0,0,1-11.32-11.32L116.69,128,50.34,61.66A8,8,0,0,1,61.66,50.34L128,116.69l66.34-66.35a8,8,0,0,1,11.32,11.32L139.31,128Z" }))
  ],
  [
    "fill",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M208,32H48A16,16,0,0,0,32,48V208a16,16,0,0,0,16,16H208a16,16,0,0,0,16-16V48A16,16,0,0,0,208,32ZM181.66,170.34a8,8,0,0,1-11.32,11.32L128,139.31,85.66,181.66a8,8,0,0,1-11.32-11.32L116.69,128,74.34,85.66A8,8,0,0,1,85.66,74.34L128,116.69l42.34-42.35a8,8,0,0,1,11.32,11.32L139.31,128Z" }))
  ],
  [
    "light",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M204.24,195.76a6,6,0,1,1-8.48,8.48L128,136.49,60.24,204.24a6,6,0,0,1-8.48-8.48L119.51,128,51.76,60.24a6,6,0,0,1,8.48-8.48L128,119.51l67.76-67.75a6,6,0,0,1,8.48,8.48L136.49,128Z" }))
  ],
  [
    "regular",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M205.66,194.34a8,8,0,0,1-11.32,11.32L128,139.31,61.66,205.66a8,8,0,0,1-11.32-11.32L116.69,128,50.34,61.66A8,8,0,0,1,61.66,50.34L128,116.69l66.34-66.35a8,8,0,0,1,11.32,11.32L139.31,128Z" }))
  ],
  [
    "thin",
    /* @__PURE__ */ s.createElement(s.Fragment, null, /* @__PURE__ */ s.createElement("path", { d: "M202.83,197.17a4,4,0,0,1-5.66,5.66L128,133.66,58.83,202.83a4,4,0,0,1-5.66-5.66L122.34,128,53.17,58.83a4,4,0,0,1,5.66-5.66L128,122.34l69.17-69.17a4,4,0,1,1,5.66,5.66L133.66,128Z" }))
  ]
]), $h = s.createContext({
  color: "currentColor",
  size: "1em",
  weight: "regular",
  mirrored: !1
}), ut = s.forwardRef(
  (i, f) => {
    const {
      alt: u,
      color: o,
      size: m,
      weight: p,
      mirrored: h,
      children: v,
      weights: x,
      ...b
    } = i, {
      color: D = "currentColor",
      size: C,
      weight: T = "regular",
      mirrored: L = !1,
      ...G
    } = s.useContext($h);
    return /* @__PURE__ */ s.createElement(
      "svg",
      {
        ref: f,
        xmlns: "http://www.w3.org/2000/svg",
        width: m ?? C,
        height: m ?? C,
        fill: o ?? D,
        viewBox: "0 0 256 256",
        transform: h || L ? "scale(-1, 1)" : void 0,
        ...G,
        ...b
      },
      !!u && /* @__PURE__ */ s.createElement("title", null, u),
      v,
      x.get(p ?? T)
    );
  }
);
ut.displayName = "IconBase";
const md = s.forwardRef((i, f) => /* @__PURE__ */ s.createElement(ut, { ref: f, ...i, weights: Ch }));
md.displayName = "ArrowsClockwiseIcon";
const Fh = md, pd = s.forwardRef((i, f) => /* @__PURE__ */ s.createElement(ut, { ref: f, ...i, weights: _h }));
pd.displayName = "CaretDownIcon";
const ks = pd, hd = s.forwardRef((i, f) => /* @__PURE__ */ s.createElement(ut, { ref: f, ...i, weights: jh }));
hd.displayName = "CheckIcon";
const Wh = hd, gd = s.forwardRef((i, f) => /* @__PURE__ */ s.createElement(ut, { ref: f, ...i, weights: Oh }));
gd.displayName = "ClipboardIcon";
const Ih = gd, bd = s.forwardRef((i, f) => /* @__PURE__ */ s.createElement(ut, { ref: f, ...i, weights: Zh }));
bd.displayName = "DotsSixVerticalIcon";
const jf = bd, vd = s.forwardRef((i, f) => /* @__PURE__ */ s.createElement(ut, { ref: f, ...i, weights: Dh }));
vd.displayName = "EyeIcon";
const xd = vd, yd = s.forwardRef((i, f) => /* @__PURE__ */ s.createElement(ut, { ref: f, ...i, weights: Rh }));
yd.displayName = "FlameIcon";
const Of = yd, wd = s.forwardRef((i, f) => /* @__PURE__ */ s.createElement(ut, { ref: f, ...i, weights: Vh }));
wd.displayName = "FunnelIcon";
const zs = wd, Sd = s.forwardRef((i, f) => /* @__PURE__ */ s.createElement(ut, { ref: f, ...i, weights: Lh }));
Sd.displayName = "ImageIcon";
const Ph = Sd, Ed = s.forwardRef((i, f) => /* @__PURE__ */ s.createElement(ut, { ref: f, ...i, weights: Uh }));
Ed.displayName = "MagnifyingGlassIcon";
const Ad = Ed, Md = s.forwardRef((i, f) => /* @__PURE__ */ s.createElement(ut, { ref: f, ...i, weights: qh }));
Md.displayName = "SigmaIcon";
const e2 = Md, kd = s.forwardRef((i, f) => /* @__PURE__ */ s.createElement(ut, { ref: f, ...i, weights: Bh }));
kd.displayName = "SortAscendingIcon";
const t2 = kd, zd = s.forwardRef((i, f) => /* @__PURE__ */ s.createElement(ut, { ref: f, ...i, weights: Gh }));
zd.displayName = "SortDescendingIcon";
const a2 = zd, Hd = s.forwardRef((i, f) => /* @__PURE__ */ s.createElement(ut, { ref: f, ...i, weights: Yh }));
Hd.displayName = "StackSimpleIcon";
const l2 = Hd, Td = s.forwardRef((i, f) => /* @__PURE__ */ s.createElement(ut, { ref: f, ...i, weights: Xh }));
Td.displayName = "TableIcon";
const n2 = Td, Nd = s.forwardRef((i, f) => /* @__PURE__ */ s.createElement(ut, { ref: f, ...i, weights: Qh }));
Nd.displayName = "TreeStructureIcon";
const Cd = Nd, _d = s.forwardRef((i, f) => /* @__PURE__ */ s.createElement(ut, { ref: f, ...i, weights: Kh }));
_d.displayName = "TrendUpIcon";
const Zf = _d, jd = s.forwardRef((i, f) => /* @__PURE__ */ s.createElement(ut, { ref: f, ...i, weights: Jh }));
jd.displayName = "XIcon";
const r2 = jd;
var Hs = dd();
function kn(i, f, u) {
  let o = u.initialDeps ?? [], m, p = !0;
  function h() {
    var v, x, b;
    let D;
    u.key && ((v = u.debug) != null && v.call(u)) && (D = Date.now());
    const C = i();
    if (!(C.length !== o.length || C.some((G, Q) => o[Q] !== G)))
      return m;
    o = C;
    let L;
    if (u.key && ((x = u.debug) != null && x.call(u)) && (L = Date.now()), m = f(...C), u.key && ((b = u.debug) != null && b.call(u))) {
      const G = Math.round((Date.now() - D) * 100) / 100, Q = Math.round((Date.now() - L) * 100) / 100, de = Q / 16, fe = (se, be) => {
        for (se = String(se); se.length < be; )
          se = " " + se;
        return se;
      };
      console.info(
        `%c⏱ ${fe(Q, 5)} /${fe(G, 5)} ms`,
        `
            font-size: .6rem;
            font-weight: bold;
            color: hsl(${Math.max(
          0,
          Math.min(120 - 120 * de, 120)
        )}deg 100% 31%);`,
        u?.key
      );
    }
    return u?.onChange && !(p && u.skipInitialOnChange) && u.onChange(m), p = !1, m;
  }
  return h.updateDeps = (v) => {
    o = v;
  }, h;
}
function Df(i, f) {
  if (i === void 0)
    throw new Error("Unexpected undefined");
  return i;
}
const i2 = (i, f) => Math.abs(i - f) < 1.01, o2 = (i, f, u) => {
  let o;
  return function(...m) {
    i.clearTimeout(o), o = i.setTimeout(() => f.apply(this, m), u);
  };
}, Rf = (i) => {
  const { offsetWidth: f, offsetHeight: u } = i;
  return { width: f, height: u };
}, c2 = (i) => i, s2 = (i) => {
  const f = Math.max(i.startIndex - i.overscan, 0), u = Math.min(i.endIndex + i.overscan, i.count - 1), o = [];
  for (let m = f; m <= u; m++)
    o.push(m);
  return o;
}, u2 = (i, f) => {
  const u = i.scrollElement;
  if (!u)
    return;
  const o = i.targetWindow;
  if (!o)
    return;
  const m = (h) => {
    const { width: v, height: x } = h;
    f({ width: Math.round(v), height: Math.round(x) });
  };
  if (m(Rf(u)), !o.ResizeObserver)
    return () => {
    };
  const p = new o.ResizeObserver((h) => {
    const v = () => {
      const x = h[0];
      if (x?.borderBoxSize) {
        const b = x.borderBoxSize[0];
        if (b) {
          m({ width: b.inlineSize, height: b.blockSize });
          return;
        }
      }
      m(Rf(u));
    };
    i.options.useAnimationFrameWithResizeObserver ? requestAnimationFrame(v) : v();
  });
  return p.observe(u, { box: "border-box" }), () => {
    p.unobserve(u);
  };
}, Vf = {
  passive: !0
}, Lf = typeof window > "u" ? !0 : "onscrollend" in window, f2 = (i, f) => {
  const u = i.scrollElement;
  if (!u)
    return;
  const o = i.targetWindow;
  if (!o)
    return;
  let m = 0;
  const p = i.options.useScrollendEvent && Lf ? () => {
  } : o2(
    o,
    () => {
      f(m, !1);
    },
    i.options.isScrollingResetDelay
  ), h = (D) => () => {
    const { horizontal: C, isRtl: T } = i.options;
    m = C ? u.scrollLeft * (T && -1 || 1) : u.scrollTop, p(), f(m, D);
  }, v = h(!0), x = h(!1);
  u.addEventListener("scroll", v, Vf);
  const b = i.options.useScrollendEvent && Lf;
  return b && u.addEventListener("scrollend", x, Vf), () => {
    u.removeEventListener("scroll", v), b && u.removeEventListener("scrollend", x);
  };
}, d2 = (i, f, u) => {
  if (f?.borderBoxSize) {
    const o = f.borderBoxSize[0];
    if (o)
      return Math.round(
        o[u.options.horizontal ? "inlineSize" : "blockSize"]
      );
  }
  return i[u.options.horizontal ? "offsetWidth" : "offsetHeight"];
}, m2 = (i, {
  adjustments: f = 0,
  behavior: u
}, o) => {
  var m, p;
  const h = i + f;
  (p = (m = o.scrollElement) == null ? void 0 : m.scrollTo) == null || p.call(m, {
    [o.options.horizontal ? "left" : "top"]: h,
    behavior: u
  });
};
class p2 {
  constructor(f) {
    this.unsubs = [], this.scrollElement = null, this.targetWindow = null, this.isScrolling = !1, this.scrollState = null, this.measurementsCache = [], this.itemSizeCache = /* @__PURE__ */ new Map(), this.laneAssignments = /* @__PURE__ */ new Map(), this.pendingMeasuredCacheIndexes = [], this.prevLanes = void 0, this.lanesChangedFlag = !1, this.lanesSettling = !1, this.scrollRect = null, this.scrollOffset = null, this.scrollDirection = null, this.scrollAdjustments = 0, this.elementsCache = /* @__PURE__ */ new Map(), this.now = () => {
      var u, o, m;
      return ((m = (o = (u = this.targetWindow) == null ? void 0 : u.performance) == null ? void 0 : o.now) == null ? void 0 : m.call(o)) ?? Date.now();
    }, this.observer = /* @__PURE__ */ (() => {
      let u = null;
      const o = () => u || (!this.targetWindow || !this.targetWindow.ResizeObserver ? null : u = new this.targetWindow.ResizeObserver((m) => {
        m.forEach((p) => {
          const h = () => {
            const v = p.target, x = this.indexFromElement(v);
            if (!v.isConnected) {
              this.observer.unobserve(v);
              return;
            }
            this.shouldMeasureDuringScroll(x) && this.resizeItem(
              x,
              this.options.measureElement(v, p, this)
            );
          };
          this.options.useAnimationFrameWithResizeObserver ? requestAnimationFrame(h) : h();
        });
      }));
      return {
        disconnect: () => {
          var m;
          (m = o()) == null || m.disconnect(), u = null;
        },
        observe: (m) => {
          var p;
          return (p = o()) == null ? void 0 : p.observe(m, { box: "border-box" });
        },
        unobserve: (m) => {
          var p;
          return (p = o()) == null ? void 0 : p.unobserve(m);
        }
      };
    })(), this.range = null, this.setOptions = (u) => {
      Object.entries(u).forEach(([o, m]) => {
        typeof m > "u" && delete u[o];
      }), this.options = {
        debug: !1,
        initialOffset: 0,
        overscan: 1,
        paddingStart: 0,
        paddingEnd: 0,
        scrollPaddingStart: 0,
        scrollPaddingEnd: 0,
        horizontal: !1,
        getItemKey: c2,
        rangeExtractor: s2,
        onChange: () => {
        },
        measureElement: d2,
        initialRect: { width: 0, height: 0 },
        scrollMargin: 0,
        gap: 0,
        indexAttribute: "data-index",
        initialMeasurementsCache: [],
        lanes: 1,
        isScrollingResetDelay: 150,
        enabled: !0,
        isRtl: !1,
        useScrollendEvent: !1,
        useAnimationFrameWithResizeObserver: !1,
        laneAssignmentMode: "estimate",
        ...u
      };
    }, this.notify = (u) => {
      var o, m;
      (m = (o = this.options).onChange) == null || m.call(o, this, u);
    }, this.maybeNotify = kn(
      () => (this.calculateRange(), [
        this.isScrolling,
        this.range ? this.range.startIndex : null,
        this.range ? this.range.endIndex : null
      ]),
      (u) => {
        this.notify(u);
      },
      {
        key: !1,
        debug: () => this.options.debug,
        initialDeps: [
          this.isScrolling,
          this.range ? this.range.startIndex : null,
          this.range ? this.range.endIndex : null
        ]
      }
    ), this.cleanup = () => {
      this.unsubs.filter(Boolean).forEach((u) => u()), this.unsubs = [], this.observer.disconnect(), this.rafId != null && this.targetWindow && (this.targetWindow.cancelAnimationFrame(this.rafId), this.rafId = null), this.scrollState = null, this.scrollElement = null, this.targetWindow = null;
    }, this._didMount = () => () => {
      this.cleanup();
    }, this._willUpdate = () => {
      var u;
      const o = this.options.enabled ? this.options.getScrollElement() : null;
      if (this.scrollElement !== o) {
        if (this.cleanup(), !o) {
          this.maybeNotify();
          return;
        }
        this.scrollElement = o, this.scrollElement && "ownerDocument" in this.scrollElement ? this.targetWindow = this.scrollElement.ownerDocument.defaultView : this.targetWindow = ((u = this.scrollElement) == null ? void 0 : u.window) ?? null, this.elementsCache.forEach((m) => {
          this.observer.observe(m);
        }), this.unsubs.push(
          this.options.observeElementRect(this, (m) => {
            this.scrollRect = m, this.maybeNotify();
          })
        ), this.unsubs.push(
          this.options.observeElementOffset(this, (m, p) => {
            this.scrollAdjustments = 0, this.scrollDirection = p ? this.getScrollOffset() < m ? "forward" : "backward" : null, this.scrollOffset = m, this.isScrolling = p, this.scrollState && this.scheduleScrollReconcile(), this.maybeNotify();
          })
        ), this._scrollToOffset(this.getScrollOffset(), {
          adjustments: void 0,
          behavior: void 0
        });
      }
    }, this.rafId = null, this.getSize = () => this.options.enabled ? (this.scrollRect = this.scrollRect ?? this.options.initialRect, this.scrollRect[this.options.horizontal ? "width" : "height"]) : (this.scrollRect = null, 0), this.getScrollOffset = () => this.options.enabled ? (this.scrollOffset = this.scrollOffset ?? (typeof this.options.initialOffset == "function" ? this.options.initialOffset() : this.options.initialOffset), this.scrollOffset) : (this.scrollOffset = null, 0), this.getFurthestMeasurement = (u, o) => {
      const m = /* @__PURE__ */ new Map(), p = /* @__PURE__ */ new Map();
      for (let h = o - 1; h >= 0; h--) {
        const v = u[h];
        if (m.has(v.lane))
          continue;
        const x = p.get(
          v.lane
        );
        if (x == null || v.end > x.end ? p.set(v.lane, v) : v.end < x.end && m.set(v.lane, !0), m.size === this.options.lanes)
          break;
      }
      return p.size === this.options.lanes ? Array.from(p.values()).sort((h, v) => h.end === v.end ? h.index - v.index : h.end - v.end)[0] : void 0;
    }, this.getMeasurementOptions = kn(
      () => [
        this.options.count,
        this.options.paddingStart,
        this.options.scrollMargin,
        this.options.getItemKey,
        this.options.enabled,
        this.options.lanes,
        this.options.laneAssignmentMode
      ],
      (u, o, m, p, h, v, x) => (this.prevLanes !== void 0 && this.prevLanes !== v && (this.lanesChangedFlag = !0), this.prevLanes = v, this.pendingMeasuredCacheIndexes = [], {
        count: u,
        paddingStart: o,
        scrollMargin: m,
        getItemKey: p,
        enabled: h,
        lanes: v,
        laneAssignmentMode: x
      }),
      {
        key: !1
      }
    ), this.getMeasurements = kn(
      () => [this.getMeasurementOptions(), this.itemSizeCache],
      ({
        count: u,
        paddingStart: o,
        scrollMargin: m,
        getItemKey: p,
        enabled: h,
        lanes: v,
        laneAssignmentMode: x
      }, b) => {
        if (!h)
          return this.measurementsCache = [], this.itemSizeCache.clear(), this.laneAssignments.clear(), [];
        if (this.laneAssignments.size > u)
          for (const L of this.laneAssignments.keys())
            L >= u && this.laneAssignments.delete(L);
        this.lanesChangedFlag && (this.lanesChangedFlag = !1, this.lanesSettling = !0, this.measurementsCache = [], this.itemSizeCache.clear(), this.laneAssignments.clear(), this.pendingMeasuredCacheIndexes = []), this.measurementsCache.length === 0 && !this.lanesSettling && (this.measurementsCache = this.options.initialMeasurementsCache, this.measurementsCache.forEach((L) => {
          this.itemSizeCache.set(L.key, L.size);
        }));
        const D = this.lanesSettling ? 0 : this.pendingMeasuredCacheIndexes.length > 0 ? Math.min(...this.pendingMeasuredCacheIndexes) : 0;
        this.pendingMeasuredCacheIndexes = [], this.lanesSettling && this.measurementsCache.length === u && (this.lanesSettling = !1);
        const C = this.measurementsCache.slice(0, D), T = new Array(v).fill(
          void 0
        );
        for (let L = 0; L < D; L++) {
          const G = C[L];
          G && (T[G.lane] = L);
        }
        for (let L = D; L < u; L++) {
          const G = p(L), Q = this.laneAssignments.get(L);
          let de, fe;
          const se = x === "estimate" || b.has(G);
          if (Q !== void 0 && this.options.lanes > 1) {
            de = Q;
            const J = T[de], K = J !== void 0 ? C[J] : void 0;
            fe = K ? K.end + this.options.gap : o + m;
          } else {
            const J = this.options.lanes === 1 ? C[L - 1] : this.getFurthestMeasurement(C, L);
            fe = J ? J.end + this.options.gap : o + m, de = J ? J.lane : L % this.options.lanes, this.options.lanes > 1 && se && this.laneAssignments.set(L, de);
          }
          const be = b.get(G), Oe = typeof be == "number" ? be : this.options.estimateSize(L), Le = fe + Oe;
          C[L] = {
            index: L,
            start: fe,
            size: Oe,
            end: Le,
            key: G,
            lane: de
          }, T[de] = L;
        }
        return this.measurementsCache = C, C;
      },
      {
        key: !1,
        debug: () => this.options.debug
      }
    ), this.calculateRange = kn(
      () => [
        this.getMeasurements(),
        this.getSize(),
        this.getScrollOffset(),
        this.options.lanes
      ],
      (u, o, m, p) => this.range = u.length > 0 && o > 0 ? h2({
        measurements: u,
        outerSize: o,
        scrollOffset: m,
        lanes: p
      }) : null,
      {
        key: !1,
        debug: () => this.options.debug
      }
    ), this.getVirtualIndexes = kn(
      () => {
        let u = null, o = null;
        const m = this.calculateRange();
        return m && (u = m.startIndex, o = m.endIndex), this.maybeNotify.updateDeps([this.isScrolling, u, o]), [
          this.options.rangeExtractor,
          this.options.overscan,
          this.options.count,
          u,
          o
        ];
      },
      (u, o, m, p, h) => p === null || h === null ? [] : u({
        startIndex: p,
        endIndex: h,
        overscan: o,
        count: m
      }),
      {
        key: !1,
        debug: () => this.options.debug
      }
    ), this.indexFromElement = (u) => {
      const o = this.options.indexAttribute, m = u.getAttribute(o);
      return m ? parseInt(m, 10) : (console.warn(
        `Missing attribute name '${o}={index}' on measured element.`
      ), -1);
    }, this.shouldMeasureDuringScroll = (u) => {
      var o;
      if (!this.scrollState || this.scrollState.behavior !== "smooth")
        return !0;
      const m = this.scrollState.index ?? ((o = this.getVirtualItemForOffset(this.scrollState.lastTargetOffset)) == null ? void 0 : o.index);
      if (m !== void 0 && this.range) {
        const p = Math.max(
          this.options.overscan,
          Math.ceil((this.range.endIndex - this.range.startIndex) / 2)
        ), h = Math.max(0, m - p), v = Math.min(
          this.options.count - 1,
          m + p
        );
        return u >= h && u <= v;
      }
      return !0;
    }, this.measureElement = (u) => {
      if (!u) {
        this.elementsCache.forEach((h, v) => {
          h.isConnected || (this.observer.unobserve(h), this.elementsCache.delete(v));
        });
        return;
      }
      const o = this.indexFromElement(u), m = this.options.getItemKey(o), p = this.elementsCache.get(m);
      p !== u && (p && this.observer.unobserve(p), this.observer.observe(u), this.elementsCache.set(m, u)), (!this.isScrolling || this.scrollState) && this.shouldMeasureDuringScroll(o) && this.resizeItem(o, this.options.measureElement(u, void 0, this));
    }, this.resizeItem = (u, o) => {
      var m;
      const p = this.measurementsCache[u];
      if (!p) return;
      const h = this.itemSizeCache.get(p.key) ?? p.size, v = o - h;
      v !== 0 && (((m = this.scrollState) == null ? void 0 : m.behavior) !== "smooth" && (this.shouldAdjustScrollPositionOnItemSizeChange !== void 0 ? this.shouldAdjustScrollPositionOnItemSizeChange(p, v, this) : p.start < this.getScrollOffset() + this.scrollAdjustments) && this._scrollToOffset(this.getScrollOffset(), {
        adjustments: this.scrollAdjustments += v,
        behavior: void 0
      }), this.pendingMeasuredCacheIndexes.push(p.index), this.itemSizeCache = new Map(this.itemSizeCache.set(p.key, o)), this.notify(!1));
    }, this.getVirtualItems = kn(
      () => [this.getVirtualIndexes(), this.getMeasurements()],
      (u, o) => {
        const m = [];
        for (let p = 0, h = u.length; p < h; p++) {
          const v = u[p], x = o[v];
          m.push(x);
        }
        return m;
      },
      {
        key: !1,
        debug: () => this.options.debug
      }
    ), this.getVirtualItemForOffset = (u) => {
      const o = this.getMeasurements();
      if (o.length !== 0)
        return Df(
          o[Od(
            0,
            o.length - 1,
            (m) => Df(o[m]).start,
            u
          )]
        );
    }, this.getMaxScrollOffset = () => {
      if (!this.scrollElement) return 0;
      if ("scrollHeight" in this.scrollElement)
        return this.options.horizontal ? this.scrollElement.scrollWidth - this.scrollElement.clientWidth : this.scrollElement.scrollHeight - this.scrollElement.clientHeight;
      {
        const u = this.scrollElement.document.documentElement;
        return this.options.horizontal ? u.scrollWidth - this.scrollElement.innerWidth : u.scrollHeight - this.scrollElement.innerHeight;
      }
    }, this.getOffsetForAlignment = (u, o, m = 0) => {
      if (!this.scrollElement) return 0;
      const p = this.getSize(), h = this.getScrollOffset();
      o === "auto" && (o = u >= h + p ? "end" : "start"), o === "center" ? u += (m - p) / 2 : o === "end" && (u -= p);
      const v = this.getMaxScrollOffset();
      return Math.max(Math.min(v, u), 0);
    }, this.getOffsetForIndex = (u, o = "auto") => {
      u = Math.max(0, Math.min(u, this.options.count - 1));
      const m = this.getSize(), p = this.getScrollOffset(), h = this.measurementsCache[u];
      if (!h) return;
      if (o === "auto")
        if (h.end >= p + m - this.options.scrollPaddingEnd)
          o = "end";
        else if (h.start <= p + this.options.scrollPaddingStart)
          o = "start";
        else
          return [p, o];
      if (o === "end" && u === this.options.count - 1)
        return [this.getMaxScrollOffset(), o];
      const v = o === "end" ? h.end + this.options.scrollPaddingEnd : h.start - this.options.scrollPaddingStart;
      return [
        this.getOffsetForAlignment(v, o, h.size),
        o
      ];
    }, this.scrollToOffset = (u, { align: o = "start", behavior: m = "auto" } = {}) => {
      const p = this.getOffsetForAlignment(u, o), h = this.now();
      this.scrollState = {
        index: null,
        align: o,
        behavior: m,
        startedAt: h,
        lastTargetOffset: p,
        stableFrames: 0
      }, this._scrollToOffset(p, { adjustments: void 0, behavior: m }), this.scheduleScrollReconcile();
    }, this.scrollToIndex = (u, {
      align: o = "auto",
      behavior: m = "auto"
    } = {}) => {
      u = Math.max(0, Math.min(u, this.options.count - 1));
      const p = this.getOffsetForIndex(u, o);
      if (!p)
        return;
      const [h, v] = p, x = this.now();
      this.scrollState = {
        index: u,
        align: v,
        behavior: m,
        startedAt: x,
        lastTargetOffset: h,
        stableFrames: 0
      }, this._scrollToOffset(h, { adjustments: void 0, behavior: m }), this.scheduleScrollReconcile();
    }, this.scrollBy = (u, { behavior: o = "auto" } = {}) => {
      const m = this.getScrollOffset() + u, p = this.now();
      this.scrollState = {
        index: null,
        align: "start",
        behavior: o,
        startedAt: p,
        lastTargetOffset: m,
        stableFrames: 0
      }, this._scrollToOffset(m, { adjustments: void 0, behavior: o }), this.scheduleScrollReconcile();
    }, this.getTotalSize = () => {
      var u;
      const o = this.getMeasurements();
      let m;
      if (o.length === 0)
        m = this.options.paddingStart;
      else if (this.options.lanes === 1)
        m = ((u = o[o.length - 1]) == null ? void 0 : u.end) ?? 0;
      else {
        const p = Array(this.options.lanes).fill(null);
        let h = o.length - 1;
        for (; h >= 0 && p.some((v) => v === null); ) {
          const v = o[h];
          p[v.lane] === null && (p[v.lane] = v.end), h--;
        }
        m = Math.max(...p.filter((v) => v !== null));
      }
      return Math.max(
        m - this.options.scrollMargin + this.options.paddingEnd,
        0
      );
    }, this._scrollToOffset = (u, {
      adjustments: o,
      behavior: m
    }) => {
      this.options.scrollToFn(u, { behavior: m, adjustments: o }, this);
    }, this.measure = () => {
      this.itemSizeCache = /* @__PURE__ */ new Map(), this.laneAssignments = /* @__PURE__ */ new Map(), this.notify(!1);
    }, this.setOptions(f);
  }
  scheduleScrollReconcile() {
    if (!this.targetWindow) {
      this.scrollState = null;
      return;
    }
    this.rafId == null && (this.rafId = this.targetWindow.requestAnimationFrame(() => {
      this.rafId = null, this.reconcileScroll();
    }));
  }
  reconcileScroll() {
    if (!this.scrollState || !this.scrollElement) return;
    if (this.now() - this.scrollState.startedAt > 5e3) {
      this.scrollState = null;
      return;
    }
    const o = this.scrollState.index != null ? this.getOffsetForIndex(this.scrollState.index, this.scrollState.align) : void 0, m = o ? o[0] : this.scrollState.lastTargetOffset, p = 1, h = m !== this.scrollState.lastTargetOffset;
    if (!h && i2(m, this.getScrollOffset())) {
      if (this.scrollState.stableFrames++, this.scrollState.stableFrames >= p) {
        this.scrollState = null;
        return;
      }
    } else
      this.scrollState.stableFrames = 0, h && (this.scrollState.lastTargetOffset = m, this.scrollState.behavior = "auto", this._scrollToOffset(m, {
        adjustments: void 0,
        behavior: "auto"
      }));
    this.scheduleScrollReconcile();
  }
}
const Od = (i, f, u, o) => {
  for (; i <= f; ) {
    const m = (i + f) / 2 | 0, p = u(m);
    if (p < o)
      i = m + 1;
    else if (p > o)
      f = m - 1;
    else
      return m;
  }
  return i > 0 ? i - 1 : 0;
};
function h2({
  measurements: i,
  outerSize: f,
  scrollOffset: u,
  lanes: o
}) {
  const m = i.length - 1, p = (x) => i[x].start;
  if (i.length <= o)
    return {
      startIndex: 0,
      endIndex: m
    };
  let h = Od(
    0,
    m,
    p,
    u
  ), v = h;
  if (o === 1)
    for (; v < m && i[v].end < u + f; )
      v++;
  else if (o > 1) {
    const x = Array(o).fill(0);
    for (; v < m && x.some((D) => D < u + f); ) {
      const D = i[v];
      x[D.lane] = D.end, v++;
    }
    const b = Array(o).fill(u + f);
    for (; h >= 0 && b.some((D) => D >= u); ) {
      const D = i[h];
      b[D.lane] = D.start, h--;
    }
    h = Math.max(0, h - h % o), v = Math.min(m, v + (o - 1 - v % o));
  }
  return { startIndex: h, endIndex: v };
}
const Uf = typeof document < "u" ? s.useLayoutEffect : s.useEffect;
function g2({
  useFlushSync: i = !0,
  ...f
}) {
  const u = s.useReducer(() => ({}), {})[1], o = {
    ...f,
    onChange: (p, h) => {
      var v;
      i && h ? Hs.flushSync(u) : u(), (v = f.onChange) == null || v.call(f, p, h);
    }
  }, [m] = s.useState(
    () => new p2(o)
  );
  return m.setOptions(o), Uf(() => m._didMount(), []), Uf(() => m._willUpdate()), m;
}
function qf(i) {
  return g2({
    observeElementRect: u2,
    observeElementOffset: f2,
    scrollToFn: m2,
    ...i
  });
}
function b2(i) {
  return `var(--viz-kind-${v2(i, 10)})`;
}
function v2(i, f) {
  let u = 0;
  for (let o = 0; o < i.length; o += 1)
    u = (u << 5) - u + i.charCodeAt(o) | 0;
  return (u % f + f) % f + 1;
}
function Zd(i) {
  var f, u, o = "";
  if (typeof i == "string" || typeof i == "number") o += i;
  else if (typeof i == "object") if (Array.isArray(i)) {
    var m = i.length;
    for (f = 0; f < m; f++) i[f] && (u = Zd(i[f])) && (o && (o += " "), o += u);
  } else for (u in i) i[u] && (o && (o += " "), o += u);
  return o;
}
function x2() {
  for (var i, f, u = 0, o = "", m = arguments.length; u < m; u++) (i = arguments[u]) && (f = Zd(i)) && (o && (o += " "), o += f);
  return o;
}
const y2 = (i, f) => {
  const u = new Array(i.length + f.length);
  for (let o = 0; o < i.length; o++)
    u[o] = i[o];
  for (let o = 0; o < f.length; o++)
    u[i.length + o] = f[o];
  return u;
}, w2 = (i, f) => ({
  classGroupId: i,
  validator: f
}), Dd = (i = /* @__PURE__ */ new Map(), f = null, u) => ({
  nextPart: i,
  validators: f,
  classGroupId: u
}), Ii = "-", Bf = [], S2 = "arbitrary..", E2 = (i) => {
  const f = M2(i), {
    conflictingClassGroups: u,
    conflictingClassGroupModifiers: o
  } = i;
  return {
    getClassGroupId: (h) => {
      if (h.startsWith("[") && h.endsWith("]"))
        return A2(h);
      const v = h.split(Ii), x = v[0] === "" && v.length > 1 ? 1 : 0;
      return Rd(v, x, f);
    },
    getConflictingClassGroupIds: (h, v) => {
      if (v) {
        const x = o[h], b = u[h];
        return x ? b ? y2(b, x) : x : b || Bf;
      }
      return u[h] || Bf;
    }
  };
}, Rd = (i, f, u) => {
  if (i.length - f === 0)
    return u.classGroupId;
  const m = i[f], p = u.nextPart.get(m);
  if (p) {
    const b = Rd(i, f + 1, p);
    if (b) return b;
  }
  const h = u.validators;
  if (h === null)
    return;
  const v = f === 0 ? i.join(Ii) : i.slice(f).join(Ii), x = h.length;
  for (let b = 0; b < x; b++) {
    const D = h[b];
    if (D.validator(v))
      return D.classGroupId;
  }
}, A2 = (i) => i.slice(1, -1).indexOf(":") === -1 ? void 0 : (() => {
  const f = i.slice(1, -1), u = f.indexOf(":"), o = f.slice(0, u);
  return o ? S2 + o : void 0;
})(), M2 = (i) => {
  const {
    theme: f,
    classGroups: u
  } = i;
  return k2(u, f);
}, k2 = (i, f) => {
  const u = Dd();
  for (const o in i) {
    const m = i[o];
    Ts(m, u, o, f);
  }
  return u;
}, Ts = (i, f, u, o) => {
  const m = i.length;
  for (let p = 0; p < m; p++) {
    const h = i[p];
    z2(h, f, u, o);
  }
}, z2 = (i, f, u, o) => {
  if (typeof i == "string") {
    H2(i, f, u);
    return;
  }
  if (typeof i == "function") {
    T2(i, f, u, o);
    return;
  }
  N2(i, f, u, o);
}, H2 = (i, f, u) => {
  const o = i === "" ? f : Vd(f, i);
  o.classGroupId = u;
}, T2 = (i, f, u, o) => {
  if (C2(i)) {
    Ts(i(o), f, u, o);
    return;
  }
  f.validators === null && (f.validators = []), f.validators.push(w2(u, i));
}, N2 = (i, f, u, o) => {
  const m = Object.entries(i), p = m.length;
  for (let h = 0; h < p; h++) {
    const [v, x] = m[h];
    Ts(x, Vd(f, v), u, o);
  }
}, Vd = (i, f) => {
  let u = i;
  const o = f.split(Ii), m = o.length;
  for (let p = 0; p < m; p++) {
    const h = o[p];
    let v = u.nextPart.get(h);
    v || (v = Dd(), u.nextPart.set(h, v)), u = v;
  }
  return u;
}, C2 = (i) => "isThemeGetter" in i && i.isThemeGetter === !0, _2 = (i) => {
  if (i < 1)
    return {
      get: () => {
      },
      set: () => {
      }
    };
  let f = 0, u = /* @__PURE__ */ Object.create(null), o = /* @__PURE__ */ Object.create(null);
  const m = (p, h) => {
    u[p] = h, f++, f > i && (f = 0, o = u, u = /* @__PURE__ */ Object.create(null));
  };
  return {
    get(p) {
      let h = u[p];
      if (h !== void 0)
        return h;
      if ((h = o[p]) !== void 0)
        return m(p, h), h;
    },
    set(p, h) {
      p in u ? u[p] = h : m(p, h);
    }
  };
}, Ss = "!", Gf = ":", j2 = [], Yf = (i, f, u, o, m) => ({
  modifiers: i,
  hasImportantModifier: f,
  baseClassName: u,
  maybePostfixModifierPosition: o,
  isExternal: m
}), O2 = (i) => {
  const {
    prefix: f,
    experimentalParseClassName: u
  } = i;
  let o = (m) => {
    const p = [];
    let h = 0, v = 0, x = 0, b;
    const D = m.length;
    for (let Q = 0; Q < D; Q++) {
      const de = m[Q];
      if (h === 0 && v === 0) {
        if (de === Gf) {
          p.push(m.slice(x, Q)), x = Q + 1;
          continue;
        }
        if (de === "/") {
          b = Q;
          continue;
        }
      }
      de === "[" ? h++ : de === "]" ? h-- : de === "(" ? v++ : de === ")" && v--;
    }
    const C = p.length === 0 ? m : m.slice(x);
    let T = C, L = !1;
    C.endsWith(Ss) ? (T = C.slice(0, -1), L = !0) : (
      /**
       * In Tailwind CSS v3 the important modifier was at the start of the base class name. This is still supported for legacy reasons.
       * @see https://github.com/dcastil/tailwind-merge/issues/513#issuecomment-2614029864
       */
      C.startsWith(Ss) && (T = C.slice(1), L = !0)
    );
    const G = b && b > x ? b - x : void 0;
    return Yf(p, L, T, G);
  };
  if (f) {
    const m = f + Gf, p = o;
    o = (h) => h.startsWith(m) ? p(h.slice(m.length)) : Yf(j2, !1, h, void 0, !0);
  }
  if (u) {
    const m = o;
    o = (p) => u({
      className: p,
      parseClassName: m
    });
  }
  return o;
}, Z2 = (i) => {
  const f = /* @__PURE__ */ new Map();
  return i.orderSensitiveModifiers.forEach((u, o) => {
    f.set(u, 1e6 + o);
  }), (u) => {
    const o = [];
    let m = [];
    for (let p = 0; p < u.length; p++) {
      const h = u[p], v = h[0] === "[", x = f.has(h);
      v || x ? (m.length > 0 && (m.sort(), o.push(...m), m = []), o.push(h)) : m.push(h);
    }
    return m.length > 0 && (m.sort(), o.push(...m)), o;
  };
}, D2 = (i) => ({
  cache: _2(i.cacheSize),
  parseClassName: O2(i),
  sortModifiers: Z2(i),
  postfixLookupClassGroupIds: R2(i),
  ...E2(i)
}), R2 = (i) => {
  const f = /* @__PURE__ */ Object.create(null), u = i.postfixLookupClassGroups;
  if (u)
    for (let o = 0; o < u.length; o++)
      f[u[o]] = !0;
  return f;
}, V2 = /\s+/, L2 = (i, f) => {
  const {
    parseClassName: u,
    getClassGroupId: o,
    getConflictingClassGroupIds: m,
    sortModifiers: p,
    postfixLookupClassGroupIds: h
  } = f, v = [], x = i.trim().split(V2);
  let b = "";
  for (let D = x.length - 1; D >= 0; D -= 1) {
    const C = x[D], {
      isExternal: T,
      modifiers: L,
      hasImportantModifier: G,
      baseClassName: Q,
      maybePostfixModifierPosition: de
    } = u(C);
    if (T) {
      b = C + (b.length > 0 ? " " + b : b);
      continue;
    }
    let fe = !!de, se;
    if (fe) {
      const K = Q.substring(0, de);
      se = o(K);
      const U = se && h[se] ? o(Q) : void 0;
      U && U !== se && (se = U, fe = !1);
    } else
      se = o(Q);
    if (!se) {
      if (!fe) {
        b = C + (b.length > 0 ? " " + b : b);
        continue;
      }
      if (se = o(Q), !se) {
        b = C + (b.length > 0 ? " " + b : b);
        continue;
      }
      fe = !1;
    }
    const be = L.length === 0 ? "" : L.length === 1 ? L[0] : p(L).join(":"), Oe = G ? be + Ss : be, Le = Oe + se;
    if (v.indexOf(Le) > -1)
      continue;
    v.push(Le);
    const J = m(se, fe);
    for (let K = 0; K < J.length; ++K) {
      const U = J[K];
      v.push(Oe + U);
    }
    b = C + (b.length > 0 ? " " + b : b);
  }
  return b;
}, U2 = (...i) => {
  let f = 0, u, o, m = "";
  for (; f < i.length; )
    (u = i[f++]) && (o = Ld(u)) && (m && (m += " "), m += o);
  return m;
}, Ld = (i) => {
  if (typeof i == "string")
    return i;
  let f, u = "";
  for (let o = 0; o < i.length; o++)
    i[o] && (f = Ld(i[o])) && (u && (u += " "), u += f);
  return u;
}, q2 = (i, ...f) => {
  let u, o, m, p;
  const h = (x) => {
    const b = f.reduce((D, C) => C(D), i());
    return u = D2(b), o = u.cache.get, m = u.cache.set, p = v, v(x);
  }, v = (x) => {
    const b = o(x);
    if (b)
      return b;
    const D = L2(x, u);
    return m(x, D), D;
  };
  return p = h, (...x) => p(U2(...x));
}, B2 = [], st = (i) => {
  const f = (u) => u[i] || B2;
  return f.isThemeGetter = !0, f;
}, Ud = /^\[(?:(\w[\w-]*):)?(.+)\]$/i, qd = /^\((?:(\w[\w-]*):)?(.+)\)$/i, G2 = /^\d+(?:\.\d+)?\/\d+(?:\.\d+)?$/, Y2 = /^(\d+(\.\d+)?)?(xs|sm|md|lg|xl)$/, X2 = /\d+(%|px|r?em|[sdl]?v([hwib]|min|max)|pt|pc|in|cm|mm|cap|ch|ex|r?lh|cq(w|h|i|b|min|max))|\b(calc|min|max|clamp)\(.+\)|^0$/, Q2 = /^(rgba?|hsla?|hwb|(ok)?(lab|lch)|color-mix)\(.+\)$/, K2 = /^(inset_)?-?((\d+)?\.?(\d+)[a-z]+|0)_-?((\d+)?\.?(\d+)[a-z]+|0)/, J2 = /^(url|image|image-set|cross-fade|element|(repeating-)?(linear|radial|conic)-gradient)\(.+\)$/, ol = (i) => G2.test(i), we = (i) => !!i && !Number.isNaN(Number(i)), ma = (i) => !!i && Number.isInteger(Number(i)), gs = (i) => i.endsWith("%") && we(i.slice(0, -1)), Za = (i) => Y2.test(i), Bd = () => !0, $2 = (i) => (
  // `colorFunctionRegex` check is necessary because color functions can have percentages in them which which would be incorrectly classified as lengths.
  // For example, `hsl(0 0% 0%)` would be classified as a length without this check.
  // I could also use lookbehind assertion in `lengthUnitRegex` but that isn't supported widely enough.
  X2.test(i) && !Q2.test(i)
), Ns = () => !1, F2 = (i) => K2.test(i), W2 = (i) => J2.test(i), I2 = (i) => !W(i) && !I(i), P2 = (i) => i.startsWith("@container") && (i[10] === "/" && i[11] !== void 0 || i[11] === "s" && i[16] !== void 0 && i.startsWith("-size/", 10) || i[11] === "n" && i[18] !== void 0 && i.startsWith("-normal/", 10)), eg = (i) => sl(i, Xd, Ns), W = (i) => Ud.test(i), Nl = (i) => sl(i, Qd, $2), Xf = (i) => sl(i, cg, we), tg = (i) => sl(i, Jd, Bd), ag = (i) => sl(i, Kd, Ns), Qf = (i) => sl(i, Gd, Ns), lg = (i) => sl(i, Yd, W2), Ki = (i) => sl(i, $d, F2), I = (i) => qd.test(i), kr = (i) => Ol(i, Qd), ng = (i) => Ol(i, Kd), Kf = (i) => Ol(i, Gd), rg = (i) => Ol(i, Xd), ig = (i) => Ol(i, Yd), Ji = (i) => Ol(i, $d, !0), og = (i) => Ol(i, Jd, !0), sl = (i, f, u) => {
  const o = Ud.exec(i);
  return o ? o[1] ? f(o[1]) : u(o[2]) : !1;
}, Ol = (i, f, u = !1) => {
  const o = qd.exec(i);
  return o ? o[1] ? f(o[1]) : u : !1;
}, Gd = (i) => i === "position" || i === "percentage", Yd = (i) => i === "image" || i === "url", Xd = (i) => i === "length" || i === "size" || i === "bg-size", Qd = (i) => i === "length", cg = (i) => i === "number", Kd = (i) => i === "family-name", Jd = (i) => i === "number" || i === "weight", $d = (i) => i === "shadow", sg = () => {
  const i = st("color"), f = st("font"), u = st("text"), o = st("font-weight"), m = st("tracking"), p = st("leading"), h = st("breakpoint"), v = st("container"), x = st("spacing"), b = st("radius"), D = st("shadow"), C = st("inset-shadow"), T = st("text-shadow"), L = st("drop-shadow"), G = st("blur"), Q = st("perspective"), de = st("aspect"), fe = st("ease"), se = st("animate"), be = () => ["auto", "avoid", "all", "avoid-page", "page", "left", "right", "column"], Oe = () => [
    "center",
    "top",
    "bottom",
    "left",
    "right",
    "top-left",
    // Deprecated since Tailwind CSS v4.1.0, see https://github.com/tailwindlabs/tailwindcss/pull/17378
    "left-top",
    "top-right",
    // Deprecated since Tailwind CSS v4.1.0, see https://github.com/tailwindlabs/tailwindcss/pull/17378
    "right-top",
    "bottom-right",
    // Deprecated since Tailwind CSS v4.1.0, see https://github.com/tailwindlabs/tailwindcss/pull/17378
    "right-bottom",
    "bottom-left",
    // Deprecated since Tailwind CSS v4.1.0, see https://github.com/tailwindlabs/tailwindcss/pull/17378
    "left-bottom"
  ], Le = () => [...Oe(), I, W], J = () => ["auto", "hidden", "clip", "visible", "scroll"], K = () => ["auto", "contain", "none"], U = () => [I, W, x], xe = () => [ol, "full", "auto", ...U()], tt = () => [ma, "none", "subgrid", I, W], R = () => ["auto", {
    span: ["full", ma, I, W]
  }, ma, I, W], $ = () => [ma, "auto", I, W], ye = () => ["auto", "min", "max", "fr", I, W], he = () => ["start", "end", "center", "between", "around", "evenly", "stretch", "baseline", "center-safe", "end-safe"], Qe = () => ["start", "end", "center", "stretch", "center-safe", "end-safe"], z = () => ["auto", ...U()], q = () => [ol, "auto", "full", "dvw", "dvh", "lvw", "lvh", "svw", "svh", "min", "max", "fit", ...U()], ne = () => [ol, "screen", "full", "dvw", "lvw", "svw", "min", "max", "fit", ...U()], He = () => [ol, "screen", "full", "lh", "dvh", "lvh", "svh", "min", "max", "fit", ...U()], B = () => [i, I, W], S = () => [...Oe(), Kf, Qf, {
    position: [I, W]
  }], _ = () => ["no-repeat", {
    repeat: ["", "x", "y", "space", "round"]
  }], Y = () => ["auto", "cover", "contain", rg, eg, {
    size: [I, W]
  }], F = () => [gs, kr, Nl], te = () => [
    // Deprecated since Tailwind CSS v4.0.0
    "",
    "none",
    "full",
    b,
    I,
    W
  ], le = () => ["", we, kr, Nl], Se = () => ["solid", "dashed", "dotted", "double"], Ge = () => ["normal", "multiply", "screen", "overlay", "darken", "lighten", "color-dodge", "color-burn", "hard-light", "soft-light", "difference", "exclusion", "hue", "saturation", "color", "luminosity"], me = () => [we, gs, Kf, Qf], Ut = () => [
    // Deprecated since Tailwind CSS v4.0.0
    "",
    "none",
    G,
    I,
    W
  ], Et = () => ["none", we, I, W], vt = () => ["none", we, I, W], Ra = () => [we, I, W], Fe = () => [ol, "full", ...U()];
  return {
    cacheSize: 500,
    theme: {
      animate: ["spin", "ping", "pulse", "bounce"],
      aspect: ["video"],
      blur: [Za],
      breakpoint: [Za],
      color: [Bd],
      container: [Za],
      "drop-shadow": [Za],
      ease: ["in", "out", "in-out"],
      font: [I2],
      "font-weight": ["thin", "extralight", "light", "normal", "medium", "semibold", "bold", "extrabold", "black"],
      "inset-shadow": [Za],
      leading: ["none", "tight", "snug", "normal", "relaxed", "loose"],
      perspective: ["dramatic", "near", "normal", "midrange", "distant", "none"],
      radius: [Za],
      shadow: [Za],
      spacing: ["px", we],
      text: [Za],
      "text-shadow": [Za],
      tracking: ["tighter", "tight", "normal", "wide", "wider", "widest"]
    },
    classGroups: {
      // --------------
      // --- Layout ---
      // --------------
      /**
       * Aspect Ratio
       * @see https://tailwindcss.com/docs/aspect-ratio
       */
      aspect: [{
        aspect: ["auto", "square", ol, W, I, de]
      }],
      /**
       * Container
       * @see https://tailwindcss.com/docs/container
       * @deprecated since Tailwind CSS v4.0.0
       */
      container: ["container"],
      /**
       * Container Type
       * @see https://tailwindcss.com/docs/responsive-design#container-queries
       */
      "container-type": [{
        "@container": ["", "normal", "size", I, W]
      }],
      /**
       * Container Name
       * @see https://tailwindcss.com/docs/responsive-design#named-containers
       */
      "container-named": [P2],
      /**
       * Columns
       * @see https://tailwindcss.com/docs/columns
       */
      columns: [{
        columns: [we, W, I, v]
      }],
      /**
       * Break After
       * @see https://tailwindcss.com/docs/break-after
       */
      "break-after": [{
        "break-after": be()
      }],
      /**
       * Break Before
       * @see https://tailwindcss.com/docs/break-before
       */
      "break-before": [{
        "break-before": be()
      }],
      /**
       * Break Inside
       * @see https://tailwindcss.com/docs/break-inside
       */
      "break-inside": [{
        "break-inside": ["auto", "avoid", "avoid-page", "avoid-column"]
      }],
      /**
       * Box Decoration Break
       * @see https://tailwindcss.com/docs/box-decoration-break
       */
      "box-decoration": [{
        "box-decoration": ["slice", "clone"]
      }],
      /**
       * Box Sizing
       * @see https://tailwindcss.com/docs/box-sizing
       */
      box: [{
        box: ["border", "content"]
      }],
      /**
       * Display
       * @see https://tailwindcss.com/docs/display
       */
      display: ["block", "inline-block", "inline", "flex", "inline-flex", "table", "inline-table", "table-caption", "table-cell", "table-column", "table-column-group", "table-footer-group", "table-header-group", "table-row-group", "table-row", "flow-root", "grid", "inline-grid", "contents", "list-item", "hidden"],
      /**
       * Screen Reader Only
       * @see https://tailwindcss.com/docs/display#screen-reader-only
       */
      sr: ["sr-only", "not-sr-only"],
      /**
       * Floats
       * @see https://tailwindcss.com/docs/float
       */
      float: [{
        float: ["right", "left", "none", "start", "end"]
      }],
      /**
       * Clear
       * @see https://tailwindcss.com/docs/clear
       */
      clear: [{
        clear: ["left", "right", "both", "none", "start", "end"]
      }],
      /**
       * Isolation
       * @see https://tailwindcss.com/docs/isolation
       */
      isolation: ["isolate", "isolation-auto"],
      /**
       * Object Fit
       * @see https://tailwindcss.com/docs/object-fit
       */
      "object-fit": [{
        object: ["contain", "cover", "fill", "none", "scale-down"]
      }],
      /**
       * Object Position
       * @see https://tailwindcss.com/docs/object-position
       */
      "object-position": [{
        object: Le()
      }],
      /**
       * Overflow
       * @see https://tailwindcss.com/docs/overflow
       */
      overflow: [{
        overflow: J()
      }],
      /**
       * Overflow X
       * @see https://tailwindcss.com/docs/overflow
       */
      "overflow-x": [{
        "overflow-x": J()
      }],
      /**
       * Overflow Y
       * @see https://tailwindcss.com/docs/overflow
       */
      "overflow-y": [{
        "overflow-y": J()
      }],
      /**
       * Overscroll Behavior
       * @see https://tailwindcss.com/docs/overscroll-behavior
       */
      overscroll: [{
        overscroll: K()
      }],
      /**
       * Overscroll Behavior X
       * @see https://tailwindcss.com/docs/overscroll-behavior
       */
      "overscroll-x": [{
        "overscroll-x": K()
      }],
      /**
       * Overscroll Behavior Y
       * @see https://tailwindcss.com/docs/overscroll-behavior
       */
      "overscroll-y": [{
        "overscroll-y": K()
      }],
      /**
       * Position
       * @see https://tailwindcss.com/docs/position
       */
      position: ["static", "fixed", "absolute", "relative", "sticky"],
      /**
       * Inset
       * @see https://tailwindcss.com/docs/top-right-bottom-left
       */
      inset: [{
        inset: xe()
      }],
      /**
       * Inset Inline
       * @see https://tailwindcss.com/docs/top-right-bottom-left
       */
      "inset-x": [{
        "inset-x": xe()
      }],
      /**
       * Inset Block
       * @see https://tailwindcss.com/docs/top-right-bottom-left
       */
      "inset-y": [{
        "inset-y": xe()
      }],
      /**
       * Inset Inline Start
       * @see https://tailwindcss.com/docs/top-right-bottom-left
       * @todo class group will be renamed to `inset-s` in next major release
       */
      start: [{
        "inset-s": xe(),
        /**
         * @deprecated since Tailwind CSS v4.2.0 in favor of `inset-s-*` utilities.
         * @see https://github.com/tailwindlabs/tailwindcss/pull/19613
         */
        start: xe()
      }],
      /**
       * Inset Inline End
       * @see https://tailwindcss.com/docs/top-right-bottom-left
       * @todo class group will be renamed to `inset-e` in next major release
       */
      end: [{
        "inset-e": xe(),
        /**
         * @deprecated since Tailwind CSS v4.2.0 in favor of `inset-e-*` utilities.
         * @see https://github.com/tailwindlabs/tailwindcss/pull/19613
         */
        end: xe()
      }],
      /**
       * Inset Block Start
       * @see https://tailwindcss.com/docs/top-right-bottom-left
       */
      "inset-bs": [{
        "inset-bs": xe()
      }],
      /**
       * Inset Block End
       * @see https://tailwindcss.com/docs/top-right-bottom-left
       */
      "inset-be": [{
        "inset-be": xe()
      }],
      /**
       * Top
       * @see https://tailwindcss.com/docs/top-right-bottom-left
       */
      top: [{
        top: xe()
      }],
      /**
       * Right
       * @see https://tailwindcss.com/docs/top-right-bottom-left
       */
      right: [{
        right: xe()
      }],
      /**
       * Bottom
       * @see https://tailwindcss.com/docs/top-right-bottom-left
       */
      bottom: [{
        bottom: xe()
      }],
      /**
       * Left
       * @see https://tailwindcss.com/docs/top-right-bottom-left
       */
      left: [{
        left: xe()
      }],
      /**
       * Visibility
       * @see https://tailwindcss.com/docs/visibility
       */
      visibility: ["visible", "invisible", "collapse"],
      /**
       * Z-Index
       * @see https://tailwindcss.com/docs/z-index
       */
      z: [{
        z: [ma, "auto", I, W]
      }],
      // ------------------------
      // --- Flexbox and Grid ---
      // ------------------------
      /**
       * Flex Basis
       * @see https://tailwindcss.com/docs/flex-basis
       */
      basis: [{
        basis: [ol, "full", "auto", v, ...U()]
      }],
      /**
       * Flex Direction
       * @see https://tailwindcss.com/docs/flex-direction
       */
      "flex-direction": [{
        flex: ["row", "row-reverse", "col", "col-reverse"]
      }],
      /**
       * Flex Wrap
       * @see https://tailwindcss.com/docs/flex-wrap
       */
      "flex-wrap": [{
        flex: ["nowrap", "wrap", "wrap-reverse"]
      }],
      /**
       * Flex
       * @see https://tailwindcss.com/docs/flex
       */
      flex: [{
        flex: [we, ol, "auto", "initial", "none", W]
      }],
      /**
       * Flex Grow
       * @see https://tailwindcss.com/docs/flex-grow
       */
      grow: [{
        grow: ["", we, I, W]
      }],
      /**
       * Flex Shrink
       * @see https://tailwindcss.com/docs/flex-shrink
       */
      shrink: [{
        shrink: ["", we, I, W]
      }],
      /**
       * Order
       * @see https://tailwindcss.com/docs/order
       */
      order: [{
        order: [ma, "first", "last", "none", I, W]
      }],
      /**
       * Grid Template Columns
       * @see https://tailwindcss.com/docs/grid-template-columns
       */
      "grid-cols": [{
        "grid-cols": tt()
      }],
      /**
       * Grid Column Start / End
       * @see https://tailwindcss.com/docs/grid-column
       */
      "col-start-end": [{
        col: R()
      }],
      /**
       * Grid Column Start
       * @see https://tailwindcss.com/docs/grid-column
       */
      "col-start": [{
        "col-start": $()
      }],
      /**
       * Grid Column End
       * @see https://tailwindcss.com/docs/grid-column
       */
      "col-end": [{
        "col-end": $()
      }],
      /**
       * Grid Template Rows
       * @see https://tailwindcss.com/docs/grid-template-rows
       */
      "grid-rows": [{
        "grid-rows": tt()
      }],
      /**
       * Grid Row Start / End
       * @see https://tailwindcss.com/docs/grid-row
       */
      "row-start-end": [{
        row: R()
      }],
      /**
       * Grid Row Start
       * @see https://tailwindcss.com/docs/grid-row
       */
      "row-start": [{
        "row-start": $()
      }],
      /**
       * Grid Row End
       * @see https://tailwindcss.com/docs/grid-row
       */
      "row-end": [{
        "row-end": $()
      }],
      /**
       * Grid Auto Flow
       * @see https://tailwindcss.com/docs/grid-auto-flow
       */
      "grid-flow": [{
        "grid-flow": ["row", "col", "dense", "row-dense", "col-dense"]
      }],
      /**
       * Grid Auto Columns
       * @see https://tailwindcss.com/docs/grid-auto-columns
       */
      "auto-cols": [{
        "auto-cols": ye()
      }],
      /**
       * Grid Auto Rows
       * @see https://tailwindcss.com/docs/grid-auto-rows
       */
      "auto-rows": [{
        "auto-rows": ye()
      }],
      /**
       * Gap
       * @see https://tailwindcss.com/docs/gap
       */
      gap: [{
        gap: U()
      }],
      /**
       * Gap X
       * @see https://tailwindcss.com/docs/gap
       */
      "gap-x": [{
        "gap-x": U()
      }],
      /**
       * Gap Y
       * @see https://tailwindcss.com/docs/gap
       */
      "gap-y": [{
        "gap-y": U()
      }],
      /**
       * Justify Content
       * @see https://tailwindcss.com/docs/justify-content
       */
      "justify-content": [{
        justify: [...he(), "normal"]
      }],
      /**
       * Justify Items
       * @see https://tailwindcss.com/docs/justify-items
       */
      "justify-items": [{
        "justify-items": [...Qe(), "normal"]
      }],
      /**
       * Justify Self
       * @see https://tailwindcss.com/docs/justify-self
       */
      "justify-self": [{
        "justify-self": ["auto", ...Qe()]
      }],
      /**
       * Align Content
       * @see https://tailwindcss.com/docs/align-content
       */
      "align-content": [{
        content: ["normal", ...he()]
      }],
      /**
       * Align Items
       * @see https://tailwindcss.com/docs/align-items
       */
      "align-items": [{
        items: [...Qe(), {
          baseline: ["", "last"]
        }]
      }],
      /**
       * Align Self
       * @see https://tailwindcss.com/docs/align-self
       */
      "align-self": [{
        self: ["auto", ...Qe(), {
          baseline: ["", "last"]
        }]
      }],
      /**
       * Place Content
       * @see https://tailwindcss.com/docs/place-content
       */
      "place-content": [{
        "place-content": he()
      }],
      /**
       * Place Items
       * @see https://tailwindcss.com/docs/place-items
       */
      "place-items": [{
        "place-items": [...Qe(), "baseline"]
      }],
      /**
       * Place Self
       * @see https://tailwindcss.com/docs/place-self
       */
      "place-self": [{
        "place-self": ["auto", ...Qe()]
      }],
      // Spacing
      /**
       * Padding
       * @see https://tailwindcss.com/docs/padding
       */
      p: [{
        p: U()
      }],
      /**
       * Padding Inline
       * @see https://tailwindcss.com/docs/padding
       */
      px: [{
        px: U()
      }],
      /**
       * Padding Block
       * @see https://tailwindcss.com/docs/padding
       */
      py: [{
        py: U()
      }],
      /**
       * Padding Inline Start
       * @see https://tailwindcss.com/docs/padding
       */
      ps: [{
        ps: U()
      }],
      /**
       * Padding Inline End
       * @see https://tailwindcss.com/docs/padding
       */
      pe: [{
        pe: U()
      }],
      /**
       * Padding Block Start
       * @see https://tailwindcss.com/docs/padding
       */
      pbs: [{
        pbs: U()
      }],
      /**
       * Padding Block End
       * @see https://tailwindcss.com/docs/padding
       */
      pbe: [{
        pbe: U()
      }],
      /**
       * Padding Top
       * @see https://tailwindcss.com/docs/padding
       */
      pt: [{
        pt: U()
      }],
      /**
       * Padding Right
       * @see https://tailwindcss.com/docs/padding
       */
      pr: [{
        pr: U()
      }],
      /**
       * Padding Bottom
       * @see https://tailwindcss.com/docs/padding
       */
      pb: [{
        pb: U()
      }],
      /**
       * Padding Left
       * @see https://tailwindcss.com/docs/padding
       */
      pl: [{
        pl: U()
      }],
      /**
       * Margin
       * @see https://tailwindcss.com/docs/margin
       */
      m: [{
        m: z()
      }],
      /**
       * Margin Inline
       * @see https://tailwindcss.com/docs/margin
       */
      mx: [{
        mx: z()
      }],
      /**
       * Margin Block
       * @see https://tailwindcss.com/docs/margin
       */
      my: [{
        my: z()
      }],
      /**
       * Margin Inline Start
       * @see https://tailwindcss.com/docs/margin
       */
      ms: [{
        ms: z()
      }],
      /**
       * Margin Inline End
       * @see https://tailwindcss.com/docs/margin
       */
      me: [{
        me: z()
      }],
      /**
       * Margin Block Start
       * @see https://tailwindcss.com/docs/margin
       */
      mbs: [{
        mbs: z()
      }],
      /**
       * Margin Block End
       * @see https://tailwindcss.com/docs/margin
       */
      mbe: [{
        mbe: z()
      }],
      /**
       * Margin Top
       * @see https://tailwindcss.com/docs/margin
       */
      mt: [{
        mt: z()
      }],
      /**
       * Margin Right
       * @see https://tailwindcss.com/docs/margin
       */
      mr: [{
        mr: z()
      }],
      /**
       * Margin Bottom
       * @see https://tailwindcss.com/docs/margin
       */
      mb: [{
        mb: z()
      }],
      /**
       * Margin Left
       * @see https://tailwindcss.com/docs/margin
       */
      ml: [{
        ml: z()
      }],
      /**
       * Space Between X
       * @see https://tailwindcss.com/docs/margin#adding-space-between-children
       */
      "space-x": [{
        "space-x": U()
      }],
      /**
       * Space Between X Reverse
       * @see https://tailwindcss.com/docs/margin#adding-space-between-children
       */
      "space-x-reverse": ["space-x-reverse"],
      /**
       * Space Between Y
       * @see https://tailwindcss.com/docs/margin#adding-space-between-children
       */
      "space-y": [{
        "space-y": U()
      }],
      /**
       * Space Between Y Reverse
       * @see https://tailwindcss.com/docs/margin#adding-space-between-children
       */
      "space-y-reverse": ["space-y-reverse"],
      // --------------
      // --- Sizing ---
      // --------------
      /**
       * Size
       * @see https://tailwindcss.com/docs/width#setting-both-width-and-height
       */
      size: [{
        size: q()
      }],
      /**
       * Inline Size
       * @see https://tailwindcss.com/docs/width
       */
      "inline-size": [{
        inline: ["auto", ...ne()]
      }],
      /**
       * Min-Inline Size
       * @see https://tailwindcss.com/docs/min-width
       */
      "min-inline-size": [{
        "min-inline": ["auto", ...ne()]
      }],
      /**
       * Max-Inline Size
       * @see https://tailwindcss.com/docs/max-width
       */
      "max-inline-size": [{
        "max-inline": ["none", ...ne()]
      }],
      /**
       * Block Size
       * @see https://tailwindcss.com/docs/height
       */
      "block-size": [{
        block: ["auto", ...He()]
      }],
      /**
       * Min-Block Size
       * @see https://tailwindcss.com/docs/min-height
       */
      "min-block-size": [{
        "min-block": ["auto", ...He()]
      }],
      /**
       * Max-Block Size
       * @see https://tailwindcss.com/docs/max-height
       */
      "max-block-size": [{
        "max-block": ["none", ...He()]
      }],
      /**
       * Width
       * @see https://tailwindcss.com/docs/width
       */
      w: [{
        w: [v, "screen", ...q()]
      }],
      /**
       * Min-Width
       * @see https://tailwindcss.com/docs/min-width
       */
      "min-w": [{
        "min-w": [
          v,
          "screen",
          /** Deprecated. @see https://github.com/tailwindlabs/tailwindcss.com/issues/2027#issuecomment-2620152757 */
          "none",
          ...q()
        ]
      }],
      /**
       * Max-Width
       * @see https://tailwindcss.com/docs/max-width
       */
      "max-w": [{
        "max-w": [
          v,
          "screen",
          "none",
          /** Deprecated since Tailwind CSS v4.0.0. @see https://github.com/tailwindlabs/tailwindcss.com/issues/2027#issuecomment-2620152757 */
          "prose",
          /** Deprecated since Tailwind CSS v4.0.0. @see https://github.com/tailwindlabs/tailwindcss.com/issues/2027#issuecomment-2620152757 */
          {
            screen: [h]
          },
          ...q()
        ]
      }],
      /**
       * Height
       * @see https://tailwindcss.com/docs/height
       */
      h: [{
        h: ["screen", "lh", ...q()]
      }],
      /**
       * Min-Height
       * @see https://tailwindcss.com/docs/min-height
       */
      "min-h": [{
        "min-h": ["screen", "lh", "none", ...q()]
      }],
      /**
       * Max-Height
       * @see https://tailwindcss.com/docs/max-height
       */
      "max-h": [{
        "max-h": ["screen", "lh", ...q()]
      }],
      // ------------------
      // --- Typography ---
      // ------------------
      /**
       * Font Size
       * @see https://tailwindcss.com/docs/font-size
       */
      "font-size": [{
        text: ["base", u, kr, Nl]
      }],
      /**
       * Font Smoothing
       * @see https://tailwindcss.com/docs/font-smoothing
       */
      "font-smoothing": ["antialiased", "subpixel-antialiased"],
      /**
       * Font Style
       * @see https://tailwindcss.com/docs/font-style
       */
      "font-style": ["italic", "not-italic"],
      /**
       * Font Weight
       * @see https://tailwindcss.com/docs/font-weight
       */
      "font-weight": [{
        font: [o, og, tg]
      }],
      /**
       * Font Stretch
       * @see https://tailwindcss.com/docs/font-stretch
       */
      "font-stretch": [{
        "font-stretch": ["ultra-condensed", "extra-condensed", "condensed", "semi-condensed", "normal", "semi-expanded", "expanded", "extra-expanded", "ultra-expanded", gs, W]
      }],
      /**
       * Font Family
       * @see https://tailwindcss.com/docs/font-family
       */
      "font-family": [{
        font: [ng, ag, f]
      }],
      /**
       * Font Feature Settings
       * @see https://tailwindcss.com/docs/font-feature-settings
       */
      "font-features": [{
        "font-features": [W]
      }],
      /**
       * Font Variant Numeric
       * @see https://tailwindcss.com/docs/font-variant-numeric
       */
      "fvn-normal": ["normal-nums"],
      /**
       * Font Variant Numeric
       * @see https://tailwindcss.com/docs/font-variant-numeric
       */
      "fvn-ordinal": ["ordinal"],
      /**
       * Font Variant Numeric
       * @see https://tailwindcss.com/docs/font-variant-numeric
       */
      "fvn-slashed-zero": ["slashed-zero"],
      /**
       * Font Variant Numeric
       * @see https://tailwindcss.com/docs/font-variant-numeric
       */
      "fvn-figure": ["lining-nums", "oldstyle-nums"],
      /**
       * Font Variant Numeric
       * @see https://tailwindcss.com/docs/font-variant-numeric
       */
      "fvn-spacing": ["proportional-nums", "tabular-nums"],
      /**
       * Font Variant Numeric
       * @see https://tailwindcss.com/docs/font-variant-numeric
       */
      "fvn-fraction": ["diagonal-fractions", "stacked-fractions"],
      /**
       * Letter Spacing
       * @see https://tailwindcss.com/docs/letter-spacing
       */
      tracking: [{
        tracking: [m, I, W]
      }],
      /**
       * Line Clamp
       * @see https://tailwindcss.com/docs/line-clamp
       */
      "line-clamp": [{
        "line-clamp": [we, "none", I, Xf]
      }],
      /**
       * Line Height
       * @see https://tailwindcss.com/docs/line-height
       */
      leading: [{
        leading: [
          /** Deprecated since Tailwind CSS v4.0.0. @see https://github.com/tailwindlabs/tailwindcss.com/issues/2027#issuecomment-2620152757 */
          p,
          ...U()
        ]
      }],
      /**
       * List Style Image
       * @see https://tailwindcss.com/docs/list-style-image
       */
      "list-image": [{
        "list-image": ["none", I, W]
      }],
      /**
       * List Style Position
       * @see https://tailwindcss.com/docs/list-style-position
       */
      "list-style-position": [{
        list: ["inside", "outside"]
      }],
      /**
       * List Style Type
       * @see https://tailwindcss.com/docs/list-style-type
       */
      "list-style-type": [{
        list: ["disc", "decimal", "none", I, W]
      }],
      /**
       * Text Alignment
       * @see https://tailwindcss.com/docs/text-align
       */
      "text-alignment": [{
        text: ["left", "center", "right", "justify", "start", "end"]
      }],
      /**
       * Placeholder Color
       * @deprecated since Tailwind CSS v3.0.0
       * @see https://v3.tailwindcss.com/docs/placeholder-color
       */
      "placeholder-color": [{
        placeholder: B()
      }],
      /**
       * Text Color
       * @see https://tailwindcss.com/docs/text-color
       */
      "text-color": [{
        text: B()
      }],
      /**
       * Text Decoration
       * @see https://tailwindcss.com/docs/text-decoration
       */
      "text-decoration": ["underline", "overline", "line-through", "no-underline"],
      /**
       * Text Decoration Style
       * @see https://tailwindcss.com/docs/text-decoration-style
       */
      "text-decoration-style": [{
        decoration: [...Se(), "wavy"]
      }],
      /**
       * Text Decoration Thickness
       * @see https://tailwindcss.com/docs/text-decoration-thickness
       */
      "text-decoration-thickness": [{
        decoration: [we, "from-font", "auto", I, Nl]
      }],
      /**
       * Text Decoration Color
       * @see https://tailwindcss.com/docs/text-decoration-color
       */
      "text-decoration-color": [{
        decoration: B()
      }],
      /**
       * Text Underline Offset
       * @see https://tailwindcss.com/docs/text-underline-offset
       */
      "underline-offset": [{
        "underline-offset": [we, "auto", I, W]
      }],
      /**
       * Text Transform
       * @see https://tailwindcss.com/docs/text-transform
       */
      "text-transform": ["uppercase", "lowercase", "capitalize", "normal-case"],
      /**
       * Text Overflow
       * @see https://tailwindcss.com/docs/text-overflow
       */
      "text-overflow": ["truncate", "text-ellipsis", "text-clip"],
      /**
       * Text Wrap
       * @see https://tailwindcss.com/docs/text-wrap
       */
      "text-wrap": [{
        text: ["wrap", "nowrap", "balance", "pretty"]
      }],
      /**
       * Text Indent
       * @see https://tailwindcss.com/docs/text-indent
       */
      indent: [{
        indent: U()
      }],
      /**
       * Tab Size
       * @see https://tailwindcss.com/docs/tab-size
       */
      "tab-size": [{
        tab: [ma, I, W]
      }],
      /**
       * Vertical Alignment
       * @see https://tailwindcss.com/docs/vertical-align
       */
      "vertical-align": [{
        align: ["baseline", "top", "middle", "bottom", "text-top", "text-bottom", "sub", "super", I, W]
      }],
      /**
       * Whitespace
       * @see https://tailwindcss.com/docs/whitespace
       */
      whitespace: [{
        whitespace: ["normal", "nowrap", "pre", "pre-line", "pre-wrap", "break-spaces"]
      }],
      /**
       * Word Break
       * @see https://tailwindcss.com/docs/word-break
       */
      break: [{
        break: ["normal", "words", "all", "keep"]
      }],
      /**
       * Overflow Wrap
       * @see https://tailwindcss.com/docs/overflow-wrap
       */
      wrap: [{
        wrap: ["break-word", "anywhere", "normal"]
      }],
      /**
       * Hyphens
       * @see https://tailwindcss.com/docs/hyphens
       */
      hyphens: [{
        hyphens: ["none", "manual", "auto"]
      }],
      /**
       * Content
       * @see https://tailwindcss.com/docs/content
       */
      content: [{
        content: ["none", I, W]
      }],
      // -------------------
      // --- Backgrounds ---
      // -------------------
      /**
       * Background Attachment
       * @see https://tailwindcss.com/docs/background-attachment
       */
      "bg-attachment": [{
        bg: ["fixed", "local", "scroll"]
      }],
      /**
       * Background Clip
       * @see https://tailwindcss.com/docs/background-clip
       */
      "bg-clip": [{
        "bg-clip": ["border", "padding", "content", "text"]
      }],
      /**
       * Background Origin
       * @see https://tailwindcss.com/docs/background-origin
       */
      "bg-origin": [{
        "bg-origin": ["border", "padding", "content"]
      }],
      /**
       * Background Position
       * @see https://tailwindcss.com/docs/background-position
       */
      "bg-position": [{
        bg: S()
      }],
      /**
       * Background Repeat
       * @see https://tailwindcss.com/docs/background-repeat
       */
      "bg-repeat": [{
        bg: _()
      }],
      /**
       * Background Size
       * @see https://tailwindcss.com/docs/background-size
       */
      "bg-size": [{
        bg: Y()
      }],
      /**
       * Background Image
       * @see https://tailwindcss.com/docs/background-image
       */
      "bg-image": [{
        bg: ["none", {
          linear: [{
            to: ["t", "tr", "r", "br", "b", "bl", "l", "tl"]
          }, ma, I, W],
          radial: ["", I, W],
          conic: [ma, I, W]
        }, ig, lg]
      }],
      /**
       * Background Color
       * @see https://tailwindcss.com/docs/background-color
       */
      "bg-color": [{
        bg: B()
      }],
      /**
       * Gradient Color Stops From Position
       * @see https://tailwindcss.com/docs/gradient-color-stops
       */
      "gradient-from-pos": [{
        from: F()
      }],
      /**
       * Gradient Color Stops Via Position
       * @see https://tailwindcss.com/docs/gradient-color-stops
       */
      "gradient-via-pos": [{
        via: F()
      }],
      /**
       * Gradient Color Stops To Position
       * @see https://tailwindcss.com/docs/gradient-color-stops
       */
      "gradient-to-pos": [{
        to: F()
      }],
      /**
       * Gradient Color Stops From
       * @see https://tailwindcss.com/docs/gradient-color-stops
       */
      "gradient-from": [{
        from: B()
      }],
      /**
       * Gradient Color Stops Via
       * @see https://tailwindcss.com/docs/gradient-color-stops
       */
      "gradient-via": [{
        via: B()
      }],
      /**
       * Gradient Color Stops To
       * @see https://tailwindcss.com/docs/gradient-color-stops
       */
      "gradient-to": [{
        to: B()
      }],
      // ---------------
      // --- Borders ---
      // ---------------
      /**
       * Border Radius
       * @see https://tailwindcss.com/docs/border-radius
       */
      rounded: [{
        rounded: te()
      }],
      /**
       * Border Radius Start
       * @see https://tailwindcss.com/docs/border-radius
       */
      "rounded-s": [{
        "rounded-s": te()
      }],
      /**
       * Border Radius End
       * @see https://tailwindcss.com/docs/border-radius
       */
      "rounded-e": [{
        "rounded-e": te()
      }],
      /**
       * Border Radius Top
       * @see https://tailwindcss.com/docs/border-radius
       */
      "rounded-t": [{
        "rounded-t": te()
      }],
      /**
       * Border Radius Right
       * @see https://tailwindcss.com/docs/border-radius
       */
      "rounded-r": [{
        "rounded-r": te()
      }],
      /**
       * Border Radius Bottom
       * @see https://tailwindcss.com/docs/border-radius
       */
      "rounded-b": [{
        "rounded-b": te()
      }],
      /**
       * Border Radius Left
       * @see https://tailwindcss.com/docs/border-radius
       */
      "rounded-l": [{
        "rounded-l": te()
      }],
      /**
       * Border Radius Start Start
       * @see https://tailwindcss.com/docs/border-radius
       */
      "rounded-ss": [{
        "rounded-ss": te()
      }],
      /**
       * Border Radius Start End
       * @see https://tailwindcss.com/docs/border-radius
       */
      "rounded-se": [{
        "rounded-se": te()
      }],
      /**
       * Border Radius End End
       * @see https://tailwindcss.com/docs/border-radius
       */
      "rounded-ee": [{
        "rounded-ee": te()
      }],
      /**
       * Border Radius End Start
       * @see https://tailwindcss.com/docs/border-radius
       */
      "rounded-es": [{
        "rounded-es": te()
      }],
      /**
       * Border Radius Top Left
       * @see https://tailwindcss.com/docs/border-radius
       */
      "rounded-tl": [{
        "rounded-tl": te()
      }],
      /**
       * Border Radius Top Right
       * @see https://tailwindcss.com/docs/border-radius
       */
      "rounded-tr": [{
        "rounded-tr": te()
      }],
      /**
       * Border Radius Bottom Right
       * @see https://tailwindcss.com/docs/border-radius
       */
      "rounded-br": [{
        "rounded-br": te()
      }],
      /**
       * Border Radius Bottom Left
       * @see https://tailwindcss.com/docs/border-radius
       */
      "rounded-bl": [{
        "rounded-bl": te()
      }],
      /**
       * Border Width
       * @see https://tailwindcss.com/docs/border-width
       */
      "border-w": [{
        border: le()
      }],
      /**
       * Border Width Inline
       * @see https://tailwindcss.com/docs/border-width
       */
      "border-w-x": [{
        "border-x": le()
      }],
      /**
       * Border Width Block
       * @see https://tailwindcss.com/docs/border-width
       */
      "border-w-y": [{
        "border-y": le()
      }],
      /**
       * Border Width Inline Start
       * @see https://tailwindcss.com/docs/border-width
       */
      "border-w-s": [{
        "border-s": le()
      }],
      /**
       * Border Width Inline End
       * @see https://tailwindcss.com/docs/border-width
       */
      "border-w-e": [{
        "border-e": le()
      }],
      /**
       * Border Width Block Start
       * @see https://tailwindcss.com/docs/border-width
       */
      "border-w-bs": [{
        "border-bs": le()
      }],
      /**
       * Border Width Block End
       * @see https://tailwindcss.com/docs/border-width
       */
      "border-w-be": [{
        "border-be": le()
      }],
      /**
       * Border Width Top
       * @see https://tailwindcss.com/docs/border-width
       */
      "border-w-t": [{
        "border-t": le()
      }],
      /**
       * Border Width Right
       * @see https://tailwindcss.com/docs/border-width
       */
      "border-w-r": [{
        "border-r": le()
      }],
      /**
       * Border Width Bottom
       * @see https://tailwindcss.com/docs/border-width
       */
      "border-w-b": [{
        "border-b": le()
      }],
      /**
       * Border Width Left
       * @see https://tailwindcss.com/docs/border-width
       */
      "border-w-l": [{
        "border-l": le()
      }],
      /**
       * Divide Width X
       * @see https://tailwindcss.com/docs/border-width#between-children
       */
      "divide-x": [{
        "divide-x": le()
      }],
      /**
       * Divide Width X Reverse
       * @see https://tailwindcss.com/docs/border-width#between-children
       */
      "divide-x-reverse": ["divide-x-reverse"],
      /**
       * Divide Width Y
       * @see https://tailwindcss.com/docs/border-width#between-children
       */
      "divide-y": [{
        "divide-y": le()
      }],
      /**
       * Divide Width Y Reverse
       * @see https://tailwindcss.com/docs/border-width#between-children
       */
      "divide-y-reverse": ["divide-y-reverse"],
      /**
       * Border Style
       * @see https://tailwindcss.com/docs/border-style
       */
      "border-style": [{
        border: [...Se(), "hidden", "none"]
      }],
      /**
       * Divide Style
       * @see https://tailwindcss.com/docs/border-style#setting-the-divider-style
       */
      "divide-style": [{
        divide: [...Se(), "hidden", "none"]
      }],
      /**
       * Border Color
       * @see https://tailwindcss.com/docs/border-color
       */
      "border-color": [{
        border: B()
      }],
      /**
       * Border Color Inline
       * @see https://tailwindcss.com/docs/border-color
       */
      "border-color-x": [{
        "border-x": B()
      }],
      /**
       * Border Color Block
       * @see https://tailwindcss.com/docs/border-color
       */
      "border-color-y": [{
        "border-y": B()
      }],
      /**
       * Border Color Inline Start
       * @see https://tailwindcss.com/docs/border-color
       */
      "border-color-s": [{
        "border-s": B()
      }],
      /**
       * Border Color Inline End
       * @see https://tailwindcss.com/docs/border-color
       */
      "border-color-e": [{
        "border-e": B()
      }],
      /**
       * Border Color Block Start
       * @see https://tailwindcss.com/docs/border-color
       */
      "border-color-bs": [{
        "border-bs": B()
      }],
      /**
       * Border Color Block End
       * @see https://tailwindcss.com/docs/border-color
       */
      "border-color-be": [{
        "border-be": B()
      }],
      /**
       * Border Color Top
       * @see https://tailwindcss.com/docs/border-color
       */
      "border-color-t": [{
        "border-t": B()
      }],
      /**
       * Border Color Right
       * @see https://tailwindcss.com/docs/border-color
       */
      "border-color-r": [{
        "border-r": B()
      }],
      /**
       * Border Color Bottom
       * @see https://tailwindcss.com/docs/border-color
       */
      "border-color-b": [{
        "border-b": B()
      }],
      /**
       * Border Color Left
       * @see https://tailwindcss.com/docs/border-color
       */
      "border-color-l": [{
        "border-l": B()
      }],
      /**
       * Divide Color
       * @see https://tailwindcss.com/docs/divide-color
       */
      "divide-color": [{
        divide: B()
      }],
      /**
       * Outline Style
       * @see https://tailwindcss.com/docs/outline-style
       */
      "outline-style": [{
        outline: [...Se(), "none", "hidden"]
      }],
      /**
       * Outline Offset
       * @see https://tailwindcss.com/docs/outline-offset
       */
      "outline-offset": [{
        "outline-offset": [we, I, W]
      }],
      /**
       * Outline Width
       * @see https://tailwindcss.com/docs/outline-width
       */
      "outline-w": [{
        outline: ["", we, kr, Nl]
      }],
      /**
       * Outline Color
       * @see https://tailwindcss.com/docs/outline-color
       */
      "outline-color": [{
        outline: B()
      }],
      // ---------------
      // --- Effects ---
      // ---------------
      /**
       * Box Shadow
       * @see https://tailwindcss.com/docs/box-shadow
       */
      shadow: [{
        shadow: [
          // Deprecated since Tailwind CSS v4.0.0
          "",
          "none",
          D,
          Ji,
          Ki
        ]
      }],
      /**
       * Box Shadow Color
       * @see https://tailwindcss.com/docs/box-shadow#setting-the-shadow-color
       */
      "shadow-color": [{
        shadow: B()
      }],
      /**
       * Inset Box Shadow
       * @see https://tailwindcss.com/docs/box-shadow#adding-an-inset-shadow
       */
      "inset-shadow": [{
        "inset-shadow": ["none", C, Ji, Ki]
      }],
      /**
       * Inset Box Shadow Color
       * @see https://tailwindcss.com/docs/box-shadow#setting-the-inset-shadow-color
       */
      "inset-shadow-color": [{
        "inset-shadow": B()
      }],
      /**
       * Ring Width
       * @see https://tailwindcss.com/docs/box-shadow#adding-a-ring
       */
      "ring-w": [{
        ring: le()
      }],
      /**
       * Ring Width Inset
       * @see https://v3.tailwindcss.com/docs/ring-width#inset-rings
       * @deprecated since Tailwind CSS v4.0.0
       * @see https://github.com/tailwindlabs/tailwindcss/blob/v4.0.0/packages/tailwindcss/src/utilities.ts#L4158
       */
      "ring-w-inset": ["ring-inset"],
      /**
       * Ring Color
       * @see https://tailwindcss.com/docs/box-shadow#setting-the-ring-color
       */
      "ring-color": [{
        ring: B()
      }],
      /**
       * Ring Offset Width
       * @see https://v3.tailwindcss.com/docs/ring-offset-width
       * @deprecated since Tailwind CSS v4.0.0
       * @see https://github.com/tailwindlabs/tailwindcss/blob/v4.0.0/packages/tailwindcss/src/utilities.ts#L4158
       */
      "ring-offset-w": [{
        "ring-offset": [we, Nl]
      }],
      /**
       * Ring Offset Color
       * @see https://v3.tailwindcss.com/docs/ring-offset-color
       * @deprecated since Tailwind CSS v4.0.0
       * @see https://github.com/tailwindlabs/tailwindcss/blob/v4.0.0/packages/tailwindcss/src/utilities.ts#L4158
       */
      "ring-offset-color": [{
        "ring-offset": B()
      }],
      /**
       * Inset Ring Width
       * @see https://tailwindcss.com/docs/box-shadow#adding-an-inset-ring
       */
      "inset-ring-w": [{
        "inset-ring": le()
      }],
      /**
       * Inset Ring Color
       * @see https://tailwindcss.com/docs/box-shadow#setting-the-inset-ring-color
       */
      "inset-ring-color": [{
        "inset-ring": B()
      }],
      /**
       * Text Shadow
       * @see https://tailwindcss.com/docs/text-shadow
       */
      "text-shadow": [{
        "text-shadow": ["none", T, Ji, Ki]
      }],
      /**
       * Text Shadow Color
       * @see https://tailwindcss.com/docs/text-shadow#setting-the-shadow-color
       */
      "text-shadow-color": [{
        "text-shadow": B()
      }],
      /**
       * Opacity
       * @see https://tailwindcss.com/docs/opacity
       */
      opacity: [{
        opacity: [we, I, W]
      }],
      /**
       * Mix Blend Mode
       * @see https://tailwindcss.com/docs/mix-blend-mode
       */
      "mix-blend": [{
        "mix-blend": [...Ge(), "plus-darker", "plus-lighter"]
      }],
      /**
       * Background Blend Mode
       * @see https://tailwindcss.com/docs/background-blend-mode
       */
      "bg-blend": [{
        "bg-blend": Ge()
      }],
      /**
       * Mask Clip
       * @see https://tailwindcss.com/docs/mask-clip
       */
      "mask-clip": [{
        "mask-clip": ["border", "padding", "content", "fill", "stroke", "view"]
      }, "mask-no-clip"],
      /**
       * Mask Composite
       * @see https://tailwindcss.com/docs/mask-composite
       */
      "mask-composite": [{
        mask: ["add", "subtract", "intersect", "exclude"]
      }],
      /**
       * Mask Image
       * @see https://tailwindcss.com/docs/mask-image
       */
      "mask-image-linear-pos": [{
        "mask-linear": [we]
      }],
      "mask-image-linear-from-pos": [{
        "mask-linear-from": me()
      }],
      "mask-image-linear-to-pos": [{
        "mask-linear-to": me()
      }],
      "mask-image-linear-from-color": [{
        "mask-linear-from": B()
      }],
      "mask-image-linear-to-color": [{
        "mask-linear-to": B()
      }],
      "mask-image-t-from-pos": [{
        "mask-t-from": me()
      }],
      "mask-image-t-to-pos": [{
        "mask-t-to": me()
      }],
      "mask-image-t-from-color": [{
        "mask-t-from": B()
      }],
      "mask-image-t-to-color": [{
        "mask-t-to": B()
      }],
      "mask-image-r-from-pos": [{
        "mask-r-from": me()
      }],
      "mask-image-r-to-pos": [{
        "mask-r-to": me()
      }],
      "mask-image-r-from-color": [{
        "mask-r-from": B()
      }],
      "mask-image-r-to-color": [{
        "mask-r-to": B()
      }],
      "mask-image-b-from-pos": [{
        "mask-b-from": me()
      }],
      "mask-image-b-to-pos": [{
        "mask-b-to": me()
      }],
      "mask-image-b-from-color": [{
        "mask-b-from": B()
      }],
      "mask-image-b-to-color": [{
        "mask-b-to": B()
      }],
      "mask-image-l-from-pos": [{
        "mask-l-from": me()
      }],
      "mask-image-l-to-pos": [{
        "mask-l-to": me()
      }],
      "mask-image-l-from-color": [{
        "mask-l-from": B()
      }],
      "mask-image-l-to-color": [{
        "mask-l-to": B()
      }],
      "mask-image-x-from-pos": [{
        "mask-x-from": me()
      }],
      "mask-image-x-to-pos": [{
        "mask-x-to": me()
      }],
      "mask-image-x-from-color": [{
        "mask-x-from": B()
      }],
      "mask-image-x-to-color": [{
        "mask-x-to": B()
      }],
      "mask-image-y-from-pos": [{
        "mask-y-from": me()
      }],
      "mask-image-y-to-pos": [{
        "mask-y-to": me()
      }],
      "mask-image-y-from-color": [{
        "mask-y-from": B()
      }],
      "mask-image-y-to-color": [{
        "mask-y-to": B()
      }],
      "mask-image-radial": [{
        "mask-radial": [I, W]
      }],
      "mask-image-radial-from-pos": [{
        "mask-radial-from": me()
      }],
      "mask-image-radial-to-pos": [{
        "mask-radial-to": me()
      }],
      "mask-image-radial-from-color": [{
        "mask-radial-from": B()
      }],
      "mask-image-radial-to-color": [{
        "mask-radial-to": B()
      }],
      "mask-image-radial-shape": [{
        "mask-radial": ["circle", "ellipse"]
      }],
      "mask-image-radial-size": [{
        "mask-radial": [{
          closest: ["side", "corner"],
          farthest: ["side", "corner"]
        }]
      }],
      "mask-image-radial-pos": [{
        "mask-radial-at": Oe()
      }],
      "mask-image-conic-pos": [{
        "mask-conic": [we]
      }],
      "mask-image-conic-from-pos": [{
        "mask-conic-from": me()
      }],
      "mask-image-conic-to-pos": [{
        "mask-conic-to": me()
      }],
      "mask-image-conic-from-color": [{
        "mask-conic-from": B()
      }],
      "mask-image-conic-to-color": [{
        "mask-conic-to": B()
      }],
      /**
       * Mask Mode
       * @see https://tailwindcss.com/docs/mask-mode
       */
      "mask-mode": [{
        mask: ["alpha", "luminance", "match"]
      }],
      /**
       * Mask Origin
       * @see https://tailwindcss.com/docs/mask-origin
       */
      "mask-origin": [{
        "mask-origin": ["border", "padding", "content", "fill", "stroke", "view"]
      }],
      /**
       * Mask Position
       * @see https://tailwindcss.com/docs/mask-position
       */
      "mask-position": [{
        mask: S()
      }],
      /**
       * Mask Repeat
       * @see https://tailwindcss.com/docs/mask-repeat
       */
      "mask-repeat": [{
        mask: _()
      }],
      /**
       * Mask Size
       * @see https://tailwindcss.com/docs/mask-size
       */
      "mask-size": [{
        mask: Y()
      }],
      /**
       * Mask Type
       * @see https://tailwindcss.com/docs/mask-type
       */
      "mask-type": [{
        "mask-type": ["alpha", "luminance"]
      }],
      /**
       * Mask Image
       * @see https://tailwindcss.com/docs/mask-image
       */
      "mask-image": [{
        mask: ["none", I, W]
      }],
      // ---------------
      // --- Filters ---
      // ---------------
      /**
       * Filter
       * @see https://tailwindcss.com/docs/filter
       */
      filter: [{
        filter: [
          // Deprecated since Tailwind CSS v3.0.0
          "",
          "none",
          I,
          W
        ]
      }],
      /**
       * Blur
       * @see https://tailwindcss.com/docs/blur
       */
      blur: [{
        blur: Ut()
      }],
      /**
       * Brightness
       * @see https://tailwindcss.com/docs/brightness
       */
      brightness: [{
        brightness: [we, I, W]
      }],
      /**
       * Contrast
       * @see https://tailwindcss.com/docs/contrast
       */
      contrast: [{
        contrast: [we, I, W]
      }],
      /**
       * Drop Shadow
       * @see https://tailwindcss.com/docs/drop-shadow
       */
      "drop-shadow": [{
        "drop-shadow": [
          // Deprecated since Tailwind CSS v4.0.0
          "",
          "none",
          L,
          Ji,
          Ki
        ]
      }],
      /**
       * Drop Shadow Color
       * @see https://tailwindcss.com/docs/filter-drop-shadow#setting-the-shadow-color
       */
      "drop-shadow-color": [{
        "drop-shadow": B()
      }],
      /**
       * Grayscale
       * @see https://tailwindcss.com/docs/grayscale
       */
      grayscale: [{
        grayscale: ["", we, I, W]
      }],
      /**
       * Hue Rotate
       * @see https://tailwindcss.com/docs/hue-rotate
       */
      "hue-rotate": [{
        "hue-rotate": [we, I, W]
      }],
      /**
       * Invert
       * @see https://tailwindcss.com/docs/invert
       */
      invert: [{
        invert: ["", we, I, W]
      }],
      /**
       * Saturate
       * @see https://tailwindcss.com/docs/saturate
       */
      saturate: [{
        saturate: [we, I, W]
      }],
      /**
       * Sepia
       * @see https://tailwindcss.com/docs/sepia
       */
      sepia: [{
        sepia: ["", we, I, W]
      }],
      /**
       * Backdrop Filter
       * @see https://tailwindcss.com/docs/backdrop-filter
       */
      "backdrop-filter": [{
        "backdrop-filter": [
          // Deprecated since Tailwind CSS v3.0.0
          "",
          "none",
          I,
          W
        ]
      }],
      /**
       * Backdrop Blur
       * @see https://tailwindcss.com/docs/backdrop-blur
       */
      "backdrop-blur": [{
        "backdrop-blur": Ut()
      }],
      /**
       * Backdrop Brightness
       * @see https://tailwindcss.com/docs/backdrop-brightness
       */
      "backdrop-brightness": [{
        "backdrop-brightness": [we, I, W]
      }],
      /**
       * Backdrop Contrast
       * @see https://tailwindcss.com/docs/backdrop-contrast
       */
      "backdrop-contrast": [{
        "backdrop-contrast": [we, I, W]
      }],
      /**
       * Backdrop Grayscale
       * @see https://tailwindcss.com/docs/backdrop-grayscale
       */
      "backdrop-grayscale": [{
        "backdrop-grayscale": ["", we, I, W]
      }],
      /**
       * Backdrop Hue Rotate
       * @see https://tailwindcss.com/docs/backdrop-hue-rotate
       */
      "backdrop-hue-rotate": [{
        "backdrop-hue-rotate": [we, I, W]
      }],
      /**
       * Backdrop Invert
       * @see https://tailwindcss.com/docs/backdrop-invert
       */
      "backdrop-invert": [{
        "backdrop-invert": ["", we, I, W]
      }],
      /**
       * Backdrop Opacity
       * @see https://tailwindcss.com/docs/backdrop-opacity
       */
      "backdrop-opacity": [{
        "backdrop-opacity": [we, I, W]
      }],
      /**
       * Backdrop Saturate
       * @see https://tailwindcss.com/docs/backdrop-saturate
       */
      "backdrop-saturate": [{
        "backdrop-saturate": [we, I, W]
      }],
      /**
       * Backdrop Sepia
       * @see https://tailwindcss.com/docs/backdrop-sepia
       */
      "backdrop-sepia": [{
        "backdrop-sepia": ["", we, I, W]
      }],
      // --------------
      // --- Tables ---
      // --------------
      /**
       * Border Collapse
       * @see https://tailwindcss.com/docs/border-collapse
       */
      "border-collapse": [{
        border: ["collapse", "separate"]
      }],
      /**
       * Border Spacing
       * @see https://tailwindcss.com/docs/border-spacing
       */
      "border-spacing": [{
        "border-spacing": U()
      }],
      /**
       * Border Spacing X
       * @see https://tailwindcss.com/docs/border-spacing
       */
      "border-spacing-x": [{
        "border-spacing-x": U()
      }],
      /**
       * Border Spacing Y
       * @see https://tailwindcss.com/docs/border-spacing
       */
      "border-spacing-y": [{
        "border-spacing-y": U()
      }],
      /**
       * Table Layout
       * @see https://tailwindcss.com/docs/table-layout
       */
      "table-layout": [{
        table: ["auto", "fixed"]
      }],
      /**
       * Caption Side
       * @see https://tailwindcss.com/docs/caption-side
       */
      caption: [{
        caption: ["top", "bottom"]
      }],
      // ---------------------------------
      // --- Transitions and Animation ---
      // ---------------------------------
      /**
       * Transition Property
       * @see https://tailwindcss.com/docs/transition-property
       */
      transition: [{
        transition: ["", "all", "colors", "opacity", "shadow", "transform", "none", I, W]
      }],
      /**
       * Transition Behavior
       * @see https://tailwindcss.com/docs/transition-behavior
       */
      "transition-behavior": [{
        transition: ["normal", "discrete"]
      }],
      /**
       * Transition Duration
       * @see https://tailwindcss.com/docs/transition-duration
       */
      duration: [{
        duration: [we, "initial", I, W]
      }],
      /**
       * Transition Timing Function
       * @see https://tailwindcss.com/docs/transition-timing-function
       */
      ease: [{
        ease: ["linear", "initial", fe, I, W]
      }],
      /**
       * Transition Delay
       * @see https://tailwindcss.com/docs/transition-delay
       */
      delay: [{
        delay: [we, I, W]
      }],
      /**
       * Animation
       * @see https://tailwindcss.com/docs/animation
       */
      animate: [{
        animate: ["none", se, I, W]
      }],
      // ------------------
      // --- Transforms ---
      // ------------------
      /**
       * Backface Visibility
       * @see https://tailwindcss.com/docs/backface-visibility
       */
      backface: [{
        backface: ["hidden", "visible"]
      }],
      /**
       * Perspective
       * @see https://tailwindcss.com/docs/perspective
       */
      perspective: [{
        perspective: [Q, I, W]
      }],
      /**
       * Perspective Origin
       * @see https://tailwindcss.com/docs/perspective-origin
       */
      "perspective-origin": [{
        "perspective-origin": Le()
      }],
      /**
       * Rotate
       * @see https://tailwindcss.com/docs/rotate
       */
      rotate: [{
        rotate: Et()
      }],
      /**
       * Rotate X
       * @see https://tailwindcss.com/docs/rotate
       */
      "rotate-x": [{
        "rotate-x": Et()
      }],
      /**
       * Rotate Y
       * @see https://tailwindcss.com/docs/rotate
       */
      "rotate-y": [{
        "rotate-y": Et()
      }],
      /**
       * Rotate Z
       * @see https://tailwindcss.com/docs/rotate
       */
      "rotate-z": [{
        "rotate-z": Et()
      }],
      /**
       * Scale
       * @see https://tailwindcss.com/docs/scale
       */
      scale: [{
        scale: vt()
      }],
      /**
       * Scale X
       * @see https://tailwindcss.com/docs/scale
       */
      "scale-x": [{
        "scale-x": vt()
      }],
      /**
       * Scale Y
       * @see https://tailwindcss.com/docs/scale
       */
      "scale-y": [{
        "scale-y": vt()
      }],
      /**
       * Scale Z
       * @see https://tailwindcss.com/docs/scale
       */
      "scale-z": [{
        "scale-z": vt()
      }],
      /**
       * Scale 3D
       * @see https://tailwindcss.com/docs/scale
       */
      "scale-3d": ["scale-3d"],
      /**
       * Skew
       * @see https://tailwindcss.com/docs/skew
       */
      skew: [{
        skew: Ra()
      }],
      /**
       * Skew X
       * @see https://tailwindcss.com/docs/skew
       */
      "skew-x": [{
        "skew-x": Ra()
      }],
      /**
       * Skew Y
       * @see https://tailwindcss.com/docs/skew
       */
      "skew-y": [{
        "skew-y": Ra()
      }],
      /**
       * Transform
       * @see https://tailwindcss.com/docs/transform
       */
      transform: [{
        transform: [I, W, "", "none", "gpu", "cpu"]
      }],
      /**
       * Transform Origin
       * @see https://tailwindcss.com/docs/transform-origin
       */
      "transform-origin": [{
        origin: Le()
      }],
      /**
       * Transform Style
       * @see https://tailwindcss.com/docs/transform-style
       */
      "transform-style": [{
        transform: ["3d", "flat"]
      }],
      /**
       * Translate
       * @see https://tailwindcss.com/docs/translate
       */
      translate: [{
        translate: Fe()
      }],
      /**
       * Translate X
       * @see https://tailwindcss.com/docs/translate
       */
      "translate-x": [{
        "translate-x": Fe()
      }],
      /**
       * Translate Y
       * @see https://tailwindcss.com/docs/translate
       */
      "translate-y": [{
        "translate-y": Fe()
      }],
      /**
       * Translate Z
       * @see https://tailwindcss.com/docs/translate
       */
      "translate-z": [{
        "translate-z": Fe()
      }],
      /**
       * Translate None
       * @see https://tailwindcss.com/docs/translate
       */
      "translate-none": ["translate-none"],
      /**
       * Zoom
       * @see https://tailwindcss.com/docs/zoom
       */
      zoom: [{
        zoom: [ma, I, W]
      }],
      // ---------------------
      // --- Interactivity ---
      // ---------------------
      /**
       * Accent Color
       * @see https://tailwindcss.com/docs/accent-color
       */
      accent: [{
        accent: B()
      }],
      /**
       * Appearance
       * @see https://tailwindcss.com/docs/appearance
       */
      appearance: [{
        appearance: ["none", "auto"]
      }],
      /**
       * Caret Color
       * @see https://tailwindcss.com/docs/just-in-time-mode#caret-color-utilities
       */
      "caret-color": [{
        caret: B()
      }],
      /**
       * Color Scheme
       * @see https://tailwindcss.com/docs/color-scheme
       */
      "color-scheme": [{
        scheme: ["normal", "dark", "light", "light-dark", "only-dark", "only-light"]
      }],
      /**
       * Cursor
       * @see https://tailwindcss.com/docs/cursor
       */
      cursor: [{
        cursor: ["auto", "default", "pointer", "wait", "text", "move", "help", "not-allowed", "none", "context-menu", "progress", "cell", "crosshair", "vertical-text", "alias", "copy", "no-drop", "grab", "grabbing", "all-scroll", "col-resize", "row-resize", "n-resize", "e-resize", "s-resize", "w-resize", "ne-resize", "nw-resize", "se-resize", "sw-resize", "ew-resize", "ns-resize", "nesw-resize", "nwse-resize", "zoom-in", "zoom-out", I, W]
      }],
      /**
       * Field Sizing
       * @see https://tailwindcss.com/docs/field-sizing
       */
      "field-sizing": [{
        "field-sizing": ["fixed", "content"]
      }],
      /**
       * Pointer Events
       * @see https://tailwindcss.com/docs/pointer-events
       */
      "pointer-events": [{
        "pointer-events": ["auto", "none"]
      }],
      /**
       * Resize
       * @see https://tailwindcss.com/docs/resize
       */
      resize: [{
        resize: ["none", "", "y", "x"]
      }],
      /**
       * Scroll Behavior
       * @see https://tailwindcss.com/docs/scroll-behavior
       */
      "scroll-behavior": [{
        scroll: ["auto", "smooth"]
      }],
      /**
       * Scrollbar Thumb Color
       * @see https://tailwindcss.com/docs/scrollbar-color
       */
      "scrollbar-thumb-color": [{
        "scrollbar-thumb": B()
      }],
      /**
       * Scrollbar Track Color
       * @see https://tailwindcss.com/docs/scrollbar-color
       */
      "scrollbar-track-color": [{
        "scrollbar-track": B()
      }],
      /**
       * Scrollbar Gutter
       * @see https://tailwindcss.com/docs/scrollbar-gutter
       */
      "scrollbar-gutter": [{
        "scrollbar-gutter": ["auto", "stable", "both"]
      }],
      /**
       * Scrollbar Width
       * @see https://tailwindcss.com/docs/scrollbar-width
       */
      "scrollbar-w": [{
        scrollbar: ["auto", "thin", "none"]
      }],
      /**
       * Scroll Margin
       * @see https://tailwindcss.com/docs/scroll-margin
       */
      "scroll-m": [{
        "scroll-m": U()
      }],
      /**
       * Scroll Margin Inline
       * @see https://tailwindcss.com/docs/scroll-margin
       */
      "scroll-mx": [{
        "scroll-mx": U()
      }],
      /**
       * Scroll Margin Block
       * @see https://tailwindcss.com/docs/scroll-margin
       */
      "scroll-my": [{
        "scroll-my": U()
      }],
      /**
       * Scroll Margin Inline Start
       * @see https://tailwindcss.com/docs/scroll-margin
       */
      "scroll-ms": [{
        "scroll-ms": U()
      }],
      /**
       * Scroll Margin Inline End
       * @see https://tailwindcss.com/docs/scroll-margin
       */
      "scroll-me": [{
        "scroll-me": U()
      }],
      /**
       * Scroll Margin Block Start
       * @see https://tailwindcss.com/docs/scroll-margin
       */
      "scroll-mbs": [{
        "scroll-mbs": U()
      }],
      /**
       * Scroll Margin Block End
       * @see https://tailwindcss.com/docs/scroll-margin
       */
      "scroll-mbe": [{
        "scroll-mbe": U()
      }],
      /**
       * Scroll Margin Top
       * @see https://tailwindcss.com/docs/scroll-margin
       */
      "scroll-mt": [{
        "scroll-mt": U()
      }],
      /**
       * Scroll Margin Right
       * @see https://tailwindcss.com/docs/scroll-margin
       */
      "scroll-mr": [{
        "scroll-mr": U()
      }],
      /**
       * Scroll Margin Bottom
       * @see https://tailwindcss.com/docs/scroll-margin
       */
      "scroll-mb": [{
        "scroll-mb": U()
      }],
      /**
       * Scroll Margin Left
       * @see https://tailwindcss.com/docs/scroll-margin
       */
      "scroll-ml": [{
        "scroll-ml": U()
      }],
      /**
       * Scroll Padding
       * @see https://tailwindcss.com/docs/scroll-padding
       */
      "scroll-p": [{
        "scroll-p": U()
      }],
      /**
       * Scroll Padding Inline
       * @see https://tailwindcss.com/docs/scroll-padding
       */
      "scroll-px": [{
        "scroll-px": U()
      }],
      /**
       * Scroll Padding Block
       * @see https://tailwindcss.com/docs/scroll-padding
       */
      "scroll-py": [{
        "scroll-py": U()
      }],
      /**
       * Scroll Padding Inline Start
       * @see https://tailwindcss.com/docs/scroll-padding
       */
      "scroll-ps": [{
        "scroll-ps": U()
      }],
      /**
       * Scroll Padding Inline End
       * @see https://tailwindcss.com/docs/scroll-padding
       */
      "scroll-pe": [{
        "scroll-pe": U()
      }],
      /**
       * Scroll Padding Block Start
       * @see https://tailwindcss.com/docs/scroll-padding
       */
      "scroll-pbs": [{
        "scroll-pbs": U()
      }],
      /**
       * Scroll Padding Block End
       * @see https://tailwindcss.com/docs/scroll-padding
       */
      "scroll-pbe": [{
        "scroll-pbe": U()
      }],
      /**
       * Scroll Padding Top
       * @see https://tailwindcss.com/docs/scroll-padding
       */
      "scroll-pt": [{
        "scroll-pt": U()
      }],
      /**
       * Scroll Padding Right
       * @see https://tailwindcss.com/docs/scroll-padding
       */
      "scroll-pr": [{
        "scroll-pr": U()
      }],
      /**
       * Scroll Padding Bottom
       * @see https://tailwindcss.com/docs/scroll-padding
       */
      "scroll-pb": [{
        "scroll-pb": U()
      }],
      /**
       * Scroll Padding Left
       * @see https://tailwindcss.com/docs/scroll-padding
       */
      "scroll-pl": [{
        "scroll-pl": U()
      }],
      /**
       * Scroll Snap Align
       * @see https://tailwindcss.com/docs/scroll-snap-align
       */
      "snap-align": [{
        snap: ["start", "end", "center", "align-none"]
      }],
      /**
       * Scroll Snap Stop
       * @see https://tailwindcss.com/docs/scroll-snap-stop
       */
      "snap-stop": [{
        snap: ["normal", "always"]
      }],
      /**
       * Scroll Snap Type
       * @see https://tailwindcss.com/docs/scroll-snap-type
       */
      "snap-type": [{
        snap: ["none", "x", "y", "both"]
      }],
      /**
       * Scroll Snap Type Strictness
       * @see https://tailwindcss.com/docs/scroll-snap-type
       */
      "snap-strictness": [{
        snap: ["mandatory", "proximity"]
      }],
      /**
       * Touch Action
       * @see https://tailwindcss.com/docs/touch-action
       */
      touch: [{
        touch: ["auto", "none", "manipulation"]
      }],
      /**
       * Touch Action X
       * @see https://tailwindcss.com/docs/touch-action
       */
      "touch-x": [{
        "touch-pan": ["x", "left", "right"]
      }],
      /**
       * Touch Action Y
       * @see https://tailwindcss.com/docs/touch-action
       */
      "touch-y": [{
        "touch-pan": ["y", "up", "down"]
      }],
      /**
       * Touch Action Pinch Zoom
       * @see https://tailwindcss.com/docs/touch-action
       */
      "touch-pz": ["touch-pinch-zoom"],
      /**
       * User Select
       * @see https://tailwindcss.com/docs/user-select
       */
      select: [{
        select: ["none", "text", "all", "auto"]
      }],
      /**
       * Will Change
       * @see https://tailwindcss.com/docs/will-change
       */
      "will-change": [{
        "will-change": ["auto", "scroll", "contents", "transform", I, W]
      }],
      // -----------
      // --- SVG ---
      // -----------
      /**
       * Fill
       * @see https://tailwindcss.com/docs/fill
       */
      fill: [{
        fill: ["none", ...B()]
      }],
      /**
       * Stroke Width
       * @see https://tailwindcss.com/docs/stroke-width
       */
      "stroke-w": [{
        stroke: [we, kr, Nl, Xf]
      }],
      /**
       * Stroke
       * @see https://tailwindcss.com/docs/stroke
       */
      stroke: [{
        stroke: ["none", ...B()]
      }],
      // ---------------------
      // --- Accessibility ---
      // ---------------------
      /**
       * Forced Color Adjust
       * @see https://tailwindcss.com/docs/forced-color-adjust
       */
      "forced-color-adjust": [{
        "forced-color-adjust": ["auto", "none"]
      }]
    },
    conflictingClassGroups: {
      "container-named": ["container-type"],
      overflow: ["overflow-x", "overflow-y"],
      overscroll: ["overscroll-x", "overscroll-y"],
      inset: ["inset-x", "inset-y", "inset-bs", "inset-be", "start", "end", "top", "right", "bottom", "left"],
      "inset-x": ["right", "left"],
      "inset-y": ["top", "bottom"],
      flex: ["basis", "grow", "shrink"],
      gap: ["gap-x", "gap-y"],
      p: ["px", "py", "ps", "pe", "pbs", "pbe", "pt", "pr", "pb", "pl"],
      px: ["pr", "pl"],
      py: ["pt", "pb"],
      m: ["mx", "my", "ms", "me", "mbs", "mbe", "mt", "mr", "mb", "ml"],
      mx: ["mr", "ml"],
      my: ["mt", "mb"],
      size: ["w", "h"],
      "font-size": ["leading"],
      "fvn-normal": ["fvn-ordinal", "fvn-slashed-zero", "fvn-figure", "fvn-spacing", "fvn-fraction"],
      "fvn-ordinal": ["fvn-normal"],
      "fvn-slashed-zero": ["fvn-normal"],
      "fvn-figure": ["fvn-normal"],
      "fvn-spacing": ["fvn-normal"],
      "fvn-fraction": ["fvn-normal"],
      "line-clamp": ["display", "overflow"],
      rounded: ["rounded-s", "rounded-e", "rounded-t", "rounded-r", "rounded-b", "rounded-l", "rounded-ss", "rounded-se", "rounded-ee", "rounded-es", "rounded-tl", "rounded-tr", "rounded-br", "rounded-bl"],
      "rounded-s": ["rounded-ss", "rounded-es"],
      "rounded-e": ["rounded-se", "rounded-ee"],
      "rounded-t": ["rounded-tl", "rounded-tr"],
      "rounded-r": ["rounded-tr", "rounded-br"],
      "rounded-b": ["rounded-br", "rounded-bl"],
      "rounded-l": ["rounded-tl", "rounded-bl"],
      "border-spacing": ["border-spacing-x", "border-spacing-y"],
      "border-w": ["border-w-x", "border-w-y", "border-w-s", "border-w-e", "border-w-bs", "border-w-be", "border-w-t", "border-w-r", "border-w-b", "border-w-l"],
      "border-w-x": ["border-w-r", "border-w-l"],
      "border-w-y": ["border-w-t", "border-w-b"],
      "border-color": ["border-color-x", "border-color-y", "border-color-s", "border-color-e", "border-color-bs", "border-color-be", "border-color-t", "border-color-r", "border-color-b", "border-color-l"],
      "border-color-x": ["border-color-r", "border-color-l"],
      "border-color-y": ["border-color-t", "border-color-b"],
      translate: ["translate-x", "translate-y", "translate-none"],
      "translate-none": ["translate", "translate-x", "translate-y", "translate-z"],
      "scroll-m": ["scroll-mx", "scroll-my", "scroll-ms", "scroll-me", "scroll-mbs", "scroll-mbe", "scroll-mt", "scroll-mr", "scroll-mb", "scroll-ml"],
      "scroll-mx": ["scroll-mr", "scroll-ml"],
      "scroll-my": ["scroll-mt", "scroll-mb"],
      "scroll-p": ["scroll-px", "scroll-py", "scroll-ps", "scroll-pe", "scroll-pbs", "scroll-pbe", "scroll-pt", "scroll-pr", "scroll-pb", "scroll-pl"],
      "scroll-px": ["scroll-pr", "scroll-pl"],
      "scroll-py": ["scroll-pt", "scroll-pb"],
      touch: ["touch-x", "touch-y", "touch-pz"],
      "touch-x": ["touch"],
      "touch-y": ["touch"],
      "touch-pz": ["touch"]
    },
    conflictingClassGroupModifiers: {
      "font-size": ["leading"]
    },
    postfixLookupClassGroups: ["container-type"],
    orderSensitiveModifiers: ["*", "**", "after", "backdrop", "before", "details-content", "file", "first-letter", "first-line", "marker", "placeholder", "selection"]
  };
}, ug = /* @__PURE__ */ q2(sg);
function Ne(...i) {
  return ug(x2(i));
}
const fg = [
  ["years", "yr", "year", "years"],
  ["months", "mo", "month", "months"],
  ["days", "d", "day", "days"],
  ["hours", "h", "hour", "hours"],
  ["minutes", "m", "minute", "minutes"]
], dg = /* @__PURE__ */ new Set(["years", "months", "days", "hours", "minutes", "seconds", "milliseconds"]);
function pa(i) {
  if (typeof i == "number") return Number.isFinite(i) ? i : null;
  if (typeof i != "string" || i.trim() === "") return null;
  const f = Number(i);
  return Number.isFinite(f) ? f : null;
}
function Fd(i) {
  let f = i;
  if (typeof f == "string") {
    const u = f.trim();
    if (u.length <= 4096 && u.startsWith("{") && u.endsWith("}"))
      try {
        f = JSON.parse(u);
      } catch {
        return null;
      }
    else
      return null;
  }
  return f == null || typeof f != "object" || Array.isArray(f) || f instanceof Date ? null : f;
}
function bs(i) {
  const f = Number(i.toFixed(6));
  return String(Object.is(f, -0) ? 0 : f);
}
function Wd(i) {
  const f = Fd(i);
  if (!f) return null;
  const u = Object.keys(f);
  if (u.length > 0 && !u.some((h) => dg.has(h))) return null;
  const o = [];
  for (const [h, v, x, b] of fg) {
    const D = pa(f[h]);
    D == null || D === 0 || o.push({ amount: D, value: bs(D), shortUnit: v, singularUnit: x, pluralUnit: b });
  }
  const m = pa(f.seconds) ?? 0, p = pa(f.milliseconds) ?? 0;
  if (m !== 0 || p !== 0)
    if (m === 0 && Math.abs(p) < 1e3)
      o.push({
        amount: p,
        value: bs(p),
        shortUnit: "ms",
        singularUnit: "millisecond",
        pluralUnit: "milliseconds"
      });
    else {
      const h = m + p / 1e3;
      h !== 0 && o.push({
        amount: h,
        value: bs(h),
        shortUnit: "s",
        singularUnit: "second",
        pluralUnit: "seconds"
      });
    }
  return o.length === 0 && o.push({ amount: 0, value: "0", shortUnit: "s", singularUnit: "second", pluralUnit: "seconds" }), o;
}
function zr(i, f = !1) {
  const u = Wd(i);
  return u ? u.map((o) => {
    if (!f) return `${o.value}${o.shortUnit}`;
    const m = Math.abs(o.amount) === 1 ? o.singularUnit : o.pluralUnit;
    return `${o.value} ${m}`;
  }).join(" ") : ia(i);
}
function Jf(i) {
  const f = Fd(i);
  if (!f) return null;
  const u = pa(f.years) ?? 0, o = pa(f.months) ?? 0, m = pa(f.days) ?? 0, p = pa(f.hours) ?? 0, h = pa(f.minutes) ?? 0, v = pa(f.seconds) ?? 0, x = pa(f.milliseconds) ?? 0;
  return (((((u * 12 + o) * 30 + m) * 24 + p) * 60 + h) * 60 + v) * 1e3 + x;
}
function ia(i) {
  if (i == null) return "";
  if (typeof i == "boolean") return i ? "true" : "false";
  if (typeof i == "number")
    return Number.isFinite(i) ? (Number.isInteger(i), i.toString()) : String(i);
  if (typeof i == "bigint") return i.toString();
  if (i instanceof Date) return i.toISOString();
  if (Array.isArray(i)) return JSON.stringify(i);
  if (typeof i == "object")
    try {
      return JSON.stringify(i);
    } catch {
      return String(i);
    }
  return String(i);
}
const mg = "application/x-rvbbit-lens-column";
function pg(i, f) {
  i.setData(mg, JSON.stringify(f)), i.setData("text/plain", f.columns.map((u) => u.name).join(", ")), i.effectAllowed = "copy";
}
const hg = /* @__PURE__ */ new Set();
function vs(i) {
  for (const f of hg) f();
}
const gg = {
  column: "--main",
  block: "--main",
  param: "--rvbbit-accent"
};
function Id(i, f) {
  if (typeof window > "u") return f;
  const u = getComputedStyle(document.documentElement).getPropertyValue(i).trim();
  return u.length > 0 ? u : f;
}
let $i = null, Cl = null;
function bg(i, f) {
  if (typeof window > "u") return f;
  try {
    if (Cl || ($i = document.createElement("canvas"), $i.width = 1, $i.height = 1, Cl = $i.getContext("2d", { willReadFrequently: !0 })), !Cl) return f;
    Cl.clearRect(0, 0, 1, 1), Cl.fillStyle = i, Cl.fillRect(0, 0, 1, 1);
    const u = Cl.getImageData(0, 0, 1, 1).data;
    return u[3] === 0 ? f : u[3] === 255 ? `rgb(${u[0]}, ${u[1]}, ${u[2]})` : `rgba(${u[0]}, ${u[1]}, ${u[2]}, ${(u[3] / 255).toFixed(3)})`;
  } catch {
    return f;
  }
}
function xs(i, f) {
  return bg(Id(i, f), f);
}
function vg(i, f) {
  if (typeof document > "u") return;
  const u = xs(gg[f.variant], "#2dd4cf"), o = xs("--block-bg", "#0d1820"), m = xs("--foreground", "#e6f7f6"), p = Id(
    "--font-family-sans",
    "ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif"
  ), h = document.createElement("div");
  h.style.cssText = [
    "position: fixed",
    "top: 0",
    "left: 0",
    "z-index: -1",
    "pointer-events: none",
    "padding: 7px 14px",
    "border-radius: 999px",
    `background: ${o}`,
    `color: ${m}`,
    `border: 1.5px solid ${u}`,
    "box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45)",
    `font-family: ${p}`,
    "font-size: 12px",
    "font-weight: 500",
    "white-space: nowrap",
    "display: inline-block"
  ].join(";");
  const v = [];
  v.push(`<span style="color:${u};margin-right:6px;font-size:11px;">●</span>`), v.push(`<span>${$f(f.label)}</span>`), f.sublabel && v.push(
    `<span style="opacity:0.6;margin-left:8px;font-size:11px;">${$f(f.sublabel)}</span>`
  ), f.count && f.count > 1 && v.push(
    `<span style="margin-left:8px;padding:1px 7px;background:color-mix(in oklch, ${u} 25%, transparent);border-radius:999px;font-size:10px;color:${u};">+${f.count - 1}</span>`
  ), h.innerHTML = v.join(""), document.body.appendChild(h), h.getBoundingClientRect(), i.setDragImage(h, 14, 14), h.style.top = "-10000px", h.style.left = "-10000px";
  const x = () => {
    h.remove(), document.removeEventListener("dragend", x, !0), document.removeEventListener("drop", x, !0);
  };
  document.addEventListener("dragend", x, !0), document.addEventListener("drop", x, !0), setTimeout(x, 5e3);
}
function $f(i) {
  return i.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
const xg = "rvbbit-lens.present", Ff = "rvbbit-lens:present-changed";
function yg() {
  if (typeof window > "u") return !1;
  try {
    return window.sessionStorage.getItem(xg) === "1";
  } catch {
    return !1;
  }
}
function wg(i) {
  return typeof window > "u" ? () => {
  } : (window.addEventListener(Ff, i), () => window.removeEventListener(Ff, i));
}
function Sg() {
  return s.useSyncExternalStore(
    wg,
    yg,
    () => !1
  );
}
const Eg = 220;
function Ag({ state: i, onClose: f, portalContainer: u }) {
  const o = s.useRef(null);
  if (s.useEffect(() => {
    if (!i) return;
    const v = (b) => {
      o.current && !b.composedPath().includes(o.current) && f();
    }, x = (b) => {
      b.key === "Escape" && f();
    };
    return document.addEventListener("mousedown", v, !0), document.addEventListener("keydown", x, !0), window.addEventListener("blur", f), window.addEventListener("resize", f), () => {
      document.removeEventListener("mousedown", v, !0), document.removeEventListener("keydown", x, !0), window.removeEventListener("blur", f), window.removeEventListener("resize", f);
    };
  }, [i, f]), !i) return null;
  const m = i.items.reduce((v, x) => v + (x.separatorBefore ? 39 : 30), 0) + 8, p = Math.max(8, Math.min(i.x, window.innerWidth - Eg - 8)), h = Math.max(8, Math.min(i.y, window.innerHeight - m - 8));
  return typeof document > "u" ? null : Hs.createPortal(
    /* @__PURE__ */ g.jsx(
      "div",
      {
        ref: o,
        role: "menu",
        onContextMenu: (v) => v.preventDefault(),
        className: "fixed z-[10000] min-w-[200px] origin-top-left animate-in fade-in-0 zoom-in-95 rounded-lg border border-chrome-border/60 bg-chrome-bg/90 p-1 text-[12px] text-chrome-text shadow-2xl backdrop-blur-[6px] duration-100",
        style: { left: p, top: h },
        children: i.items.map((v) => /* @__PURE__ */ g.jsxs("div", { children: [
          v.separatorBefore ? /* @__PURE__ */ g.jsx("div", { className: "my-1 h-px bg-chrome-border/50" }) : null,
          /* @__PURE__ */ g.jsxs(
            "button",
            {
              type: "button",
              role: "menuitem",
              disabled: v.disabled,
              onClick: () => {
                v.onSelect?.(), f();
              },
              className: Ne(
                "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left transition-colors disabled:opacity-40",
                v.danger ? "hover:bg-danger/10 hover:text-danger" : "hover:bg-rvbbit-accent/15 hover:text-foreground"
              ),
              children: [
                v.icon ? /* @__PURE__ */ g.jsx(v.icon, { className: "h-3.5 w-3.5 shrink-0 text-chrome-text/70" }) : /* @__PURE__ */ g.jsx("span", { className: "w-3.5 shrink-0" }),
                /* @__PURE__ */ g.jsx("span", { className: "flex-1 truncate", children: v.label }),
                v.checked ? /* @__PURE__ */ g.jsx(Wh, { className: "h-3.5 w-3.5 shrink-0 text-rvbbit-accent" }) : null
              ]
            }
          )
        ] }, v.id))
      }
    ),
    u ?? document.body
  );
}
const Wf = 24, Mg = 44, kg = 80, zg = 360, Hg = 300, Tg = 3, If = 72, Ng = 64e3, Fi = "__rvbbit_transposed_field", Cg = 152, _g = /\.(png|jpe?g|gif|webp|avif|svg)(\?\S*)?$/i, jg = /[?&]format=(png|jpe?g|webp)(&|$)/i;
function Pf(i) {
  if (typeof i != "string") return !1;
  const f = i.trim();
  return f.startsWith("data:image/") ? !0 : f.length > 2048 || /\s/.test(f) || !/^https?:\/\//i.test(f) ? !1 : _g.test(f) || jg.test(f);
}
const Og = /__heat(row)?$/i, ed = 10, Zg = 38, Dg = 26, Rg = 10, Vg = /__(pct_of_ttl|delta_prev)$/i, Lg = /__(heat(row)?|pct_of_ttl|delta_prev)$/i;
function Es(i) {
  return i.replace(Lg, "");
}
function Ug(i) {
  return typeof i == "string" && i.length <= 2048 && /^https?:\/\/\S+$/i.test(i.trim());
}
function Wi(i) {
  return Number.isFinite(i) ? Math.abs(i) >= 100 ? i.toFixed(0) : String(Number(i.toPrecision(3))) : String(i);
}
function qg(i) {
  const f = i.row ? " · tints the whole row" : "";
  return i.kind === "categorical" ? i.slots ? `heat: ${i.slots.size} value bands${f}` : `heat: value bands (hashed colors)${f}` : i.divergent ? `heat: diverging around 0 (${Wi(i.min)} … ${Wi(i.max)})${f}` : `heat: ${Wi(i.min)} → ${Wi(i.max)}${f}`;
}
function Bg(i, f) {
  const u = /* @__PURE__ */ new Map();
  for (const o of i) {
    const m = Og.exec(o.name);
    if (!m) continue;
    let p = 1 / 0, h = -1 / 0, v = 0, x = !0;
    const b = /* @__PURE__ */ new Set();
    for (const D of f) {
      const C = D?.[o.name];
      if (!(C == null || C === "")) {
        if (x) {
          const T = Number(C);
          Number.isFinite(T) ? (v += 1, T < p && (p = T), T > h && (h = T)) : x = !1;
        }
        b.size <= ed && b.add(String(C));
      }
    }
    if (!x || v === 0) {
      let D;
      if (b.size > 0 && b.size <= ed) {
        const C = [...b].sort((T, L) => T.localeCompare(L));
        D = new Map(C.map((T, L) => [T, Math.floor(L * 10 / C.length) + 1]));
      }
      u.set(o.name, { column: o.name, row: m[1] != null, kind: "categorical", min: 0, max: 0, divergent: !1, slots: D });
    } else
      u.set(o.name, {
        column: o.name,
        row: m[1] != null,
        kind: "numeric",
        min: p,
        max: h,
        divergent: p < 0 && h > 0
      });
  }
  return u;
}
function td(i, f, u) {
  if (f == null || f === "") return null;
  if (i.kind === "categorical") {
    const p = String(f), h = i.slots?.get(p);
    return `color-mix(in oklch, ${h != null ? `var(--viz-kind-${h})` : b2(p)} ${u}%, transparent)`;
  }
  const o = Number(f);
  if (!Number.isFinite(o)) return null;
  if (i.divergent) {
    const p = Math.max(Math.abs(i.min), Math.abs(i.max));
    if (p <= 0) return null;
    const h = Math.min(1, Math.abs(o) / p);
    return h < 0.02 ? null : `color-mix(in oklch, ${o < 0 ? "var(--danger)" : "var(--success)"} ${(h * u).toFixed(1)}%, transparent)`;
  }
  return i.max <= i.min ? null : `color-mix(in oklch, var(--chart-3) ${(3 + (o - i.min) / (i.max - i.min) * (u - 3)).toFixed(1)}%, transparent)`;
}
function Gg({
  portalContainer: i,
  filterLabel: f = "filter rows…",
  showSearch: u = !0,
  columns: o,
  rows: m,
  className: p,
  transposed: h = !1,
  onTransposedChange: v,
  columnDragSource: x,
  onEmitCellParam: b,
  activeParams: D,
  onOpenRow: C,
  onCellFilter: T,
  highlightFilters: L,
  extraCellMenuItems: G,
  editable: Q,
  receiptColumns: de,
  onOpenReceipt: fe
}) {
  const se = s.useRef(null), [be, Oe] = s.useState({}), [Le, J] = s.useState(/* @__PURE__ */ new Set()), [K, U] = s.useState(null), [xe, tt] = s.useState(null), [R, $] = s.useState(/* @__PURE__ */ new Set()), [ye, he] = s.useState(/* @__PURE__ */ new Map());
  s.useEffect(() => {
    he(/* @__PURE__ */ new Map()), tt(null);
  }, [m]);
  const Qe = s.useMemo(
    () => new Set((Q?.columns ?? []).map((Z) => Z.toLowerCase())),
    [Q]
  ), z = (Z) => !!Q && !qt && Qe.has(Z.toLowerCase()), q = async (Z, V, ie, X) => {
    const re = `${V}:${ie.name}`, ue = Z?.[ie.name];
    if (tt(null), X === String(ue ?? "")) return;
    $((ee) => new Set(ee).add(re));
    let Ze = !1;
    try {
      Ze = await Q.onEdit({ row: Z, rowIndex: V, column: ie, value: X, previous: ue });
    } catch {
      Ze = !1;
    }
    $((ee) => {
      const Ye = new Set(ee);
      return Ye.delete(re), Ye;
    }), Ze && he((ee) => new Map(ee).set(re, X));
  }, [ne, He] = s.useState(!0), B = s.useMemo(() => {
    const Z = /* @__PURE__ */ new Set(), V = m.slice(0, 50);
    for (const ie of o)
      V.some((X) => Pf(X?.[ie.name])) && Z.add(ie.name);
    return Z;
  }, [o, m]), S = ne && B.size > 0 && !h, _ = S ? Mg : Wf, [Y, F] = s.useState(!0), te = s.useMemo(
    () => new Set(o.filter(zn).map((Z) => Z.name)),
    [o]
  ), [le, Se] = s.useState(!0), Ge = s.useMemo(() => Bg(o, m), [o, m]), me = le && Ge.size > 0 && !h, Ut = s.useMemo(() => {
    if (!me) return null;
    for (const Z of o) {
      const V = Ge.get(Z.name);
      if (V?.row) return V;
    }
    return null;
  }, [o, me, Ge]), Et = s.useMemo(() => {
    const Z = /* @__PURE__ */ new Map();
    for (const V of o) {
      const ie = Vg.exec(V.name);
      ie && Z.set(V.name, ie[1].toLowerCase() === "pct_of_ttl" ? "pct" : "delta");
    }
    return Z;
  }, [o]), [vt, Ra] = s.useState(""), [Fe, Tn] = s.useState(null), Zl = (Z) => Tn((V) => !V || V.col !== Z ? { col: Z, dir: "asc" } : V.dir === "asc" ? { col: Z, dir: "desc" } : null), qt = Sg(), Pt = !!x && !qt;
  s.useEffect(() => {
    se.current?.scrollTo({ left: 0, top: 0 });
  }, [h]);
  const Dl = s.useMemo(() => {
    const Z = /* @__PURE__ */ new Map();
    for (const V of D ?? []) {
      const ie = V.cascade === !1;
      if (!ie && V.operator !== "in") continue;
      const X = (Array.isArray(V.value) ? V.value : [V.value]).map((re) => String(re));
      Z.set(V.field, { values: new Set(X), cascade: !ie });
    }
    return Z;
  }, [D]), Rl = s.useMemo(() => {
    const Z = /* @__PURE__ */ new Map();
    if (!L?.length) return Z;
    for (const V of o) {
      const ie = L.find(
        (re) => re.sourceTable ? !!V.sourceColumn && re.column.toLowerCase() === V.sourceColumn.toLowerCase() && (!V.sourceTable || re.sourceTable.toLowerCase() === V.sourceTable.toLowerCase()) : re.column.toLowerCase() === V.name.toLowerCase()
      );
      if (!ie) continue;
      const X = (Array.isArray(ie.value) ? ie.value : [ie.value]).map((re) => String(re));
      Z.set(V.name, new Set(X));
    }
    return Z;
  }, [L, o]);
  function Vl(Z, V) {
    if (!x || qt) return;
    const ie = x.columns.find((De) => De.name === V);
    if (!ie) return;
    const X = x.columns.filter((De) => Le.has(De.name)), re = X.some((De) => De.name === ie.name) && X.length > 0 ? X : [ie], ue = {
      kind: "rvbbit-lens.desktop.column",
      parentWindowId: x.parentWindowId,
      parentBlockName: x.parentBlockName,
      parentTitle: x.parentTitle,
      parentSql: x.parentSql,
      relationKey: x.relationKey,
      columns: re,
      // Full source column set so a multi-arg semantic bind can offer sibling
      // columns (e.g. contradicts(a, b) with b bound to another column).
      sourceColumns: x.columns
    };
    pg(Z.dataTransfer, ue), vs({
      parentWindowId: ue.parentWindowId,
      parentBlockName: ue.parentBlockName,
      relationKey: ue.relationKey
    });
    const Ze = re.filter((De) => De.role === "dimension").length, ee = re.length - Ze, Ye = re.length > 1 ? `${Ze} dim · ${ee} metric` : ie.role === "metric" ? "metric" : "dimension";
    vg(Z.dataTransfer, {
      variant: "column",
      label: re.length > 1 ? re.map((De) => De.name).join(", ") : ie.name,
      sublabel: Ye,
      count: re.length
    });
  }
  function Cr(Z, V) {
    if (!(!x || qt)) {
      if (!Z.metaKey && !Z.ctrlKey && !Z.shiftKey) {
        J((ie) => {
          const X = new Set(ie);
          return X.has(V) ? X.delete(V) : X.add(V), X;
        });
        return;
      }
      J((ie) => {
        const X = new Set(ie);
        return X.has(V) ? X.delete(V) : X.add(V), X;
      });
    }
  }
  const Ll = s.useMemo(() => o.map((Z) => be[Z.name] ?? rd(Z, m)), [be, o, m]), Ke = s.useMemo(() => {
    let Z = m;
    const V = vt.trim().toLowerCase();
    if (V && (h && o.some((X) => ld(X, V)) || (Z = Z.filter((X) => o.some((re) => ad(X[re.name], V, re))))), Fe) {
      const { col: ie, dir: X } = Fe, re = X === "asc" ? 1 : -1, ue = o.find((Ze) => Ze.name === ie);
      Z = [...Z].sort((Ze, ee) => {
        const Ye = Ze[ie], De = ee[ie];
        if (Ye == null && De == null) return 0;
        if (Ye == null) return 1;
        if (De == null) return -1;
        if (typeof Ye == "number" && typeof De == "number") return (Ye - De) * re;
        if (ue && cl(ue)) {
          const ca = Jf(Ye), ga = Jf(De);
          if (ca != null && ga != null) return (ca - ga) * re;
        }
        const Bt = ue && zn(ue) ? Hr(Ye, 1200) : ue && cl(ue) ? zr(Ye) : String(ia(Ye)), ta = ue && zn(ue) ? Hr(De, 1200) : ue && cl(ue) ? zr(De) : String(ia(De)), kt = Number(Bt), wt = Number(ta);
        return Number.isFinite(kt) && Number.isFinite(wt) ? (kt - wt) * re : Bt.localeCompare(ta) * re;
      });
    }
    return Z;
  }, [m, o, vt, Fe, h]), Nn = s.useMemo(() => {
    if (!h) return o;
    const Z = vt.trim().toLowerCase();
    return Z ? o.filter((V) => ld(V, Z) || Ke.some((ie) => ad(ie[V.name], Z, V))) : o;
  }, [o, vt, h, Ke]), _r = s.useMemo(() => {
    const Z = /* @__PURE__ */ new Map();
    for (const [V, ie] of Et) {
      if (ie !== "pct") continue;
      let X = 0;
      for (const re of Ke) {
        const ue = Number(re?.[V]);
        Number.isFinite(ue) && (X += ue);
      }
      Z.set(V, X);
    }
    return Z;
  }, [Et, Ke]), Ul = be[Fi] ?? ys(o), ea = qf({
    count: h ? Nn.length : Ke.length,
    getScrollElement: () => se.current,
    estimateSize: () => _,
    overscan: 16
  });
  s.useEffect(() => {
    ea.measure();
  }, [_, ea]);
  const jr = qf({
    horizontal: !0,
    count: h ? Ke.length : 0,
    getScrollElement: () => se.current,
    estimateSize: () => Cg,
    overscan: 8
  });
  function Cn(Z, V) {
    V.preventDefault(), V.stopPropagation();
    const ie = V.clientX, X = be[Z] ?? (Z === Fi ? ys(o) : rd(o.find((ee) => ee.name === Z), m)), re = Z === Fi ? ys(o) : Pd(o.find((ee) => ee.name === Z));
    function ue(ee) {
      const Ye = Math.max(re, X + (ee.clientX - ie));
      Oe((De) => ({ ...De, [Z]: Ye }));
    }
    function Ze() {
      window.removeEventListener("pointermove", ue), window.removeEventListener("pointerup", Ze);
    }
    window.addEventListener("pointermove", ue), window.addEventListener("pointerup", Ze);
  }
  if (o.length === 0)
    return /* @__PURE__ */ g.jsx("div", { className: "grid h-full place-items-center text-xs text-chrome-text/70", children: "No columns" });
  const _n = Ll.reduce((Z, V) => Z + V, 0), jn = jr.getTotalSize(), ha = Ul + jn, xt = jr.getVirtualItems(), oa = h ? vt.trim() || Fe ? `${Nn.length} fields × ${Ke.length} rows` : `${o.length} fields × ${m.length} rows` : vt.trim() || Fe ? `${Ke.length} of ${m.length}` : `${m.length} ${m.length === 1 ? "row" : "rows"}`;
  function yt(Z, V, ie, X) {
    Z.preventDefault(), Z.stopPropagation();
    const re = V?.[X.name];
    U({
      x: Z.clientX,
      y: Z.clientY,
      items: [
        {
          id: "open-row",
          label: "Open row",
          icon: xd,
          disabled: !C,
          onSelect: () => C?.({ row: V, rowIndex: ie, column: X })
        },
        {
          id: "copy-cell",
          label: "Copy cell",
          icon: Ih,
          onSelect: () => {
            id(re == null ? "" : Xg(re));
          }
        },
        {
          id: "copy-row",
          label: "Copy row JSON",
          icon: n2,
          onSelect: () => {
            id(tm(V));
          }
        },
        ...G?.({ row: V, rowIndex: ie, column: X, value: re }) ?? []
      ]
    });
  }
  return /* @__PURE__ */ g.jsxs("div", { className: Ne("flex h-full flex-col", p), children: [
    /* @__PURE__ */ g.jsxs("div", { className: "flex items-center gap-1.5 border-b border-chrome-border bg-chrome-bg/60 px-2 py-1", children: [
      u && /* @__PURE__ */ g.jsxs(g.Fragment, { children: [
        /* @__PURE__ */ g.jsx(Ad, { className: "h-3 w-3 shrink-0 text-chrome-text/40" }),
        /* @__PURE__ */ g.jsx(
          "input",
          {
            value: vt,
            onChange: (Z) => Ra(Z.target.value),
            "aria-label": f,
            placeholder: h ? "filter fields or values…" : f,
            spellCheck: !1,
            className: "h-5 min-w-0 flex-1 bg-transparent text-[11px] text-foreground outline-none placeholder:text-chrome-text/35"
          }
        )
      ] }),
      /* @__PURE__ */ g.jsx("span", { className: "shrink-0 text-[10px] tabular-nums text-chrome-text/45", children: oa }),
      te.size > 0 && !qt ? /* @__PURE__ */ g.jsx(
        "button",
        {
          type: "button",
          "aria-pressed": Y,
          "data-rvbbit-json-toggle": !0,
          onClick: () => F((Z) => !Z),
          title: Y ? "Structured JSON previews on — click for compact raw JSON" : "Render JSON as compact structured previews",
          className: Ne(
            "grid h-5 w-6 shrink-0 place-items-center rounded border transition-colors",
            Y ? "border-main/50 bg-main/15 text-foreground" : "border-chrome-border/60 text-chrome-text/55 hover:text-foreground"
          ),
          children: /* @__PURE__ */ g.jsx(Cd, { className: "h-3 w-3" })
        }
      ) : null,
      Ge.size > 0 && !h && !qt ? /* @__PURE__ */ g.jsx(
        "button",
        {
          type: "button",
          onClick: () => Se((Z) => !Z),
          title: le ? "Heat formatting on (__heat / __heatrow columns) — click to disable" : "Enable heat formatting",
          className: Ne(
            "grid h-5 w-6 shrink-0 place-items-center rounded border transition-colors",
            le ? "border-main/50 bg-main/15 text-foreground" : "border-chrome-border/60 text-chrome-text/55 hover:text-foreground"
          ),
          children: /* @__PURE__ */ g.jsx(Of, { className: "h-3 w-3" })
        }
      ) : null,
      B.size > 0 && !h && !qt ? /* @__PURE__ */ g.jsx(
        "button",
        {
          type: "button",
          onClick: () => He((Z) => !Z),
          title: ne ? "Showing image thumbnails — click for text" : "Render image URLs as thumbnails",
          className: Ne(
            "grid h-5 w-6 shrink-0 place-items-center rounded border transition-colors",
            ne ? "border-main/50 bg-main/15 text-foreground" : "border-chrome-border/60 text-chrome-text/55 hover:text-foreground"
          ),
          children: /* @__PURE__ */ g.jsx(Ph, { className: "h-3 w-3" })
        }
      ) : null,
      v && !qt ? /* @__PURE__ */ g.jsxs(
        "button",
        {
          type: "button",
          role: "switch",
          "aria-checked": h,
          "aria-label": "Transpose",
          onClick: () => v(!h),
          title: "Swap rows and columns; show field names down the left",
          className: Ne(
            "inline-flex shrink-0 items-center gap-1.5 rounded px-1 py-0.5 text-[10px] transition-colors",
            h ? "text-foreground" : "text-chrome-text/55 hover:text-foreground"
          ),
          children: [
            /* @__PURE__ */ g.jsx("span", { "aria-hidden": "true", className: Ne(
              "relative h-3 w-5 shrink-0 rounded-full border transition-colors",
              h ? "border-main/60 bg-main/30" : "border-chrome-border bg-chrome-text/10"
            ), children: /* @__PURE__ */ g.jsx("span", { className: Ne(
              "absolute left-0 top-px h-2 w-2 rounded-full transition-transform",
              h ? "translate-x-[calc(0.75rem-3px)] bg-main" : "translate-x-px bg-chrome-text/55"
            ) }) }),
            "Transpose"
          ]
        }
      ) : null
    ] }),
    /* @__PURE__ */ g.jsx(
      "div",
      {
        ref: se,
        className: "result-grid-scrollport flex flex-1 flex-col overflow-auto bg-doc-bg group-data-[focused=false]/window:bg-doc-bg/70",
        children: h ? /* @__PURE__ */ g.jsxs(g.Fragment, { children: [
          /* @__PURE__ */ g.jsxs("div", { className: "sticky top-0 z-20 flex shrink-0 border-b border-chrome-border bg-chrome-bg/95 backdrop-blur", style: { minWidth: ha }, children: [
            /* @__PURE__ */ g.jsxs(
              "div",
              {
                className: "sticky left-0 z-30 flex items-center border-r border-chrome-border/70 bg-chrome-bg/95 px-2 py-1 text-[11px] uppercase tracking-wider text-chrome-text",
                style: { width: Ul },
                children: [
                  /* @__PURE__ */ g.jsx("span", { className: "truncate text-foreground", children: "Field" }),
                  qt ? null : /* @__PURE__ */ g.jsx(
                    "div",
                    {
                      onPointerDown: (Z) => Cn(Fi, Z),
                      className: "absolute right-0 top-0 h-full w-1.5 cursor-col-resize hover:bg-main/50"
                    }
                  )
                ]
              }
            ),
            /* @__PURE__ */ g.jsx("div", { className: "relative h-full", style: { width: jn }, children: xt.map((Z) => {
              const V = Ke[Z.index], ie = m.indexOf(V);
              return /* @__PURE__ */ g.jsx(
                "div",
                {
                  className: "absolute top-0 flex h-full items-center border-r border-chrome-border/60 px-2 py-1 text-[11px] uppercase tracking-wider text-chrome-text",
                  style: { transform: `translateX(${Z.start}px)`, width: Z.size },
                  title: `Result row ${ie >= 0 ? ie + 1 : Z.index + 1}`,
                  children: /* @__PURE__ */ g.jsxs("span", { className: "truncate text-foreground", children: [
                    "row ",
                    ie >= 0 ? ie + 1 : Z.index + 1
                  ] })
                },
                Z.key
              );
            }) })
          ] }),
          /* @__PURE__ */ g.jsx("div", { style: { height: ea.getTotalSize(), minWidth: ha }, className: "relative shrink-0", children: ea.getVirtualItems().map((Z) => {
            const V = Nn[Z.index];
            if (!V) return null;
            const ie = x?.columns.find((ue) => ue.name === V.name)?.role, X = Le.has(V.name), re = nd(V);
            return /* @__PURE__ */ g.jsxs(
              "div",
              {
                className: Ne(
                  "absolute left-0 right-0 flex border-b border-chrome-border/30",
                  Z.index % 2 === 0 ? "bg-transparent" : "bg-foreground/[0.025]"
                ),
                style: { transform: `translateY(${Z.start}px)`, height: Wf, minWidth: ha },
                children: [
                  /* @__PURE__ */ g.jsxs(
                    "div",
                    {
                      draggable: Pt,
                      onDragStart: (ue) => Vl(ue, V.name),
                      onDragEnd: () => vs(),
                      onClick: (ue) => Cr(ue, V.name),
                      className: Ne(
                        "group sticky left-0 z-10 flex select-none items-center border-r border-chrome-border/70 bg-doc-bg px-2 py-0.5 text-[12px] group-data-[focused=false]/window:bg-doc-bg/90",
                        Pt && "cursor-grab active:cursor-grabbing hover:bg-foreground/[0.05]",
                        X && "bg-main/15 text-foreground"
                      ),
                      style: { width: Ul },
                      title: Pt ? "Drag onto canvas to GROUP BY this field. Cmd/Ctrl-click to multi-select." : void 0,
                      children: [
                        Pt ? /* @__PURE__ */ g.jsx(jf, { className: Ne("mr-1 h-3 w-3 shrink-0", ie === "metric" ? "text-chart-3" : "text-main/70") }) : null,
                        /* @__PURE__ */ g.jsx("span", { className: "min-w-0 flex-1 truncate text-foreground", children: Es(V.name) }),
                        /* @__PURE__ */ g.jsx("span", { className: "ml-2 truncate text-[10px] text-chrome-text/70", children: V.dataTypeName ?? `oid:${V.dataTypeId}` }),
                        /* @__PURE__ */ g.jsx(
                          "button",
                          {
                            type: "button",
                            onClick: (ue) => {
                              ue.stopPropagation(), Zl(V.name);
                            },
                            title: "Sort row columns by this field",
                            className: Ne(
                              "ml-1 shrink-0 px-0.5 text-[10px] leading-none hover:text-foreground",
                              Fe?.col === V.name ? "text-main" : "text-chrome-text/40 opacity-0 group-hover:opacity-100"
                            ),
                            children: Fe?.col === V.name ? Fe.dir === "asc" ? "▲" : "▼" : "↕"
                          }
                        )
                      ]
                    }
                  ),
                  /* @__PURE__ */ g.jsx("div", { className: "relative", style: { width: jn }, children: xt.map((ue) => {
                    const Ze = Ke[ue.index], ee = Ze?.[V.name], Ye = te.has(V.name) && ee != null, De = cl(V) && ee != null, Bt = Dl.get(V.name), ta = Bt ? Bt.values.has(String(ee)) : !1, kt = Rl.get(V.name)?.has(String(ee)) ?? !1;
                    return /* @__PURE__ */ g.jsx(
                      "div",
                      {
                        onClick: (wt) => {
                          if (T) {
                            wt.preventDefault(), wt.stopPropagation(), T(V, ee);
                            return;
                          }
                          if (!b) return;
                          wt.preventDefault(), wt.stopPropagation();
                          const ca = wt.metaKey || wt.ctrlKey;
                          b(V.name, ee, V.dataTypeId, "in", ca, {
                            schema: V.sourceSchema,
                            table: V.sourceTable,
                            column: V.sourceColumn
                          });
                        },
                        onContextMenu: (wt) => yt(wt, Ze, m.indexOf(Ze), V),
                        className: Ne(
                          "absolute top-0 h-full truncate border-r border-chrome-border/30 px-2 py-0.5 text-[12px]",
                          (Ye || De) && "font-mono",
                          re ? "text-right tabular-nums" : "text-left",
                          ee == null ? "text-chrome-text/40 italic" : "text-foreground",
                          (b || T) && "cursor-pointer hover:bg-main/10",
                          ta && (Bt.cascade ? "bg-main/25 text-foreground" : "bg-main/15 text-foreground ring-1 ring-inset ring-main/55"),
                          kt && "bg-rvbbit-accent/20 text-foreground ring-1 ring-inset ring-rvbbit-accent/60"
                        ),
                        style: { transform: `translateX(${ue.start}px)`, width: ue.size },
                        title: T ? `${_l(ee, V)} — click to filter the dashboard` : b ? `${_l(ee, V)} — click to select · ⌘ filter` : _l(ee, V),
                        children: ee == null ? "∅" : Ye ? /* @__PURE__ */ g.jsx(cd, { value: ee, structured: Y, compact: !0 }) : De ? /* @__PURE__ */ g.jsx(od, { value: ee, compact: !0 }) : ia(ee)
                      },
                      `${V.name}:${ue.key}`
                    );
                  }) })
                ]
              },
              Z.key
            );
          }) })
        ] }) : /* @__PURE__ */ g.jsxs(g.Fragment, { children: [
          /* @__PURE__ */ g.jsx("div", { className: "sticky top-0 z-10 flex shrink-0 border-b border-chrome-border bg-chrome-bg/95 backdrop-blur", style: { minWidth: _n }, children: o.map((Z, V) => {
            const ie = x?.columns.find((re) => re.name === Z.name)?.role, X = Le.has(Z.name);
            return /* @__PURE__ */ g.jsxs(
              "div",
              {
                draggable: Pt,
                onDragStart: (re) => Vl(re, Z.name),
                onDragEnd: () => vs(),
                onClick: (re) => Cr(re, Z.name),
                className: Ne(
                  "group relative flex select-none items-center border-r border-chrome-border/60 px-2 py-1 text-[11px] uppercase tracking-wider text-chrome-text",
                  Pt && "cursor-grab active:cursor-grabbing hover:bg-foreground/[0.05]",
                  X && "bg-main/15 text-foreground"
                ),
                style: { width: Ll[V] },
                title: [
                  Ge.has(Z.name) ? qg(Ge.get(Z.name)) : null,
                  Et.has(Z.name) ? Et.get(Z.name) === "pct" ? "table calc: % of visible column total" : "table calc: change vs previous visible row (sort-aware)" : null,
                  Pt ? "Drag onto canvas to GROUP BY this column. Cmd/Ctrl-click to multi-select." : null
                ].filter(Boolean).join(`
`) || void 0,
                children: [
                  Pt ? /* @__PURE__ */ g.jsx(jf, { className: Ne("mr-1 h-3 w-3 shrink-0", ie === "metric" ? "text-chart-3" : "text-main/70") }) : null,
                  /* @__PURE__ */ g.jsx("span", { className: "truncate text-foreground", children: Es(Z.name) }),
                  Ge.has(Z.name) ? /* @__PURE__ */ g.jsx(Of, { className: "ml-1 h-2.5 w-2.5 shrink-0 text-chart-3/80" }) : null,
                  /* @__PURE__ */ g.jsx("span", { className: "ml-2 truncate text-[10px] text-chrome-text/70", children: Z.dataTypeName ?? `oid:${Z.dataTypeId}` }),
                  /* @__PURE__ */ g.jsx(
                    "button",
                    {
                      type: "button",
                      onClick: (re) => {
                        re.stopPropagation(), Zl(Z.name);
                      },
                      title: "Sort by this column",
                      className: Ne(
                        "ml-1 shrink-0 px-0.5 text-[10px] leading-none hover:text-foreground",
                        Fe?.col === Z.name ? "text-main" : "text-chrome-text/40 opacity-0 group-hover:opacity-100"
                      ),
                      children: Fe?.col === Z.name ? Fe.dir === "asc" ? "▲" : "▼" : "↕"
                    }
                  ),
                  qt ? null : /* @__PURE__ */ g.jsx(
                    "div",
                    {
                      onPointerDown: (re) => Cn(Z.name, re),
                      className: "absolute right-0 top-0 h-full w-1.5 cursor-col-resize hover:bg-main/50"
                    }
                  )
                ]
              },
              Z.name
            );
          }) }),
          /* @__PURE__ */ g.jsx("div", { style: { height: ea.getTotalSize(), minWidth: _n }, className: "relative shrink-0", children: ea.getVirtualItems().map((Z) => {
            const V = Ke[Z.index], ie = Ut ? td(Ut, V?.[Ut.column], Rg) : null;
            return /* @__PURE__ */ g.jsx(
              "div",
              {
                className: Ne(
                  "absolute left-0 right-0 flex border-b border-chrome-border/30",
                  Z.index % 2 === 0 ? "bg-transparent" : "bg-foreground/[0.025]"
                ),
                style: {
                  transform: `translateY(${Z.start}px)`,
                  height: _,
                  minWidth: _n,
                  ...ie ? { background: ie } : {}
                },
                children: o.map((X, re) => {
                  const ue = m.indexOf(V), Ze = `${ue}:${X.name}`, ee = ye.has(Ze) ? ye.get(Ze) : V?.[X.name], Ye = nd(X), De = S && B.has(X.name) && Pf(ee), Bt = te.has(X.name) && ee != null, ta = cl(X) && ee != null, kt = Dl.get(X.name), wt = kt ? kt.values.has(String(ee)) : !1, ca = Rl.get(X.name)?.has(String(ee)) ?? !1, ga = z(X.name), Or = xe != null && xe.rowIdx === ue && xe.col === X.name, ul = R.has(Ze), fl = me && !wt && !ca && !ul ? Ge.get(X.name) : void 0, On = fl ? td(fl, ee, fl.kind === "numeric" ? Zg : Dg) : null;
                  let dl = null;
                  const aa = Et.get(X.name);
                  if (aa && ee != null) {
                    const Te = Number(ee);
                    if (Number.isFinite(Te))
                      if (aa === "pct") {
                        const At = _r.get(X.name) ?? 0;
                        At !== 0 && (dl = `${(Te / At * 100).toFixed(1)}%`);
                      } else if (Z.index > 0) {
                        const At = Number(Ke[Z.index - 1]?.[X.name]);
                        if (Number.isFinite(At)) {
                          const ql = Number((Te - At).toPrecision(12));
                          dl = /* @__PURE__ */ g.jsxs("span", { style: ql === 0 ? void 0 : { color: ql > 0 ? "var(--success)" : "var(--danger)" }, children: [
                            ql > 0 ? "+" : "",
                            ia(ql)
                          ] });
                        }
                      } else
                        dl = /* @__PURE__ */ g.jsx("span", { className: "text-chrome-text/40", children: "·" });
                  }
                  const Ie = typeof ee == "string" && Ug(ee) ? ee.trim() : null, mt = !!fe && !!de?.has(X.name) && ee != null;
                  return Or ? /* @__PURE__ */ g.jsx(
                    "div",
                    {
                      className: "border-r border-chrome-border/30 bg-main/10 px-0.5 py-0",
                      style: { width: Ll[re] },
                      children: /* @__PURE__ */ g.jsx(
                        "input",
                        {
                          autoFocus: !0,
                          value: xe.draft,
                          onChange: (Te) => tt((At) => At && { ...At, draft: Te.target.value }),
                          onKeyDown: (Te) => {
                            Te.key === "Enter" ? (Te.preventDefault(), q(V, ue, X, xe.draft)) : Te.key === "Escape" && (Te.preventDefault(), tt(null));
                          },
                          onBlur: () => tt(null),
                          className: Ne(
                            "h-full w-full bg-transparent text-[12px] text-foreground outline-none ring-1 ring-inset ring-main/60",
                            Ye ? "text-right tabular-nums" : "text-left"
                          )
                        }
                      )
                    },
                    X.name
                  ) : /* @__PURE__ */ g.jsxs(
                    "div",
                    {
                      onDoubleClick: (Te) => {
                        !ga || ul || (Te.preventDefault(), Te.stopPropagation(), tt({ rowIdx: ue, col: X.name, draft: ee == null ? "" : String(ee) }));
                      },
                      onClick: (Te) => {
                        if (T) {
                          Te.preventDefault(), Te.stopPropagation(), T(X, ee);
                          return;
                        }
                        if (!b) return;
                        Te.preventDefault(), Te.stopPropagation();
                        const At = Te.metaKey || Te.ctrlKey;
                        b(X.name, ee, X.dataTypeId, "in", At, {
                          schema: X.sourceSchema,
                          table: X.sourceTable,
                          column: X.sourceColumn
                        });
                      },
                      onContextMenu: (Te) => yt(Te, V, m.indexOf(V), X),
                      className: Ne(
                        "group/cell relative border-r border-chrome-border/30 px-2 py-0.5 text-[12px]",
                        De ? "flex items-center overflow-hidden" : "truncate",
                        (Bt || ta) && "font-mono",
                        Ye ? "text-right tabular-nums" : "text-left",
                        ee == null ? "text-chrome-text/40 italic" : "text-foreground",
                        (b || T) && "cursor-pointer hover:bg-main/10",
                        ga && "hover:ring-1 hover:ring-inset hover:ring-main/35",
                        ul && "animate-pulse text-chrome-text/50",
                        wt && (kt.cascade ? "bg-main/25 text-foreground" : "bg-main/15 text-foreground ring-1 ring-inset ring-main/55"),
                        ca && "bg-rvbbit-accent/20 text-foreground ring-1 ring-inset ring-rvbbit-accent/60"
                      ),
                      style: {
                        width: Ll[re],
                        // Taller thumb rows: keep single-line text cells vertically
                        // centered without switching them to flex (truncate needs
                        // block layout).
                        ...S && !De ? { lineHeight: `${_ - 6}px` } : {},
                        ...On ? { background: On } : {}
                      },
                      title: ga ? `${_l(ee, X)} — double-click to edit · Enter saves · Esc cancels` : T ? `${_l(ee, X)} — click to filter the dashboard` : b ? `${_l(ee, X)} — click to select · ⌘ filter` : _l(ee, X),
                      children: [
                        ee == null ? "∅" : De ? /* @__PURE__ */ g.jsx(Yg, { portalContainer: i, src: String(ee).trim() }) : dl ?? (Bt ? /* @__PURE__ */ g.jsx(cd, { value: ee, structured: Y }) : ta ? /* @__PURE__ */ g.jsx(od, { value: ee }) : ia(ee)),
                        Ie || mt ? /* @__PURE__ */ g.jsxs("span", { className: "absolute right-0.5 top-1/2 flex -translate-y-1/2 items-center gap-0.5 opacity-0 transition-opacity group-hover/cell:opacity-100", children: [
                          mt ? /* @__PURE__ */ g.jsx(
                            "button",
                            {
                              type: "button",
                              title: "Semantic receipt — trace this value's model call",
                              onClick: (Te) => {
                                Te.preventDefault(), Te.stopPropagation(), fe?.({ row: V, rowIndex: ue, column: X, value: ee });
                              },
                              className: "rounded border border-chrome-border/60 bg-chrome-bg/90 px-1 text-[10px] leading-4 text-rvbbit-accent hover:text-foreground",
                              children: "✦"
                            }
                          ) : null,
                          Ie ? /* @__PURE__ */ g.jsx(
                            "a",
                            {
                              href: Ie,
                              target: "_blank",
                              rel: "noreferrer",
                              title: `Open ${Ie}`,
                              onClick: (Te) => Te.stopPropagation(),
                              className: "rounded border border-chrome-border/60 bg-chrome-bg/90 px-1 text-[10px] leading-4 text-chrome-text hover:text-foreground",
                              children: "↗"
                            }
                          ) : null
                        ] }) : null
                      ]
                    },
                    X.name
                  );
                })
              },
              Z.key
            );
          }) })
        ] })
      }
    ),
    /* @__PURE__ */ g.jsx(Ag, { portalContainer: i, state: K, onClose: () => U(null) })
  ] });
}
function Yg({ src: i, portalContainer: f }) {
  const [u, o] = s.useState(!1), [m, p] = s.useState(null);
  return s.useEffect(() => {
    o(!1), p(null);
  }, [i]), u ? /* @__PURE__ */ g.jsx("span", { className: "min-w-0 truncate", children: i }) : /* @__PURE__ */ g.jsxs(g.Fragment, { children: [
    /* @__PURE__ */ g.jsx(
      "img",
      {
        src: i,
        alt: "",
        loading: "lazy",
        decoding: "async",
        referrerPolicy: "no-referrer",
        draggable: !1,
        onError: () => o(!0),
        onMouseEnter: (h) => p({ x: h.clientX, y: h.clientY }),
        onMouseLeave: () => p(null),
        className: "max-h-[38px] max-w-[220px] rounded-sm object-contain"
      }
    ),
    m ? Hs.createPortal(
      /* @__PURE__ */ g.jsx(
        "div",
        {
          className: "pointer-events-none fixed z-[9999]",
          style: {
            left: Math.min(m.x + 18, Math.max(8, window.innerWidth - 448)),
            top: Math.min(m.y + 14, Math.max(8, window.innerHeight - 388))
          },
          children: /* @__PURE__ */ g.jsx(
            "img",
            {
              src: i,
              alt: "",
              referrerPolicy: "no-referrer",
              className: "max-h-[360px] max-w-[440px] rounded-md border border-chrome-border bg-doc-bg object-contain shadow-2xl"
            }
          )
        }
      ),
      f ?? document.body
    ) : null
  ] });
}
function ad(i, f, u) {
  return i == null ? !1 : (zn(u) ? Hr(i, 12e3, 48, 4) : cl(u) ? zr(i, !0) : String(ia(i))).toLowerCase().includes(f);
}
function ld(i, f) {
  return i.name.toLowerCase().includes(f) || (i.dataTypeName ?? `oid:${i.dataTypeId}`).toLowerCase().includes(f);
}
function nd(i) {
  const f = (i.dataTypeName ?? "").toLowerCase();
  return f ? f.includes("int") || f === "numeric" || f.includes("float") || f === "real" || f === "double" || f === "money" : [20, 21, 23, 700, 701, 1700].includes(i.dataTypeId);
}
function zn(i) {
  const f = i.dataTypeName?.toLowerCase().trim() ?? "";
  return i.dataTypeId === 114 || i.dataTypeId === 3802 || f === "json" || f === "jsonb" || f.endsWith(" json") || f.endsWith(" jsonb");
}
function cl(i) {
  const f = i.dataTypeName?.toLowerCase().trim() ?? "";
  return i.dataTypeId === 1186 || f === "interval" || f.endsWith(" interval");
}
function Pd(i) {
  const f = Array.from(Es(i.name)).length, u = Array.from(i.dataTypeName ?? `oid:${i.dataTypeId}`).length, o = f * 7.4, m = Math.min(110, u * 5.6 + 10);
  return Math.max(kg, Math.ceil(o + m + 46));
}
function rd(i, f) {
  const u = Pd(i);
  if (zn(i)) return Math.max(u, Hg);
  const m = f.slice(0, 20).reduce((h, v) => {
    const x = v[i.name], b = x == null ? 1 : cl(i) ? zr(x).length : String(ia(x)).length;
    return Math.max(h, b);
  }, i.name.length), p = Math.min(zg, m * 7 + 24);
  return Math.max(u, p);
}
function ys(i) {
  const f = i.reduce((u, o) => Math.max(u, o.name.length + (o.dataTypeName?.length ?? 0) + 4), 12);
  return Math.min(640, Math.max(180, f * 7 + 28));
}
async function id(i) {
  try {
    await navigator.clipboard.writeText(i);
  } catch {
  }
}
function Xg(i) {
  return typeof i == "string" ? i : i != null && typeof i == "object" ? tm(i) : ia(i);
}
function _l(i, f) {
  return i == null ? "null" : zn(f) ? Hr(i, 800, 12, 3) : cl(f) ? zr(i, !0) : String(ia(i));
}
function od({ value: i, compact: f = !1 }) {
  const u = Wd(i);
  if (!u) return /* @__PURE__ */ g.jsx("span", { className: "block max-w-full truncate", children: ia(i) });
  const o = f ? u.slice(0, 3) : u;
  return /* @__PURE__ */ g.jsxs(
    "span",
    {
      "data-rvbbit-interval-preview": !0,
      className: "inline-flex max-w-full items-baseline gap-1.5 overflow-hidden whitespace-nowrap tabular-nums",
      children: [
        o.map((m, p) => /* @__PURE__ */ g.jsxs("span", { className: "inline-flex shrink-0 items-baseline gap-px", children: [
          /* @__PURE__ */ g.jsx("span", { children: m.value }),
          /* @__PURE__ */ g.jsx("span", { className: "text-[9px] uppercase tracking-wide text-chrome-text/55", children: m.shortUnit })
        ] }, `${p}:${m.shortUnit}`)),
        o.length < u.length ? /* @__PURE__ */ g.jsx("span", { className: "text-chrome-text/45", children: "…" }) : null
      ]
    }
  );
}
function cd({
  value: i,
  structured: f,
  compact: u = !1
}) {
  if (!f)
    return /* @__PURE__ */ g.jsx("span", { "data-rvbbit-json-preview": "text", className: "block max-w-full truncate", children: Hr(i, u ? 240 : 600, 24, 3) });
  const o = em(i), m = u ? 1 : Tg;
  if (Array.isArray(o)) {
    const p = o.slice(0, m);
    return /* @__PURE__ */ g.jsxs("span", { "data-rvbbit-json-preview": "tree", className: "block max-w-full truncate text-[10px]", children: [
      /* @__PURE__ */ g.jsx(Da, { text: "[" }),
      p.map((h, v) => /* @__PURE__ */ g.jsxs("span", { children: [
        v > 0 ? /* @__PURE__ */ g.jsx(Da, { text: ", " }) : null,
        /* @__PURE__ */ g.jsx(ws, { value: h })
      ] }, v)),
      o.length > p.length ? /* @__PURE__ */ g.jsx(Da, { text: p.length > 0 ? ", …" : "…" }) : null,
      /* @__PURE__ */ g.jsx(Da, { text: "]" })
    ] });
  }
  if (o != null && typeof o == "object") {
    const { entries: p, hasMore: h } = Qg(o, m);
    return /* @__PURE__ */ g.jsxs("span", { "data-rvbbit-json-preview": "tree", className: "block max-w-full truncate text-[10px]", children: [
      /* @__PURE__ */ g.jsx(Da, { text: "{" }),
      p.map(([v, x], b) => /* @__PURE__ */ g.jsxs("span", { children: [
        b > 0 ? /* @__PURE__ */ g.jsx(Da, { text: ", " }) : null,
        /* @__PURE__ */ g.jsx("span", { style: { color: "var(--syntax-identifier)" }, children: jl(v, 42) }),
        /* @__PURE__ */ g.jsx(Da, { text: ": " }),
        /* @__PURE__ */ g.jsx(ws, { value: x })
      ] }, `${b}:${v}`)),
      h ? /* @__PURE__ */ g.jsx(Da, { text: p.length > 0 ? ", …" : "…" }) : null,
      /* @__PURE__ */ g.jsx(Da, { text: "}" })
    ] });
  }
  return /* @__PURE__ */ g.jsx("span", { "data-rvbbit-json-preview": "tree", className: "block max-w-full truncate text-[10px]", children: /* @__PURE__ */ g.jsx(ws, { value: o }) });
}
function Da({ text: i }) {
  return /* @__PURE__ */ g.jsx("span", { style: { color: "var(--syntax-operator)" }, children: i });
}
function ws({ value: i }) {
  return i == null ? /* @__PURE__ */ g.jsx("span", { style: { color: "var(--syntax-keyword)" }, children: "null" }) : typeof i == "boolean" ? /* @__PURE__ */ g.jsx("span", { style: { color: "var(--syntax-keyword)" }, children: i ? "true" : "false" }) : typeof i == "number" || typeof i == "bigint" ? /* @__PURE__ */ g.jsx("span", { style: { color: "var(--syntax-number)" }, children: String(i) }) : typeof i == "string" ? /* @__PURE__ */ g.jsx("span", { style: { color: "var(--syntax-string)" }, children: JSON.stringify(jl(i, If)) }) : Array.isArray(i) ? /* @__PURE__ */ g.jsxs("span", { style: { color: "var(--syntax-operator)" }, children: [
    "[",
    /* @__PURE__ */ g.jsx("span", { style: { color: "var(--syntax-number)" }, children: i.length }),
    "]"
  ] }) : typeof i == "object" ? /* @__PURE__ */ g.jsx("span", { style: { color: "var(--syntax-operator)" }, children: "{…}" }) : /* @__PURE__ */ g.jsx("span", { style: { color: "var(--syntax-foreground)" }, children: jl(String(i), If) });
}
function em(i) {
  if (typeof i != "string") return i;
  const f = i.trim();
  if (f.length > Ng || !(f.startsWith("{") && f.endsWith("}") || f.startsWith("[") && f.endsWith("]")))
    return i;
  try {
    return JSON.parse(f);
  } catch {
    return i;
  }
}
function Qg(i, f) {
  const u = [];
  let o = !1;
  for (const m in i)
    if (Object.prototype.hasOwnProperty.call(i, m)) {
      if (u.length >= f) {
        o = !0;
        break;
      }
      u.push([m, i[m]]);
    }
  return { entries: u, hasMore: o };
}
function Hr(i, f, u = 24, o = 3) {
  const m = em(i), p = /* @__PURE__ */ new WeakSet();
  let h = 0;
  const v = (x, b) => {
    if (h += 1, h > u) return "…";
    if (x == null) return "null";
    if (typeof x == "string") return JSON.stringify(jl(x, Math.min(f, 320)));
    if (typeof x == "number" || typeof x == "boolean") return String(x);
    if (typeof x == "bigint") return JSON.stringify(x.toString());
    if (x instanceof Date) return JSON.stringify(x.toISOString());
    if (typeof x != "object") return JSON.stringify(jl(String(x), 160));
    if (p.has(x)) return '"[Circular]"';
    if (p.add(x), Array.isArray(x)) {
      if (b >= o) return x.length > 0 ? "[…]" : "[]";
      const T = [];
      for (let L = 0; L < x.length && h < u; L += 1)
        T.push(v(x[L], b + 1));
      return T.length < x.length && T.push("…"), `[${T.join(", ")}]`;
    }
    if (b >= o) return "{…}";
    const D = [];
    let C = !1;
    for (const T in x)
      if (Object.prototype.hasOwnProperty.call(x, T)) {
        if (h >= u) {
          C = !0;
          break;
        }
        D.push(`${JSON.stringify(jl(T, 120))}: ${v(x[T], b + 1)}`);
      }
    return C && D.push("…"), `{${D.join(", ")}}`;
  };
  return jl(v(m, 0), f);
}
function jl(i, f) {
  return i.length <= f ? i : `${i.slice(0, Math.max(0, f - 1))}…`;
}
function tm(i) {
  try {
    return JSON.stringify(i, (f, u) => typeof u == "bigint" ? u.toString() : u, 2);
  } catch {
    return String(i);
  }
}
const am = /* @__PURE__ */ new Set([
  20,
  // int8
  21,
  // int2
  23,
  // int4
  700,
  // float4
  701,
  // float8
  790,
  // money
  1700
  // numeric
]);
function Kg(i) {
  if (i.dataTypeId != null && am.has(i.dataTypeId)) return "metric";
  const f = i.type?.toLowerCase() ?? "";
  return /\b(int|integer|bigint|smallint|numeric|decimal|double|float|real|money)\b/.test(f) || f.includes("number") ? "metric" : "dimension";
}
function Jg(i) {
  return i.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "") || "value";
}
function $g(i) {
  return i.role === "metric" || i.dataTypeId != null && am.has(i.dataTypeId) ? !0 : Kg({ type: i.type, dataTypeId: i.dataTypeId }) === "metric";
}
function Hn(i) {
  return i.displayLabel ? i.displayLabel : i.alias === "row_count" ? "count" : i.agg === "count_distinct" ? `distinct(${i.column?.name ?? "*"})` : `${i.agg}(${i.column?.name ?? "*"})`;
}
function Fg(i, f) {
  return `${i}:${f?.name ?? "*"}`;
}
function Wg(i, f) {
  const u = Jg(f.name);
  return i === "count_distinct" ? `distinct_${u}` : `${i}_${u}`;
}
function Ig(i, f) {
  if (!f.has(i)) return i;
  for (let u = 2; u < 1e3; u += 1) {
    const o = `${i}_${u}`;
    if (!f.has(o)) return o;
  }
  return `${i}_${f.size}`;
}
const Pg = [
  "sum",
  "avg",
  "min",
  "max",
  "count",
  "count_distinct",
  "median",
  "stddev",
  "variance"
], e6 = ["count", "count_distinct"];
function t6(i) {
  return i.column ? $g(i.column) ? Pg : e6 : ["count"];
}
function a6(i, f) {
  const u = i.measures.findIndex((v) => v.id === f);
  if (u < 0) return i;
  const o = i.measures[u];
  if (!o.column) return i;
  const m = t6(o), p = m.indexOf(o.agg), h = new Set(i.measures.map((v) => v.id));
  for (let v = 1; v <= m.length; v += 1) {
    const x = m[(p + v) % m.length], b = Fg(x, o.column);
    if (b !== o.id && h.has(b)) continue;
    const D = new Set(i.measures.filter((Q, de) => de !== u).map((Q) => Q.alias)), C = Ig(Wg(x, o.column), D), T = [...i.measures];
    T[u] = { id: b, column: o.column, agg: x, alias: C };
    const L = i.having?.map((Q) => Q.measureId === f ? { ...Q, measureId: b } : Q), G = i.limit?.byMeasureId === f ? { ...i.limit, byMeasureId: b } : i.limit;
    return im({ ...i, measures: T, having: L, limit: G });
  }
  return i;
}
function lm(i, f) {
  const u = f.toLowerCase(), o = i.groupBy.filter((p) => p.column.name.toLowerCase() !== u);
  if (o.length === i.groupBy.length) return i;
  const m = i.orderBy?.filter((p) => p.ref.toLowerCase() !== u);
  return { ...i, groupBy: o, orderBy: m?.length ? m : void 0 };
}
function l6(i, f, u) {
  const o = f.toLowerCase();
  let m = !1;
  const p = i.groupBy.map((h) => h.column.name.toLowerCase() !== o || h.grain === u ? h : (m = !0, { ...h, grain: u }));
  return m ? { ...i, groupBy: p } : i;
}
function n6(i, f) {
  const u = i.measures.find((v) => v.id === f);
  if (!u) return i;
  const o = i.measures.filter((v) => v.id !== f), m = i.orderBy?.filter((v) => v.ref.toLowerCase() !== u.alias.toLowerCase()), p = i.having?.filter((v) => v.measureId !== f), h = i.limit?.byMeasureId === f ? { ...i.limit, byMeasureId: void 0 } : i.limit;
  return im({
    ...i,
    measures: o,
    orderBy: m?.length ? m : void 0,
    having: p?.length ? p : void 0,
    limit: h
  });
}
function r6(i, f, u, o) {
  const m = (i.having ?? []).filter((p) => p.measureId !== f);
  return m.push({ measureId: f, op: u, value: o }), { ...i, having: m };
}
function i6(i, f) {
  const u = i.having?.filter((o) => o.measureId !== f);
  return { ...i, having: u?.length ? u : void 0 };
}
function o6(i, f) {
  return i.orderBy?.find((o) => o.ref.toLowerCase() === f.toLowerCase())?.dir ?? null;
}
function c6(i, f) {
  return (i.orderBy ?? []).findIndex((u) => u.ref.toLowerCase() === f.toLowerCase());
}
function sd(i, f) {
  const u = i.orderBy ?? [], o = u.findIndex((p) => p.ref.toLowerCase() === f.toLowerCase());
  let m;
  return o < 0 ? m = [...u, { ref: f, dir: "asc" }] : u[o].dir === "asc" ? (m = [...u], m[o] = { ref: f, dir: "desc" }) : m = u.filter((p, h) => h !== o), { ...i, orderBy: m.length ? m : void 0 };
}
function s6(i, f) {
  return { ...i, limit: f };
}
function nm(i) {
  return i.limit ? { ...i, limit: null } : i;
}
function u6(i) {
  return i.pivot ? { ...i, pivot: null } : i;
}
function ud(i, f) {
  const u = f.toLowerCase();
  return (i.filters ?? []).filter((o) => o.column.name.toLowerCase() === u);
}
function Cs(i, f, u) {
  const o = f.toLowerCase(), p = [...(i.filters ?? []).filter((h) => h.column.name.toLowerCase() !== o), ...u];
  return { ...i, filters: p.length ? p : void 0 };
}
function As(i, f) {
  return Cs(i, f, []);
}
function rm(i) {
  if (i.length === 0) return "";
  const f = (x) => i.find((b) => b.op === x), u = f("in");
  if (u) return `in ${u.values?.length ?? 0}`;
  const o = f("not_in");
  if (o) return `not in ${o.values?.length ?? 0}`;
  if (f("is_null")) return "is null";
  if (f("not_null")) return "not null";
  const m = f("gte") ?? f("gt"), p = f("lte") ?? f("lt");
  if (m && p) return `${m.value} – ${p.value}`;
  if (m) return `≥ ${m.value}`;
  if (p) return `≤ ${p.value}`;
  const h = f("eq");
  if (h) return `= ${h.value}`;
  const v = f("neq");
  return v ? `≠ ${v.value}` : `${i.length} filter${i.length === 1 ? "" : "s"}`;
}
function im(i) {
  if (!i.pivot?.measureIds) return i;
  const f = new Set(i.measures.map((o) => o.id)), u = i.pivot.measureIds.filter((o) => f.has(o));
  return { ...i, pivot: { ...i.pivot, measureIds: u.length ? u : void 0 } };
}
const f6 = ["year", "quarter", "month", "week", "day", "hour"], d6 = [">", ">=", "<", "<=", "=", "!="], Tr = "group inline-flex max-w-[240px] items-center gap-1 rounded-full border border-chrome-border/50 bg-doc-bg px-1.5 py-0.5 font-mono text-[10px] text-foreground transition-colors", Pi = "absolute left-0 top-full z-50 mt-1 rounded-md border border-chrome-border bg-chrome-bg/95 p-2 shadow-lg backdrop-blur";
function om({ sort: i, onCycle: f }) {
  const u = i.dir === "desc" ? a2 : t2;
  return /* @__PURE__ */ g.jsxs(
    "span",
    {
      role: "button",
      onClick: (o) => {
        o.stopPropagation(), f();
      },
      title: i.dir ? `sorted ${i.dir} — click to ${i.dir === "asc" ? "flip to desc" : "clear"}` : "sort",
      className: Ne(
        "ml-0.5 inline-flex items-center gap-0.5 rounded-full",
        i.dir ? "bg-main/15 px-1 text-[9px] text-main hover:bg-main/25" : "h-3.5 w-3.5 justify-center text-chrome-text/40 opacity-0 transition-opacity hover:bg-foreground/10 hover:text-foreground group-hover:opacity-100"
      ),
      children: [
        /* @__PURE__ */ g.jsx(u, { className: "h-2.5 w-2.5" }),
        i.dir && i.badge ? /* @__PURE__ */ g.jsx("span", { children: i.badge }) : null
      ]
    }
  );
}
function Nr({ onRemove: i }) {
  return /* @__PURE__ */ g.jsx(
    "span",
    {
      role: "button",
      "aria-label": "remove",
      onClick: i,
      className: "ml-0.5 inline-grid h-3.5 w-3.5 place-items-center rounded-full text-chrome-text/55 hover:bg-danger/20 hover:text-danger",
      children: /* @__PURE__ */ g.jsx(r2, { className: "h-2.5 w-2.5" })
    }
  );
}
function eo() {
  const [i, f] = s.useState(!1), u = s.useRef(null);
  return s.useEffect(() => {
    if (!i) return;
    const o = (m) => {
      m.composedPath().includes(u.current) || f(!1);
    };
    return document.addEventListener("mousedown", o), () => document.removeEventListener("mousedown", o);
  }, [i]), { open: i, setOpen: f, ref: u };
}
function cm({ value: i, onPick: f }) {
  return /* @__PURE__ */ g.jsx("div", { className: Ne(Pi, "min-w-[96px] p-1"), children: f6.map((u) => /* @__PURE__ */ g.jsx(
    "button",
    {
      type: "button",
      onClick: () => f(u),
      className: Ne(
        "block w-full rounded px-2 py-0.5 text-left font-mono text-[10px] capitalize",
        u === i ? "bg-main/20 text-foreground" : "text-chrome-text hover:bg-foreground/10"
      ),
      children: u
    },
    u
  )) });
}
function m6({
  term: i,
  filters: f,
  kind: u,
  sort: o,
  onCycleSort: m,
  onEdit: p,
  onProbeValues: h
}) {
  const v = s.useRef(null), [x, b] = s.useState(null);
  s.useEffect(() => {
    if (!x) return;
    const T = (L) => {
      L.composedPath().includes(v.current) || b(null);
    };
    return document.addEventListener("mousedown", T), () => document.removeEventListener("mousedown", T);
  }, [x]);
  const D = rm(f), C = i.column.name;
  return /* @__PURE__ */ g.jsxs("span", { ref: v, className: "relative inline-flex", children: [
    /* @__PURE__ */ g.jsxs("span", { className: Tr, title: `GROUP BY ${C}${i.grain ? ` (${i.grain})` : ""}`, children: [
      /* @__PURE__ */ g.jsx(l2, { className: "h-3 w-3 shrink-0 text-main/75" }),
      /* @__PURE__ */ g.jsx("span", { className: "truncate", children: C }),
      i.grain ? /* @__PURE__ */ g.jsxs(
        "span",
        {
          role: "button",
          onClick: (T) => {
            T.stopPropagation(), b((L) => L === "grain" ? null : "grain");
          },
          className: "ml-0.5 inline-flex cursor-pointer items-center gap-0.5 rounded-full bg-foreground/[0.07] px-1 text-[9px] text-chrome-text/85 hover:bg-foreground/[0.14] hover:text-foreground",
          title: "change grain",
          children: [
            i.grain,
            /* @__PURE__ */ g.jsx(ks, { className: "h-2 w-2" })
          ]
        }
      ) : null,
      D ? /* @__PURE__ */ g.jsx(
        "span",
        {
          role: "button",
          onClick: (T) => {
            T.stopPropagation(), b((L) => L === "filter" ? null : "filter");
          },
          className: "ml-0.5 cursor-pointer rounded-full bg-main/15 px-1 text-[9px] text-main hover:bg-main/25",
          title: "edit filter",
          children: D
        }
      ) : /* @__PURE__ */ g.jsx(
        "span",
        {
          role: "button",
          onClick: (T) => {
            T.stopPropagation(), b((L) => L === "filter" ? null : "filter");
          },
          className: "ml-0.5 inline-grid h-3.5 w-3.5 place-items-center rounded-full text-chrome-text/40 opacity-0 transition-opacity hover:bg-foreground/10 hover:text-foreground group-hover:opacity-100",
          title: "filter (WHERE)",
          children: /* @__PURE__ */ g.jsx(zs, { className: "h-2.5 w-2.5" })
        }
      ),
      /* @__PURE__ */ g.jsx(om, { sort: o, onCycle: m }),
      /* @__PURE__ */ g.jsx(Nr, { onRemove: () => p((T) => lm(T, C)) })
    ] }),
    x === "grain" && i.grain ? /* @__PURE__ */ g.jsx(cm, { value: i.grain, onPick: (T) => {
      b(null), p((L) => l6(L, C, T));
    } }) : null,
    x === "filter" ? /* @__PURE__ */ g.jsx(
      sm,
      {
        column: i.column,
        kind: u,
        filters: f,
        onProbeValues: h,
        onApply: (T) => {
          b(null), p((L) => Cs(L, C, T));
        },
        onClear: () => {
          b(null), p((T) => As(T, C));
        }
      }
    ) : null
  ] });
}
function p6({
  column: i,
  filters: f,
  kind: u,
  onEdit: o,
  onProbeValues: m
}) {
  const { open: p, setOpen: h, ref: v } = eo(), x = i.name;
  return /* @__PURE__ */ g.jsxs("span", { ref: v, className: "relative inline-flex", children: [
    /* @__PURE__ */ g.jsxs(
      "span",
      {
        role: "button",
        onClick: () => h((b) => !b),
        className: Ne(Tr, "cursor-pointer hover:border-main/50"),
        title: `WHERE ${x}`,
        children: [
          /* @__PURE__ */ g.jsx(zs, { className: "h-3 w-3 shrink-0 text-main/75" }),
          /* @__PURE__ */ g.jsx("span", { className: "truncate", children: x }),
          /* @__PURE__ */ g.jsx("span", { className: "rounded-full bg-main/15 px-1 text-[9px] text-main", children: rm(f) }),
          /* @__PURE__ */ g.jsx(Nr, { onRemove: (b) => {
            b.stopPropagation(), o((D) => As(D, x));
          } })
        ]
      }
    ),
    p ? /* @__PURE__ */ g.jsx(
      sm,
      {
        column: i,
        kind: u,
        filters: f,
        onProbeValues: m,
        onApply: (b) => {
          h(!1), o((D) => Cs(D, x, b));
        },
        onClear: () => {
          h(!1), o((b) => As(b, x));
        }
      }
    ) : null
  ] });
}
function sm({
  column: i,
  kind: f,
  filters: u,
  onProbeValues: o,
  onApply: m,
  onClear: p
}) {
  return /* @__PURE__ */ g.jsxs("div", { className: Ne(Pi, "min-w-[210px] space-y-1.5"), children: [
    /* @__PURE__ */ g.jsxs("div", { className: "truncate text-[9px] uppercase tracking-wider text-chrome-text/70", children: [
      "filter ",
      i.name
    ] }),
    f === "text" ? /* @__PURE__ */ g.jsx(h6, { column: i, filters: u, probe: o, onApply: m, onClear: p }) : /* @__PURE__ */ g.jsx(g6, { column: i, kind: f, filters: u, onApply: m, onClear: p })
  ] });
}
function um({ canClear: i, onApply: f, onClear: u }) {
  return /* @__PURE__ */ g.jsxs("div", { className: "flex items-center gap-1 pt-0.5", children: [
    /* @__PURE__ */ g.jsx("button", { type: "button", onClick: f, className: "rounded bg-main/20 px-2 py-0.5 text-[10px] font-medium text-foreground hover:bg-main/30", children: "Apply" }),
    i ? /* @__PURE__ */ g.jsx("button", { type: "button", onClick: u, className: "rounded px-2 py-0.5 text-[10px] text-chrome-text hover:bg-danger/15 hover:text-danger", children: "Clear" }) : null
  ] });
}
function h6({
  column: i,
  filters: f,
  probe: u,
  onApply: o,
  onClear: m
}) {
  const p = f.find((J) => J.op === "in"), [h, v] = s.useState(() => {
    const J = /* @__PURE__ */ new Map();
    for (const K of p?.values ?? []) J.set(String(K), K);
    return J;
  }), [x, b] = s.useState(""), [D, C] = s.useState([]), [T, L] = s.useState(!1), [G, Q] = s.useState(""), [de, fe] = s.useState(!1);
  s.useEffect(() => {
    if (!u) return;
    let J = !1;
    const K = setTimeout(async () => {
      L(!0), Q("");
      try {
        const U = await u(i, x);
        if (J) return;
        C(U.values), fe(U.truncated);
      } catch (U) {
        J || Q(U instanceof Error ? U.message : "Could not load values");
      } finally {
        J || L(!1);
      }
    }, x ? 250 : 0);
    return () => {
      J = !0, clearTimeout(K);
    };
  }, [x]);
  const se = s.useMemo(() => {
    const J = [], K = /* @__PURE__ */ new Set();
    for (const U of h.values())
      J.push(U), K.add(String(U));
    for (const U of D) {
      const xe = String(U);
      K.has(xe) || (J.push(U), K.add(xe));
    }
    return J;
  }, [h, D]), be = (J) => v((K) => {
    const U = new Map(K), xe = String(J);
    return U.has(xe) ? U.delete(xe) : U.set(xe, J), U;
  }), Oe = () => {
    const J = x.trim();
    J && (v((K) => new Map(K).set(J, J)), b(""));
  }, Le = () => {
    const J = [...h.values()];
    J.length ? o([{ column: i, op: "in", values: J }]) : m();
  };
  return /* @__PURE__ */ g.jsxs("div", { className: "space-y-1.5", children: [
    /* @__PURE__ */ g.jsxs("div", { className: "flex items-center gap-1 rounded border border-chrome-border/60 bg-doc-bg px-1", children: [
      /* @__PURE__ */ g.jsx(Ad, { className: "h-3 w-3 shrink-0 text-chrome-text/55" }),
      /* @__PURE__ */ g.jsx(
        "input",
        {
          value: x,
          autoFocus: !0,
          onChange: (J) => b(J.target.value),
          onKeyDown: (J) => {
            J.key === "Enter" && Oe();
          },
          placeholder: u ? "search values…" : "type a value, Enter",
          className: "w-full bg-transparent py-0.5 text-[10px] text-foreground outline-none placeholder:text-chrome-text/40"
        }
      )
    ] }),
    /* @__PURE__ */ g.jsx("div", { className: "max-h-40 overflow-auto rounded border border-chrome-border/40", children: T && se.length === 0 ? /* @__PURE__ */ g.jsx("div", { className: "px-2 py-2 text-[10px] text-chrome-text/50", children: "loading…" }) : se.length === 0 ? /* @__PURE__ */ g.jsx("div", { className: "px-2 py-2 text-[10px] text-chrome-text/50", children: "no values" }) : se.map((J) => {
      const K = h.has(String(J));
      return /* @__PURE__ */ g.jsxs(
        "button",
        {
          type: "button",
          onClick: () => be(J),
          className: Ne(
            "flex w-full items-center gap-1.5 px-2 py-0.5 text-left font-mono text-[10px]",
            K ? "bg-main/15 text-foreground" : "text-chrome-text hover:bg-foreground/10"
          ),
          children: [
            /* @__PURE__ */ g.jsx("span", { className: Ne("inline-grid h-3 w-3 shrink-0 place-items-center rounded-sm border", K ? "border-main bg-main/30" : "border-chrome-border/70"), children: K ? /* @__PURE__ */ g.jsx("span", { className: "h-1.5 w-1.5 rounded-[1px] bg-main" }) : null }),
            /* @__PURE__ */ g.jsx("span", { className: "truncate", children: J === null ? "∅ null" : String(J) })
          ]
        },
        String(J)
      );
    }) }),
    G ? /* @__PURE__ */ g.jsx("div", { role: "alert", className: "text-[10px] text-danger", children: G }) : null,
    de ? /* @__PURE__ */ g.jsx("div", { className: "text-[9px] text-chrome-text/45", children: "showing most common — search to narrow" }) : null,
    /* @__PURE__ */ g.jsxs("div", { className: "flex items-center justify-between", children: [
      /* @__PURE__ */ g.jsxs("span", { className: "text-[9px] text-chrome-text/55", children: [
        h.size,
        " selected"
      ] }),
      /* @__PURE__ */ g.jsx(um, { canClear: !!p, onApply: Le, onClear: m })
    ] })
  ] });
}
function g6({
  column: i,
  kind: f,
  filters: u,
  onApply: o,
  onClear: m
}) {
  const p = u.find((G) => G.op === "gte" || G.op === "gt"), h = u.find((G) => G.op === "lte" || G.op === "lt"), [v, x] = s.useState(p?.value != null ? String(p.value) : ""), [b, D] = s.useState(h?.value != null ? String(h.value) : ""), C = f === "date" ? "date" : "number", T = (G) => {
    if (f === "numeric") {
      const Q = Number(G);
      return Number.isFinite(Q) ? Q : null;
    }
    return G;
  }, L = () => {
    const G = [], Q = v.trim(), de = b.trim();
    if (Q) {
      const fe = T(Q);
      fe !== null && G.push({ column: i, op: "gte", value: fe });
    }
    if (de) {
      const fe = T(de);
      fe !== null && G.push({ column: i, op: "lte", value: fe });
    }
    G.length ? o(G) : m();
  };
  return /* @__PURE__ */ g.jsxs("div", { className: "space-y-1.5", children: [
    /* @__PURE__ */ g.jsxs("div", { className: "flex items-center gap-1", children: [
      /* @__PURE__ */ g.jsx(
        "input",
        {
          type: C,
          value: v,
          autoFocus: !0,
          onChange: (G) => x(G.target.value),
          onKeyDown: (G) => {
            G.key === "Enter" && L();
          },
          placeholder: "min",
          className: "w-full rounded border border-chrome-border bg-doc-bg px-1.5 py-0.5 font-mono text-[10px] text-foreground outline-none"
        }
      ),
      /* @__PURE__ */ g.jsx("span", { className: "text-chrome-text/50", children: "–" }),
      /* @__PURE__ */ g.jsx(
        "input",
        {
          type: C,
          value: b,
          onChange: (G) => D(G.target.value),
          onKeyDown: (G) => {
            G.key === "Enter" && L();
          },
          placeholder: "max",
          className: "w-full rounded border border-chrome-border bg-doc-bg px-1.5 py-0.5 font-mono text-[10px] text-foreground outline-none"
        }
      )
    ] }),
    /* @__PURE__ */ g.jsx(um, { canClear: !!(p || h), onApply: L, onClear: m })
  ] });
}
function b6({
  measure: i,
  having: f,
  sort: u,
  onCycleSort: o,
  onEdit: m,
  caption: p,
  onCycleAggregate: h,
  fixedAggregate: v = !1
}) {
  const { open: x, setOpen: b, ref: D } = eo(), C = !!i.column && !v;
  return /* @__PURE__ */ g.jsxs("span", { ref: D, className: "relative inline-flex", children: [
    /* @__PURE__ */ g.jsxs(
      "span",
      {
        role: C ? "button" : void 0,
        onClick: C ? h ?? (() => m((T) => a6(T, i.id))) : void 0,
        title: C ? `${Hn(i)} · click to change aggregate` : Hn(i),
        className: Ne(Tr, C && "cursor-pointer hover:border-main/50"),
        children: [
          /* @__PURE__ */ g.jsx(e2, { className: "h-3 w-3 shrink-0 text-chart-3" }),
          /* @__PURE__ */ g.jsx("span", { className: "truncate", children: p || Hn(i) }),
          C ? /* @__PURE__ */ g.jsx(ks, { className: "h-2.5 w-2.5 shrink-0 text-chrome-text/40" }) : null,
          f ? /* @__PURE__ */ g.jsxs(
            "span",
            {
              role: "button",
              onClick: (T) => {
                T.stopPropagation(), b((L) => !L);
              },
              className: "ml-0.5 cursor-pointer rounded-full bg-main/15 px-1 text-[9px] text-main hover:bg-main/25",
              title: "edit filter",
              children: [
                f.op,
                " ",
                f.value
              ]
            }
          ) : /* @__PURE__ */ g.jsx(
            "span",
            {
              role: "button",
              onClick: (T) => {
                T.stopPropagation(), b((L) => !L);
              },
              className: "ml-0.5 inline-grid h-3.5 w-3.5 place-items-center rounded-full text-chrome-text/40 opacity-0 transition-opacity hover:bg-foreground/10 hover:text-foreground group-hover:opacity-100",
              title: "add filter (HAVING)",
              children: /* @__PURE__ */ g.jsx(zs, { className: "h-2.5 w-2.5" })
            }
          ),
          /* @__PURE__ */ g.jsx(om, { sort: u, onCycle: o }),
          /* @__PURE__ */ g.jsx(Nr, { onRemove: () => m((T) => n6(T, i.id)) })
        ]
      }
    ),
    x ? /* @__PURE__ */ g.jsx(
      v6,
      {
        measure: i,
        having: f,
        onApply: (T, L) => {
          b(!1), m((G) => r6(G, i.id, T, L));
        },
        onClear: () => {
          b(!1), m((T) => i6(T, i.id));
        }
      }
    ) : null
  ] });
}
function v6({
  measure: i,
  having: f,
  onApply: u,
  onClear: o
}) {
  const [m, p] = s.useState(f?.op ?? ">"), [h, v] = s.useState(f ? String(f.value) : ""), x = () => {
    const b = Number(h);
    Number.isFinite(b) && h.trim() !== "" && u(m, b);
  };
  return /* @__PURE__ */ g.jsxs("div", { className: Ne(Pi, "min-w-[180px] space-y-1.5"), children: [
    /* @__PURE__ */ g.jsxs("div", { className: "text-[9px] uppercase tracking-wider text-chrome-text/70", children: [
      "keep rows where ",
      Hn(i)
    ] }),
    /* @__PURE__ */ g.jsx("div", { className: "flex flex-wrap gap-0.5", children: d6.map((b) => /* @__PURE__ */ g.jsx(
      "button",
      {
        type: "button",
        onClick: () => p(b),
        className: Ne(
          "rounded border px-1.5 py-0.5 font-mono text-[10px]",
          b === m ? "border-main/60 bg-main/15 text-foreground" : "border-chrome-border/50 text-chrome-text hover:border-main/40"
        ),
        children: b
      },
      b
    )) }),
    /* @__PURE__ */ g.jsx(
      "input",
      {
        type: "number",
        value: h,
        autoFocus: !0,
        onChange: (b) => v(b.target.value),
        onKeyDown: (b) => {
          b.key === "Enter" && x();
        },
        placeholder: "value",
        className: "w-full rounded border border-chrome-border bg-doc-bg px-1.5 py-0.5 font-mono text-[10px] text-foreground outline-none placeholder:text-chrome-text/40"
      }
    ),
    /* @__PURE__ */ g.jsxs("div", { className: "flex items-center gap-1", children: [
      /* @__PURE__ */ g.jsx(
        "button",
        {
          type: "button",
          onClick: x,
          className: "rounded bg-main/20 px-2 py-0.5 text-[10px] font-medium text-foreground hover:bg-main/30",
          children: "Apply"
        }
      ),
      f ? /* @__PURE__ */ g.jsx(
        "button",
        {
          type: "button",
          onClick: o,
          className: "rounded px-2 py-0.5 text-[10px] text-chrome-text hover:bg-danger/15 hover:text-danger",
          children: "Clear"
        }
      ) : null
    ] })
  ] });
}
function x6({ pivot: i, onEdit: f, onRepivot: u }) {
  const { open: o, setOpen: m, ref: p } = eo(), h = i.grain != null && !!u;
  return /* @__PURE__ */ g.jsxs("span", { ref: p, className: "relative inline-flex", children: [
    /* @__PURE__ */ g.jsxs(
      "span",
      {
        className: Tr,
        title: `pivoted across ${i.values.length}${i.truncated ? "+ (capped)" : ""} values of ${i.column.name}${i.grain ? ` (${i.grain})` : ""}`,
        children: [
          /* @__PURE__ */ g.jsx(Cd, { className: "h-3 w-3 shrink-0 text-rvbbit-accent" }),
          /* @__PURE__ */ g.jsxs("span", { className: "truncate", children: [
            i.column.name,
            " · ",
            i.values.length,
            i.truncated ? "+" : ""
          ] }),
          i.grain ? /* @__PURE__ */ g.jsxs(
            "span",
            {
              role: h ? "button" : void 0,
              onClick: h ? (v) => {
                v.stopPropagation(), m((x) => !x);
              } : void 0,
              className: Ne(
                "ml-0.5 inline-flex items-center gap-0.5 rounded-full bg-foreground/[0.07] px-1 text-[9px] text-chrome-text/85",
                h && "cursor-pointer hover:bg-foreground/[0.14] hover:text-foreground"
              ),
              title: h ? "change grain" : i.grain,
              children: [
                i.grain,
                h ? /* @__PURE__ */ g.jsx(ks, { className: "h-2 w-2" }) : null
              ]
            }
          ) : null,
          /* @__PURE__ */ g.jsx(Nr, { onRemove: () => f(u6) })
        ]
      }
    ),
    o && h ? /* @__PURE__ */ g.jsx(cm, { value: i.grain, onPick: (v) => {
      m(!1), u(v);
    } }) : null
  ] });
}
function y6({ spec: i, onEdit: f }) {
  const { open: u, setOpen: o, ref: m } = eo(), p = i.limit ?? null, h = (i.orderBy?.length ?? 0) > 0, v = (() => {
    if (!p) return "";
    const x = i.measures.find((b) => b.id === p.byMeasureId) ?? i.measures.find((b) => b.alias !== "row_count") ?? i.measures[0];
    return x ? Hn(x) : "row_count";
  })();
  return /* @__PURE__ */ g.jsxs("span", { ref: m, className: "relative inline-flex", children: [
    p ? /* @__PURE__ */ g.jsxs("span", { className: Ne(Tr, "cursor-pointer hover:border-main/50"), onClick: () => o((x) => !x), title: "edit Top-N", children: [
      /* @__PURE__ */ g.jsx(Zf, { className: Ne("h-3 w-3 shrink-0 text-main/75", p.dir === "asc" && "rotate-180") }),
      /* @__PURE__ */ g.jsx("span", { className: "truncate", children: h ? `Limit ${p.n}` : `${p.dir === "asc" ? "Bottom" : "Top"} ${p.n} · ${v}` }),
      /* @__PURE__ */ g.jsx(Nr, { onRemove: (x) => {
        x.stopPropagation(), f(nm);
      } })
    ] }) : /* @__PURE__ */ g.jsxs(
      "button",
      {
        type: "button",
        onClick: () => o((x) => !x),
        className: "inline-flex items-center gap-1 rounded-full border border-dashed border-chrome-border/50 px-1.5 py-0.5 text-[10px] text-chrome-text/55 hover:border-main/40 hover:text-foreground",
        title: "limit to the top N rows by a measure",
        children: [
          /* @__PURE__ */ g.jsx(Zf, { className: "h-3 w-3" }),
          " Top N"
        ]
      }
    ),
    u ? /* @__PURE__ */ g.jsx(w6, { spec: i, onEdit: f, close: () => o(!1) }) : null
  ] });
}
function w6({ spec: i, onEdit: f, close: u }) {
  const o = i.limit ?? null, m = i.measures, [p, h] = s.useState(o ? String(o.n) : "10"), [v, x] = s.useState(o?.byMeasureId ?? m[0]?.id ?? ""), [b, D] = s.useState(o?.dir ?? "desc"), C = () => {
    const T = Number(p);
    !Number.isFinite(T) || T < 1 || (f((L) => s6(L, { n: Math.floor(T), byMeasureId: v || void 0, dir: b })), u());
  };
  return /* @__PURE__ */ g.jsxs("div", { className: Ne(Pi, "right-0 left-auto min-w-[200px] space-y-1.5"), children: [
    /* @__PURE__ */ g.jsx("div", { className: "text-[9px] uppercase tracking-wider text-chrome-text/70", children: "top-n rows" }),
    /* @__PURE__ */ g.jsxs("div", { className: "flex items-center gap-1", children: [
      /* @__PURE__ */ g.jsx("div", { className: "flex overflow-hidden rounded border border-chrome-border/50", children: ["desc", "asc"].map((T) => /* @__PURE__ */ g.jsx(
        "button",
        {
          type: "button",
          onClick: () => D(T),
          className: Ne(
            "px-1.5 py-0.5 text-[10px]",
            T === b ? "bg-main/15 text-foreground" : "text-chrome-text hover:bg-foreground/10"
          ),
          children: T === "desc" ? "Top" : "Bottom"
        },
        T
      )) }),
      /* @__PURE__ */ g.jsx(
        "input",
        {
          type: "number",
          value: p,
          onChange: (T) => h(T.target.value),
          onKeyDown: (T) => {
            T.key === "Enter" && C();
          },
          className: "w-16 rounded border border-chrome-border bg-doc-bg px-1.5 py-0.5 font-mono text-[10px] text-foreground outline-none"
        }
      )
    ] }),
    /* @__PURE__ */ g.jsxs("div", { className: "flex items-center gap-1", children: [
      /* @__PURE__ */ g.jsx("span", { className: "text-[9px] uppercase tracking-wider text-chrome-text/70", children: "by" }),
      /* @__PURE__ */ g.jsx(
        "select",
        {
          value: v,
          onChange: (T) => x(T.target.value),
          className: "min-w-0 flex-1 rounded border border-chrome-border bg-doc-bg px-1 py-0.5 font-mono text-[10px] text-foreground outline-none",
          children: m.map((T) => /* @__PURE__ */ g.jsx("option", { value: T.id, children: Hn(T) }, T.id))
        }
      )
    ] }),
    /* @__PURE__ */ g.jsxs("div", { className: "flex items-center gap-1", children: [
      /* @__PURE__ */ g.jsx("button", { type: "button", onClick: C, className: "rounded bg-main/20 px-2 py-0.5 text-[10px] font-medium text-foreground hover:bg-main/30", children: "Apply" }),
      o ? /* @__PURE__ */ g.jsx("button", { type: "button", onClick: () => {
        f(nm), u();
      }, className: "rounded px-2 py-0.5 text-[10px] text-chrome-text hover:bg-danger/15 hover:text-danger", children: "Clear" }) : null
    ] })
  ] });
}
const fd = (i) => ({ name: i.name, role: i.role === "measure" ? "metric" : "dimension", dataTypeId: i.role === "date" ? 1082 : i.role === "measure" ? 701 : 25, type: i.type });
function S6({ value: i, onChange: f, probe: u, toolbar: o, children: m, rowCount: p = !1 }) {
  const { catalog: h, spec: v } = i, [x, b] = s.useState(""), [D, C] = s.useState(null), [T, L] = s.useState(null), [G, Q] = s.useState([]), [de, fe] = s.useState(!1), se = (R) => f(R(v)), be = (R) => h.fields.find(($) => $.name === R) || { name: R, type: "text", role: "dimension", aggregates: [], default_aggregate: null, note: "This field is no longer available." }, Oe = (R) => ({ dir: o6(v, R), badge: (v.orderBy?.length || 0) > 1 ? String(c6(v, R) + 1) : null }), Le = (R) => be(R).role === "date" ? "date" : be(R).role === "measure" ? "numeric" : "text", J = (R, $) => $ === "Values" ? !!R.default_aggregate : $ === "Filters" || R.role !== "measure";
  function K(R, $) {
    const ye = h.fields.find((z) => z.name === R);
    if (!ye || !J(ye, $)) return;
    let he = structuredClone(v);
    const Qe = fd(ye);
    if ($ === "Rows")
      he.pivot?.column.name === R && (he.pivot = null), he.groupBy.some((z) => z.column.name === R) || he.groupBy.push({ column: Qe, ...ye.role === "date" ? { grain: "month" } : {} });
    else if ($ === "Columns")
      he = lm(he, R), he.pivot = { column: Qe, values: [], ...ye.role === "date" ? { grain: "month" } : {} };
    else if ($ === "Values")
      he.measures.some((z) => z.column?.name === R) || he.measures.push({ id: "value:" + R, column: Qe, agg: ye.default_aggregate, alias: R });
    else if (!(he.filters || []).some((z) => z.column.name === R)) {
      Q((z) => [.../* @__PURE__ */ new Set([...z, R])]), C(null), fe(!1);
      return;
    }
    C(null), fe(!1), f(he);
  }
  const U = { ...v, measures: v.measures.map((R) => ({ ...R, displayLabel: R.column && be(R.column.name).rule ? R.column.name + " (recomputed)" : void 0 })) }, xe = /* @__PURE__ */ new Set([...G, ...(v.filters || []).map((R) => R.column.name)]);
  function tt(R, $, ye) {
    return /* @__PURE__ */ g.jsxs(
      "section",
      {
        className: "cube-lane cube-lane-" + R.toLowerCase() + (T === R ? " cube-lane-over" : ""),
        "aria-label": R + " shelf",
        onDragOver: (he) => {
          he.dataTransfer.types.includes("application/x-calliope-cube-field") && (he.preventDefault(), L(R));
        },
        onDragLeave: () => L(null),
        onDrop: (he) => {
          he.preventDefault(), L(null), K(he.dataTransfer.getData("application/x-calliope-cube-field"), R);
        },
        children: [
          /* @__PURE__ */ g.jsx("span", { className: "cube-lane-label", children: R }),
          /* @__PURE__ */ g.jsxs("div", { className: "cube-pills", children: [
            $,
            ye && /* @__PURE__ */ g.jsx("span", { className: "cube-empty", children: R === "Columns" ? "Drop a field to pivot" : R === "Filters" ? "Drop a field to filter" : "Drop fields here" })
          ] })
        ]
      }
    );
  }
  return /* @__PURE__ */ g.jsxs("section", { className: "cube-workbench", "aria-label": "Cube query shelves", children: [
    /* @__PURE__ */ g.jsxs("div", { className: "cube-toolbar data-toolbar", children: [
      p && !v.measures.some((R) => !R.column) && /* @__PURE__ */ g.jsx("button", { onClick: () => se((R) => ({ ...R, measures: [...R.measures, { id: "row-count", column: null, agg: "count", alias: "Record count" }] })), children: "+ Count rows" }),
      /* @__PURE__ */ g.jsx("button", { className: "cube-fields-toggle", "aria-expanded": de, "aria-controls": "cube-fields", onClick: () => fe(!de), children: "Fields" }),
      o
    ] }),
    /* @__PURE__ */ g.jsxs("div", { className: "cube-sidebar", children: [
      /* @__PURE__ */ g.jsxs("aside", { id: "cube-fields", className: "cube-fields" + (de ? " cube-fields-open" : ""), "aria-label": "Cube fields", onKeyDown: (R) => {
        R.key === "Escape" && fe(!1);
      }, children: [
        /* @__PURE__ */ g.jsxs("header", { children: [
          /* @__PURE__ */ g.jsxs("strong", { children: [
            "Fields ",
            /* @__PURE__ */ g.jsx("small", { children: h.fields.length })
          ] }),
          /* @__PURE__ */ g.jsx("button", { className: "cube-fields-close", "aria-label": "Close fields", onClick: () => fe(!1), children: "×" })
        ] }),
        /* @__PURE__ */ g.jsx("input", { className: "workspace-field", type: "search", "aria-label": "Find a cube field", value: x, onChange: (R) => b(R.target.value), placeholder: "Find a field…" }),
        /* @__PURE__ */ g.jsx("div", { className: "cube-field-catalog", children: ["dimension", "date", "measure"].filter((R) => h.fields.some(($) => $.role === R)).map((R) => /* @__PURE__ */ g.jsxs("div", { className: "cube-field-group", children: [
          /* @__PURE__ */ g.jsx("h3", { children: { dimension: "Dimensions", date: "Dates", measure: "Measures" }[R] }),
          /* @__PURE__ */ g.jsx("div", { className: "cube-field-list", children: h.fields.filter(($) => $.role === R && $.name.toLowerCase().includes(x.toLowerCase())).map(($) => /* @__PURE__ */ g.jsxs(
            "button",
            {
              className: "cube-field " + (R === "measure" ? "cube-measure" : ""),
              draggable: !0,
              onDragStart: (ye) => {
                ye.dataTransfer.setData("application/x-calliope-cube-field", $.name), ye.dataTransfer.effectAllowed = "copy";
              },
              title: [$.description, $.note].filter(Boolean).join(" "),
              "aria-pressed": D === $.name,
              onClick: () => C(D === $.name ? null : $.name),
              children: [
                R === "measure" ? "Σ" : R === "date" ? "◷" : "◇",
                " ",
                $.name
              ]
            },
            $.name
          )) })
        ] }, R)) }),
        D && /* @__PURE__ */ g.jsxs("div", { className: "cube-field-actions", children: [
          /* @__PURE__ */ g.jsxs("span", { children: [
            "Add ",
            D,
            " to"
          ] }),
          ["Rows", "Columns", "Values", "Filters"].map((R) => /* @__PURE__ */ g.jsx("button", { className: "workspace-control", disabled: !J(be(D), R), onClick: () => K(D, R), children: R }, R))
        ] })
      ] }),
      tt("Values", U.measures.map((R) => {
        const $ = R.column ? be(R.column.name) : { name: "Rows", rule: null, aggregates: ["count"] };
        return /* @__PURE__ */ g.jsx(
          b6,
          {
            measure: R,
            caption: $.rule ? $.name : void 0,
            fixedAggregate: !!$.rule || $.aggregates.length < 2,
            onCycleAggregate: () => se((ye) => ({ ...ye, measures: ye.measures.map((he) => he.id === R.id ? { ...he, agg: $.aggregates[($.aggregates.indexOf(he.agg) + 1) % $.aggregates.length] } : he) })),
            having: v.having?.find((ye) => ye.measureId === R.id),
            sort: Oe(R.alias),
            onCycleSort: () => se((ye) => sd(ye, R.alias)),
            onEdit: se
          },
          R.id
        );
      }), !v.measures.length),
      tt("Filters", [...xe].map((R) => /* @__PURE__ */ g.jsx(p6, { column: fd(be(R)), filters: ud(v, R), kind: Le(R), onEdit: ($) => {
        const ye = $(v);
        ye.filters?.some((he) => he.column.name === R) || Q((he) => he.filter((Qe) => Qe !== R)), f(ye);
      }, onProbeValues: ($, ye) => u($.name, ye) }, R)), !xe.size),
      /* @__PURE__ */ g.jsxs("div", { className: "cube-shelf-foot", children: [
        /* @__PURE__ */ g.jsx("span", { title: "Queries use all matching cube rows. Ratios and averages are recomputed from their component totals.", children: v.measures.length ? "Auto-update" : "Choose a measure" }),
        /* @__PURE__ */ g.jsx(y6, { spec: U, onEdit: se })
      ] })
    ] }),
    tt("Rows", v.groupBy.map((R) => /* @__PURE__ */ g.jsx(m6, { term: R, filters: ud(v, R.column.name), kind: Le(R.column.name), sort: Oe(R.column.name), onCycleSort: () => se(($) => sd($, R.column.name)), onEdit: se, onProbeValues: ($, ye) => u($.name, ye) }, R.column.name)), !v.groupBy.length),
    tt("Columns", v.pivot && /* @__PURE__ */ g.jsx(x6, { pivot: v.pivot, onEdit: se, onRepivot: (R) => se(($) => ({ ...$, pivot: $.pivot ? { ...$.pivot, grain: R, values: [] } : null })) }), !v.pivot),
    /* @__PURE__ */ g.jsx("div", { className: "cube-result", children: m })
  ] });
}
const E6 = ":root{--workspace-font: var(--sans, var(--data-font, var(--font-superior-sans, system-ui, sans-serif)));--workspace-mono: var(--mono, var(--data-font-mono, var(--font-superior-mono, ui-monospace, monospace)));--workspace-text: var(--bone, var(--foreground, #e8ddcc));--workspace-bright: var(--bone-bright, var(--foreground, #fff7e9));--workspace-muted: var(--fog, var(--chrome-text, #98aab5));--workspace-bg: var(--panel, var(--background, #171310));--workspace-raised: var(--panel-raised, var(--chrome-bg, #1d1813));--workspace-line: var(--line, var(--chrome-border, #344751));--workspace-accent: var(--amber, var(--main, #f5b446));--workspace-line-hot: var(--line-hot, var(--workspace-accent));--workspace-inset: color-mix(in oklch, var(--workspace-bg) 88%, #000);--workspace-wash: color-mix(in oklch, var(--workspace-accent) 7%, transparent);--workspace-radius: 9px;--workspace-control-height: 36px;--workspace-control-font: 500 13px/1.3 var(--workspace-font);--workspace-header-height: 70px;--workspace-gutter: 16px}.workspace-control{min-height:var(--workspace-control-height);padding:7px 10px;border:1px solid var(--workspace-line);border-radius:var(--workspace-radius);background:transparent;color:var(--workspace-muted);font:var(--workspace-control-font);box-shadow:none;cursor:pointer;transition:color .18s,background .18s,border-color .18s}.workspace-control:hover:not(:disabled){border-color:var(--workspace-line-hot);background:var(--workspace-wash);color:var(--workspace-bright)}.workspace-control:focus-visible{outline:2px solid var(--workspace-accent);outline-offset:2px}.workspace-control:disabled{opacity:.4;cursor:default}.workspace-control-primary{color:var(--workspace-accent);border-color:color-mix(in oklch,var(--workspace-accent) 40%,var(--workspace-line));background:var(--workspace-wash)}.workspace-field{width:100%;min-width:0;padding:12px 14px;border:0;border-radius:10px;outline:2px solid transparent;outline-offset:-2px;background:var(--workspace-inset);color:var(--workspace-text);box-shadow:inset 0 2px 5px #0000001f;font:16px/1.5 var(--workspace-font);transition:outline-color .18s}.workspace-field:focus-visible{outline:2px solid color-mix(in oklch,var(--workspace-accent) 55%,var(--workspace-bg));outline-offset:-2px}.workspace-field::placeholder{color:var(--workspace-muted)}", A6 = '@layer properties;@layer theme,base,components,utilities;@layer theme{:root,:host{--font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;--spacing: .25rem;--text-xs: .75rem;--text-xs--line-height: calc(1 / .75);--font-weight-medium: 500;--font-weight-semibold: 600;--tracking-wide: .025em;--tracking-wider: .05em;--radius-sm: .25rem;--radius-md: .375rem;--radius-lg: .5rem;--animate-pulse: pulse 2s cubic-bezier(.4, 0, .6, 1) infinite;--default-transition-duration: .15s;--default-transition-timing-function: cubic-bezier(.4, 0, .2, 1);--default-font-family: var(--data-font,system-ui,sans-serif);--default-mono-font-family: var(--font-mono)}}@layer base{*,:after,:before,::backdrop,::file-selector-button{box-sizing:border-box;margin:0;padding:0;border:0 solid}html,:host{line-height:1.5;-webkit-text-size-adjust:100%;tab-size:4;font-family:var(--default-font-family, ui-sans-serif, system-ui, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji");font-feature-settings:var(--default-font-feature-settings, normal);font-variation-settings:var(--default-font-variation-settings, normal);-webkit-tap-highlight-color:transparent}hr{height:0;color:inherit;border-top-width:1px}abbr:where([title]){-webkit-text-decoration:underline dotted;text-decoration:underline dotted}h1,h2,h3,h4,h5,h6{font-size:inherit;font-weight:inherit}a{color:inherit;-webkit-text-decoration:inherit;text-decoration:inherit}b,strong{font-weight:bolder}code,kbd,samp,pre{font-family:var(--default-mono-font-family, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace);font-feature-settings:var(--default-mono-font-feature-settings, normal);font-variation-settings:var(--default-mono-font-variation-settings, normal);font-size:1em}small{font-size:80%}sub,sup{font-size:75%;line-height:0;position:relative;vertical-align:baseline}sub{bottom:-.25em}sup{top:-.5em}table{text-indent:0;border-color:inherit;border-collapse:collapse}:-moz-focusring{outline:auto}progress{vertical-align:baseline}summary{display:list-item}ol,ul,menu{list-style:none}img,svg,video,canvas,audio,iframe,embed,object{display:block;vertical-align:middle}img,video{max-width:100%;height:auto}button,input,select,optgroup,textarea,::file-selector-button{font:inherit;font-feature-settings:inherit;font-variation-settings:inherit;letter-spacing:inherit;color:inherit;border-radius:0;background-color:transparent;opacity:1}:where(select:is([multiple],[size])) optgroup{font-weight:bolder}:where(select:is([multiple],[size])) optgroup option{padding-inline-start:20px}::file-selector-button{margin-inline-end:4px}::placeholder{opacity:1}@supports (not (-webkit-appearance: -apple-pay-button)) or (contain-intrinsic-size: 1px){::placeholder{color:currentcolor}@supports (color: color-mix(in lab,red,red)){::placeholder{color:color-mix(in oklab,currentcolor 50%,transparent)}}}textarea{resize:vertical}::-webkit-search-decoration{-webkit-appearance:none}::-webkit-date-and-time-value{min-height:1lh;text-align:inherit}::-webkit-datetime-edit{display:inline-flex}::-webkit-datetime-edit-fields-wrapper{padding:0}::-webkit-datetime-edit,::-webkit-datetime-edit-year-field,::-webkit-datetime-edit-month-field,::-webkit-datetime-edit-day-field,::-webkit-datetime-edit-hour-field,::-webkit-datetime-edit-minute-field,::-webkit-datetime-edit-second-field,::-webkit-datetime-edit-millisecond-field,::-webkit-datetime-edit-meridiem-field{padding-block:0}::-webkit-calendar-picker-indicator{line-height:1}:-moz-ui-invalid{box-shadow:none}button,input:where([type=button],[type=reset],[type=submit]),::file-selector-button{appearance:button}::-webkit-inner-spin-button,::-webkit-outer-spin-button{height:auto}[hidden]:where(:not([hidden=until-found])){display:none!important}}@layer utilities{.pointer-events-none{pointer-events:none}.visible{visibility:visible}.absolute{position:absolute}.fixed{position:fixed}.relative{position:relative}.sticky{position:sticky}.inset-0{inset:calc(var(--spacing) * 0)}.top-0{top:calc(var(--spacing) * 0)}.top-1\\/2{top:50%}.top-full{top:100%}.top-px{top:1px}.right-0{right:calc(var(--spacing) * 0)}.right-0\\.5{right:calc(var(--spacing) * .5)}.bottom-0{bottom:calc(var(--spacing) * 0)}.left-0{left:calc(var(--spacing) * 0)}.left-auto{left:auto}.z-10{z-index:10}.z-20{z-index:20}.z-30{z-index:30}.z-50{z-index:50}.z-\\[9999\\]{z-index:9999}.z-\\[10000\\]{z-index:10000}.mx-0\\.5{margin-inline:calc(var(--spacing) * .5)}.my-1{margin-block:calc(var(--spacing) * 1)}.mt-1{margin-top:calc(var(--spacing) * 1)}.mt-auto{margin-top:auto}.mr-1{margin-right:calc(var(--spacing) * 1)}.ml-0\\.5{margin-left:calc(var(--spacing) * .5)}.ml-1{margin-left:calc(var(--spacing) * 1)}.ml-2{margin-left:calc(var(--spacing) * 2)}.ml-auto{margin-left:auto}.block{display:block}.contents{display:contents}.flex{display:flex}.grid{display:grid}.inline{display:inline}.inline-flex{display:inline-flex}.inline-grid{display:inline-grid}.table{display:table}.h-1\\.5{height:calc(var(--spacing) * 1.5)}.h-2{height:calc(var(--spacing) * 2)}.h-2\\.5{height:calc(var(--spacing) * 2.5)}.h-3{height:calc(var(--spacing) * 3)}.h-3\\.5{height:calc(var(--spacing) * 3.5)}.h-5{height:calc(var(--spacing) * 5)}.h-\\[3px\\]{height:3px}.h-full{height:100%}.h-px{height:1px}.max-h-40{max-height:calc(var(--spacing) * 40)}.max-h-52{max-height:calc(var(--spacing) * 52)}.max-h-\\[38px\\]{max-height:38px}.max-h-\\[360px\\]{max-height:360px}.w-1\\.5{width:calc(var(--spacing) * 1.5)}.w-2{width:calc(var(--spacing) * 2)}.w-2\\.5{width:calc(var(--spacing) * 2.5)}.w-3{width:calc(var(--spacing) * 3)}.w-3\\.5{width:calc(var(--spacing) * 3.5)}.w-5{width:calc(var(--spacing) * 5)}.w-6{width:calc(var(--spacing) * 6)}.w-16{width:calc(var(--spacing) * 16)}.w-full{width:100%}.w-px{width:1px}.max-w-\\[220px\\]{max-width:220px}.max-w-\\[240px\\]{max-width:240px}.max-w-\\[440px\\]{max-width:440px}.max-w-full{max-width:100%}.min-w-0{min-width:calc(var(--spacing) * 0)}.min-w-\\[96px\\]{min-width:96px}.min-w-\\[180px\\]{min-width:180px}.min-w-\\[200px\\]{min-width:200px}.min-w-\\[210px\\]{min-width:210px}.flex-1{flex:1}.shrink-0{flex-shrink:0}.grow{flex-grow:1}.origin-top-left{transform-origin:0 0}.translate-x-\\[calc\\(0\\.75rem-3px\\)\\]{--tw-translate-x: calc(.75rem - 3px) ;translate:var(--tw-translate-x) var(--tw-translate-y)}.translate-x-px{--tw-translate-x: 1px;translate:var(--tw-translate-x) var(--tw-translate-y)}.-translate-y-1\\/2{--tw-translate-y: -50% ;translate:var(--tw-translate-x) var(--tw-translate-y)}.rotate-180{rotate:180deg}.transform{transform:var(--tw-rotate-x,) var(--tw-rotate-y,) var(--tw-rotate-z,) var(--tw-skew-x,) var(--tw-skew-y,)}.animate-pulse{animation:var(--animate-pulse)}.cursor-col-resize{cursor:col-resize}.cursor-grab{cursor:grab}.cursor-pointer{cursor:pointer}.resize{resize:both}.grid-cols-\\[max-content_1fr\\]{grid-template-columns:max-content 1fr}.flex-col{flex-direction:column}.flex-wrap{flex-wrap:wrap}.place-items-center{place-items:center}.items-baseline{align-items:baseline}.items-center{align-items:center}.justify-between{justify-content:space-between}.justify-center{justify-content:center}.gap-0\\.5{gap:calc(var(--spacing) * .5)}.gap-1{gap:calc(var(--spacing) * 1)}.gap-1\\.5{gap:calc(var(--spacing) * 1.5)}.gap-2{gap:calc(var(--spacing) * 2)}.gap-3{gap:calc(var(--spacing) * 3)}.gap-px{gap:1px}:where(.space-y-1\\.5>:not(:last-child)){--tw-space-y-reverse: 0;margin-block-start:calc(calc(var(--spacing) * 1.5) * var(--tw-space-y-reverse));margin-block-end:calc(calc(var(--spacing) * 1.5) * calc(1 - var(--tw-space-y-reverse)))}.gap-x-3{column-gap:calc(var(--spacing) * 3)}.gap-y-0\\.5{row-gap:calc(var(--spacing) * .5)}.truncate{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.overflow-auto{overflow:auto}.overflow-hidden{overflow:hidden}.rounded{border-radius:.25rem}.rounded-\\[1px\\]{border-radius:1px}.rounded-full{border-radius:calc(infinity * 1px)}.rounded-lg{border-radius:var(--radius-lg)}.rounded-md{border-radius:var(--radius-md)}.rounded-sm{border-radius:var(--radius-sm)}.border{border-style:var(--tw-border-style);border-width:1px}.border-2{border-style:var(--tw-border-style);border-width:2px}.border-t{border-top-style:var(--tw-border-style);border-top-width:1px}.border-r{border-right-style:var(--tw-border-style);border-right-width:1px}.border-b{border-bottom-style:var(--tw-border-style);border-bottom-width:1px}.border-dashed{--tw-border-style: dashed;border-style:dashed}.border-chrome-border{border-color:var(--chrome-border)}.border-chrome-border\\/30{border-color:var(--chrome-border)}@supports (color: color-mix(in lab,red,red)){.border-chrome-border\\/30{border-color:color-mix(in oklab,var(--chrome-border) 30%,transparent)}}.border-chrome-border\\/40{border-color:var(--chrome-border)}@supports (color: color-mix(in lab,red,red)){.border-chrome-border\\/40{border-color:color-mix(in oklab,var(--chrome-border) 40%,transparent)}}.border-chrome-border\\/50{border-color:var(--chrome-border)}@supports (color: color-mix(in lab,red,red)){.border-chrome-border\\/50{border-color:color-mix(in oklab,var(--chrome-border) 50%,transparent)}}.border-chrome-border\\/60{border-color:var(--chrome-border)}@supports (color: color-mix(in lab,red,red)){.border-chrome-border\\/60{border-color:color-mix(in oklab,var(--chrome-border) 60%,transparent)}}.border-chrome-border\\/70{border-color:var(--chrome-border)}@supports (color: color-mix(in lab,red,red)){.border-chrome-border\\/70{border-color:color-mix(in oklab,var(--chrome-border) 70%,transparent)}}.border-main{border-color:var(--main)}.border-main\\/50{border-color:var(--main)}@supports (color: color-mix(in lab,red,red)){.border-main\\/50{border-color:color-mix(in oklab,var(--main) 50%,transparent)}}.border-main\\/60{border-color:var(--main)}@supports (color: color-mix(in lab,red,red)){.border-main\\/60{border-color:color-mix(in oklab,var(--main) 60%,transparent)}}.border-rvbbit-accent\\/40{border-color:var(--main)}@supports (color: color-mix(in lab,red,red)){.border-rvbbit-accent\\/40{border-color:color-mix(in oklab,var(--main) 40%,transparent)}}.bg-chart-3\\/20{background-color:var(--main)}@supports (color: color-mix(in lab,red,red)){.bg-chart-3\\/20{background-color:color-mix(in oklab,var(--main) 20%,transparent)}}.bg-chrome-bg\\/40{background-color:var(--chrome-bg)}@supports (color: color-mix(in lab,red,red)){.bg-chrome-bg\\/40{background-color:color-mix(in oklab,var(--chrome-bg) 40%,transparent)}}.bg-chrome-bg\\/60{background-color:var(--chrome-bg)}@supports (color: color-mix(in lab,red,red)){.bg-chrome-bg\\/60{background-color:color-mix(in oklab,var(--chrome-bg) 60%,transparent)}}.bg-chrome-bg\\/90{background-color:var(--chrome-bg)}@supports (color: color-mix(in lab,red,red)){.bg-chrome-bg\\/90{background-color:color-mix(in oklab,var(--chrome-bg) 90%,transparent)}}.bg-chrome-bg\\/95{background-color:var(--chrome-bg)}@supports (color: color-mix(in lab,red,red)){.bg-chrome-bg\\/95{background-color:color-mix(in oklab,var(--chrome-bg) 95%,transparent)}}.bg-chrome-border\\/50{background-color:var(--chrome-border)}@supports (color: color-mix(in lab,red,red)){.bg-chrome-border\\/50{background-color:color-mix(in oklab,var(--chrome-border) 50%,transparent)}}.bg-chrome-border\\/60{background-color:var(--chrome-border)}@supports (color: color-mix(in lab,red,red)){.bg-chrome-border\\/60{background-color:color-mix(in oklab,var(--chrome-border) 60%,transparent)}}.bg-chrome-text\\/10{background-color:var(--chrome-text)}@supports (color: color-mix(in lab,red,red)){.bg-chrome-text\\/10{background-color:color-mix(in oklab,var(--chrome-text) 10%,transparent)}}.bg-chrome-text\\/55{background-color:var(--chrome-text)}@supports (color: color-mix(in lab,red,red)){.bg-chrome-text\\/55{background-color:color-mix(in oklab,var(--chrome-text) 55%,transparent)}}.bg-danger\\/20{background-color:color-mix(in oklab,#ef7979 20%,transparent)}.bg-doc-bg{background-color:var(--doc-bg)}.bg-foreground\\/10{background-color:var(--foreground)}@supports (color: color-mix(in lab,red,red)){.bg-foreground\\/10{background-color:color-mix(in oklab,var(--foreground) 10%,transparent)}}.bg-foreground\\/\\[0\\.07\\]{background-color:var(--foreground)}@supports (color: color-mix(in lab,red,red)){.bg-foreground\\/\\[0\\.07\\]{background-color:color-mix(in oklab,var(--foreground) 7.000000000000001%,transparent)}}.bg-foreground\\/\\[0\\.025\\]{background-color:var(--foreground)}@supports (color: color-mix(in lab,red,red)){.bg-foreground\\/\\[0\\.025\\]{background-color:color-mix(in oklab,var(--foreground) 2.5%,transparent)}}.bg-main{background-color:var(--main)}.bg-main\\/10{background-color:var(--main)}@supports (color: color-mix(in lab,red,red)){.bg-main\\/10{background-color:color-mix(in oklab,var(--main) 10%,transparent)}}.bg-main\\/15{background-color:var(--main)}@supports (color: color-mix(in lab,red,red)){.bg-main\\/15{background-color:color-mix(in oklab,var(--main) 15%,transparent)}}.bg-main\\/20{background-color:var(--main)}@supports (color: color-mix(in lab,red,red)){.bg-main\\/20{background-color:color-mix(in oklab,var(--main) 20%,transparent)}}.bg-main\\/25{background-color:var(--main)}@supports (color: color-mix(in lab,red,red)){.bg-main\\/25{background-color:color-mix(in oklab,var(--main) 25%,transparent)}}.bg-main\\/30{background-color:var(--main)}@supports (color: color-mix(in lab,red,red)){.bg-main\\/30{background-color:color-mix(in oklab,var(--main) 30%,transparent)}}.bg-rvbbit-accent\\/20{background-color:var(--main)}@supports (color: color-mix(in lab,red,red)){.bg-rvbbit-accent\\/20{background-color:color-mix(in oklab,var(--main) 20%,transparent)}}.bg-transparent{background-color:transparent}.object-contain{object-fit:contain}.p-1{padding:calc(var(--spacing) * 1)}.p-2{padding:calc(var(--spacing) * 2)}.p-6{padding:calc(var(--spacing) * 6)}.px-0\\.5{padding-inline:calc(var(--spacing) * .5)}.px-1{padding-inline:calc(var(--spacing) * 1)}.px-1\\.5{padding-inline:calc(var(--spacing) * 1.5)}.px-2{padding-inline:calc(var(--spacing) * 2)}.px-3{padding-inline:calc(var(--spacing) * 3)}.py-0{padding-block:calc(var(--spacing) * 0)}.py-0\\.5{padding-block:calc(var(--spacing) * .5)}.py-1{padding-block:calc(var(--spacing) * 1)}.py-1\\.5{padding-block:calc(var(--spacing) * 1.5)}.py-2{padding-block:calc(var(--spacing) * 2)}.pt-0\\.5{padding-top:calc(var(--spacing) * .5)}.pb-1\\.5{padding-bottom:calc(var(--spacing) * 1.5)}.text-left{text-align:left}.text-right{text-align:right}.font-mono{font-family:var(--font-mono)}.text-xs{font-size:var(--text-xs);line-height:var(--tw-leading, var(--text-xs--line-height))}.text-\\[8px\\]{font-size:8px}.text-\\[9px\\]{font-size:9px}.text-\\[10px\\]{font-size:10px}.text-\\[11px\\]{font-size:11px}.text-\\[12px\\]{font-size:12px}.leading-4{--tw-leading: calc(var(--spacing) * 4);line-height:calc(var(--spacing) * 4)}.leading-none{--tw-leading: 1;line-height:1}.font-medium{--tw-font-weight: var(--font-weight-medium);font-weight:var(--font-weight-medium)}.font-semibold{--tw-font-weight: var(--font-weight-semibold);font-weight:var(--font-weight-semibold)}.tracking-wide{--tw-tracking: var(--tracking-wide);letter-spacing:var(--tracking-wide)}.tracking-wider{--tw-tracking: var(--tracking-wider);letter-spacing:var(--tracking-wider)}.break-words{overflow-wrap:break-word}.whitespace-nowrap{white-space:nowrap}.text-chart-3{color:var(--main)}.text-chart-3\\/80{color:var(--main)}@supports (color: color-mix(in lab,red,red)){.text-chart-3\\/80{color:color-mix(in oklab,var(--main) 80%,transparent)}}.text-chrome-text{color:var(--chrome-text)}.text-chrome-text\\/40{color:var(--chrome-text)}@supports (color: color-mix(in lab,red,red)){.text-chrome-text\\/40{color:color-mix(in oklab,var(--chrome-text) 40%,transparent)}}.text-chrome-text\\/45{color:var(--chrome-text)}@supports (color: color-mix(in lab,red,red)){.text-chrome-text\\/45{color:color-mix(in oklab,var(--chrome-text) 45%,transparent)}}.text-chrome-text\\/50{color:var(--chrome-text)}@supports (color: color-mix(in lab,red,red)){.text-chrome-text\\/50{color:color-mix(in oklab,var(--chrome-text) 50%,transparent)}}.text-chrome-text\\/55{color:var(--chrome-text)}@supports (color: color-mix(in lab,red,red)){.text-chrome-text\\/55{color:color-mix(in oklab,var(--chrome-text) 55%,transparent)}}.text-chrome-text\\/60{color:var(--chrome-text)}@supports (color: color-mix(in lab,red,red)){.text-chrome-text\\/60{color:color-mix(in oklab,var(--chrome-text) 60%,transparent)}}.text-chrome-text\\/70{color:var(--chrome-text)}@supports (color: color-mix(in lab,red,red)){.text-chrome-text\\/70{color:color-mix(in oklab,var(--chrome-text) 70%,transparent)}}.text-chrome-text\\/85{color:var(--chrome-text)}@supports (color: color-mix(in lab,red,red)){.text-chrome-text\\/85{color:color-mix(in oklab,var(--chrome-text) 85%,transparent)}}.text-danger{color:#ef7979}.text-foreground{color:var(--foreground)}.text-foreground\\/85{color:var(--foreground)}@supports (color: color-mix(in lab,red,red)){.text-foreground\\/85{color:color-mix(in oklab,var(--foreground) 85%,transparent)}}.text-main{color:var(--main)}.text-main\\/70{color:var(--main)}@supports (color: color-mix(in lab,red,red)){.text-main\\/70{color:color-mix(in oklab,var(--main) 70%,transparent)}}.text-main\\/75{color:var(--main)}@supports (color: color-mix(in lab,red,red)){.text-main\\/75{color:color-mix(in oklab,var(--main) 75%,transparent)}}.text-rvbbit-accent{color:var(--main)}.capitalize{text-transform:capitalize}.uppercase{text-transform:uppercase}.italic{font-style:italic}.tabular-nums{--tw-numeric-spacing: tabular-nums;font-variant-numeric:var(--tw-ordinal,) var(--tw-slashed-zero,) var(--tw-numeric-figure,) var(--tw-numeric-spacing,) var(--tw-numeric-fraction,)}.opacity-0{opacity:0%}.shadow-2xl{--tw-shadow: 0 25px 50px -12px var(--tw-shadow-color, rgb(0 0 0 / .25));box-shadow:var(--tw-inset-shadow),var(--tw-inset-ring-shadow),var(--tw-ring-offset-shadow),var(--tw-ring-shadow),var(--tw-shadow)}.shadow-lg{--tw-shadow: 0 10px 15px -3px var(--tw-shadow-color, rgb(0 0 0 / .1)), 0 4px 6px -4px var(--tw-shadow-color, rgb(0 0 0 / .1));box-shadow:var(--tw-inset-shadow),var(--tw-inset-ring-shadow),var(--tw-ring-offset-shadow),var(--tw-ring-shadow),var(--tw-shadow)}.ring-1{--tw-ring-shadow: var(--tw-ring-inset,) 0 0 0 calc(1px + var(--tw-ring-offset-width)) var(--tw-ring-color, currentcolor);box-shadow:var(--tw-inset-shadow),var(--tw-inset-ring-shadow),var(--tw-ring-offset-shadow),var(--tw-ring-shadow),var(--tw-shadow)}.ring-main\\/50{--tw-ring-color: var(--main)}@supports (color: color-mix(in lab,red,red)){.ring-main\\/50{--tw-ring-color: color-mix(in oklab, var(--main) 50%, transparent)}}.ring-main\\/55{--tw-ring-color: var(--main)}@supports (color: color-mix(in lab,red,red)){.ring-main\\/55{--tw-ring-color: color-mix(in oklab, var(--main) 55%, transparent)}}.ring-main\\/60,.ring-rvbbit-accent\\/60{--tw-ring-color: var(--main)}@supports (color: color-mix(in lab,red,red)){.ring-main\\/60,.ring-rvbbit-accent\\/60{--tw-ring-color: color-mix(in oklab, var(--main) 60%, transparent)}}.blur{--tw-blur: blur(8px);filter:var(--tw-blur,) var(--tw-brightness,) var(--tw-contrast,) var(--tw-grayscale,) var(--tw-hue-rotate,) var(--tw-invert,) var(--tw-saturate,) var(--tw-sepia,) var(--tw-drop-shadow,)}.filter{filter:var(--tw-blur,) var(--tw-brightness,) var(--tw-contrast,) var(--tw-grayscale,) var(--tw-hue-rotate,) var(--tw-invert,) var(--tw-saturate,) var(--tw-sepia,) var(--tw-drop-shadow,)}.backdrop-blur{--tw-backdrop-blur: blur(8px);-webkit-backdrop-filter:var(--tw-backdrop-blur,) var(--tw-backdrop-brightness,) var(--tw-backdrop-contrast,) var(--tw-backdrop-grayscale,) var(--tw-backdrop-hue-rotate,) var(--tw-backdrop-invert,) var(--tw-backdrop-opacity,) var(--tw-backdrop-saturate,) var(--tw-backdrop-sepia,);backdrop-filter:var(--tw-backdrop-blur,) var(--tw-backdrop-brightness,) var(--tw-backdrop-contrast,) var(--tw-backdrop-grayscale,) var(--tw-backdrop-hue-rotate,) var(--tw-backdrop-invert,) var(--tw-backdrop-opacity,) var(--tw-backdrop-saturate,) var(--tw-backdrop-sepia,)}.backdrop-blur-\\[6px\\]{--tw-backdrop-blur: blur(6px);-webkit-backdrop-filter:var(--tw-backdrop-blur,) var(--tw-backdrop-brightness,) var(--tw-backdrop-contrast,) var(--tw-backdrop-grayscale,) var(--tw-backdrop-hue-rotate,) var(--tw-backdrop-invert,) var(--tw-backdrop-opacity,) var(--tw-backdrop-saturate,) var(--tw-backdrop-sepia,);backdrop-filter:var(--tw-backdrop-blur,) var(--tw-backdrop-brightness,) var(--tw-backdrop-contrast,) var(--tw-backdrop-grayscale,) var(--tw-backdrop-hue-rotate,) var(--tw-backdrop-invert,) var(--tw-backdrop-opacity,) var(--tw-backdrop-saturate,) var(--tw-backdrop-sepia,)}.transition-colors{transition-property:color,background-color,border-color,outline-color,text-decoration-color,fill,stroke,--tw-gradient-from,--tw-gradient-via,--tw-gradient-to;transition-timing-function:var(--tw-ease, var(--default-transition-timing-function));transition-duration:var(--tw-duration, var(--default-transition-duration))}.transition-opacity{transition-property:opacity;transition-timing-function:var(--tw-ease, var(--default-transition-timing-function));transition-duration:var(--tw-duration, var(--default-transition-duration))}.transition-transform{transition-property:transform,translate,scale,rotate;transition-timing-function:var(--tw-ease, var(--default-transition-timing-function));transition-duration:var(--tw-duration, var(--default-transition-duration))}.duration-100{--tw-duration: .1s;transition-duration:.1s}.outline-none{--tw-outline-style: none;outline-style:none}.select-none{-webkit-user-select:none;user-select:none}.ring-inset{--tw-ring-inset: inset}@media(hover:hover){.group-hover\\:opacity-100:is(:where(.group):hover *){opacity:100%}}@media(hover:hover){.group-hover\\/cell\\:opacity-100:is(:where(.group\\/cell):hover *){opacity:100%}}.group-data-\\[focused\\=false\\]\\/window\\:bg-doc-bg\\/70:is(:where(.group\\/window)[data-focused=false] *){background-color:var(--doc-bg)}@supports (color: color-mix(in lab,red,red)){.group-data-\\[focused\\=false\\]\\/window\\:bg-doc-bg\\/70:is(:where(.group\\/window)[data-focused=false] *){background-color:color-mix(in oklab,var(--doc-bg) 70%,transparent)}}.group-data-\\[focused\\=false\\]\\/window\\:bg-doc-bg\\/90:is(:where(.group\\/window)[data-focused=false] *){background-color:var(--doc-bg)}@supports (color: color-mix(in lab,red,red)){.group-data-\\[focused\\=false\\]\\/window\\:bg-doc-bg\\/90:is(:where(.group\\/window)[data-focused=false] *){background-color:color-mix(in oklab,var(--doc-bg) 90%,transparent)}}.placeholder\\:text-chrome-text\\/35::placeholder{color:var(--chrome-text)}@supports (color: color-mix(in lab,red,red)){.placeholder\\:text-chrome-text\\/35::placeholder{color:color-mix(in oklab,var(--chrome-text) 35%,transparent)}}.placeholder\\:text-chrome-text\\/40::placeholder{color:var(--chrome-text)}@supports (color: color-mix(in lab,red,red)){.placeholder\\:text-chrome-text\\/40::placeholder{color:color-mix(in oklab,var(--chrome-text) 40%,transparent)}}@media(hover:hover){.hover\\:border-main\\/40:hover{border-color:var(--main)}@supports (color: color-mix(in lab,red,red)){.hover\\:border-main\\/40:hover{border-color:color-mix(in oklab,var(--main) 40%,transparent)}}}@media(hover:hover){.hover\\:border-main\\/50:hover{border-color:var(--main)}@supports (color: color-mix(in lab,red,red)){.hover\\:border-main\\/50:hover{border-color:color-mix(in oklab,var(--main) 50%,transparent)}}}@media(hover:hover){.hover\\:bg-danger\\/10:hover{background-color:color-mix(in oklab,#ef7979 10%,transparent)}}@media(hover:hover){.hover\\:bg-danger\\/15:hover{background-color:color-mix(in oklab,#ef7979 15%,transparent)}}@media(hover:hover){.hover\\:bg-danger\\/20:hover{background-color:color-mix(in oklab,#ef7979 20%,transparent)}}@media(hover:hover){.hover\\:bg-foreground\\/10:hover{background-color:var(--foreground)}@supports (color: color-mix(in lab,red,red)){.hover\\:bg-foreground\\/10:hover{background-color:color-mix(in oklab,var(--foreground) 10%,transparent)}}}@media(hover:hover){.hover\\:bg-foreground\\/\\[0\\.05\\]:hover{background-color:var(--foreground)}@supports (color: color-mix(in lab,red,red)){.hover\\:bg-foreground\\/\\[0\\.05\\]:hover{background-color:color-mix(in oklab,var(--foreground) 5%,transparent)}}}@media(hover:hover){.hover\\:bg-foreground\\/\\[0\\.06\\]:hover{background-color:var(--foreground)}@supports (color: color-mix(in lab,red,red)){.hover\\:bg-foreground\\/\\[0\\.06\\]:hover{background-color:color-mix(in oklab,var(--foreground) 6%,transparent)}}}@media(hover:hover){.hover\\:bg-foreground\\/\\[0\\.14\\]:hover{background-color:var(--foreground)}@supports (color: color-mix(in lab,red,red)){.hover\\:bg-foreground\\/\\[0\\.14\\]:hover{background-color:color-mix(in oklab,var(--foreground) 14.000000000000002%,transparent)}}}@media(hover:hover){.hover\\:bg-main\\/10:hover{background-color:var(--main)}@supports (color: color-mix(in lab,red,red)){.hover\\:bg-main\\/10:hover{background-color:color-mix(in oklab,var(--main) 10%,transparent)}}}@media(hover:hover){.hover\\:bg-main\\/25:hover{background-color:var(--main)}@supports (color: color-mix(in lab,red,red)){.hover\\:bg-main\\/25:hover{background-color:color-mix(in oklab,var(--main) 25%,transparent)}}}@media(hover:hover){.hover\\:bg-main\\/30:hover{background-color:var(--main)}@supports (color: color-mix(in lab,red,red)){.hover\\:bg-main\\/30:hover{background-color:color-mix(in oklab,var(--main) 30%,transparent)}}}@media(hover:hover){.hover\\:bg-main\\/50:hover{background-color:var(--main)}@supports (color: color-mix(in lab,red,red)){.hover\\:bg-main\\/50:hover{background-color:color-mix(in oklab,var(--main) 50%,transparent)}}}@media(hover:hover){.hover\\:bg-rvbbit-accent\\/15:hover{background-color:var(--main)}@supports (color: color-mix(in lab,red,red)){.hover\\:bg-rvbbit-accent\\/15:hover{background-color:color-mix(in oklab,var(--main) 15%,transparent)}}}@media(hover:hover){.hover\\:text-danger:hover{color:#ef7979}}@media(hover:hover){.hover\\:text-foreground:hover{color:var(--foreground)}}@media(hover:hover){.hover\\:ring-1:hover{--tw-ring-shadow: var(--tw-ring-inset,) 0 0 0 calc(1px + var(--tw-ring-offset-width)) var(--tw-ring-color, currentcolor);box-shadow:var(--tw-inset-shadow),var(--tw-inset-ring-shadow),var(--tw-ring-offset-shadow),var(--tw-ring-shadow),var(--tw-shadow)}}@media(hover:hover){.hover\\:ring-main\\/35:hover{--tw-ring-color: var(--main)}@supports (color: color-mix(in lab,red,red)){.hover\\:ring-main\\/35:hover{--tw-ring-color: color-mix(in oklab, var(--main) 35%, transparent)}}}@media(hover:hover){.hover\\:ring-inset:hover{--tw-ring-inset: inset}}.active\\:cursor-grabbing:active{cursor:grabbing}.disabled\\:opacity-40:disabled{opacity:40%}}:root{color-scheme:dark;--background: #08151c;--foreground: #d4e6ee;--main: #8bbbd0;--chrome-text: #98aab5;--chrome-border: #344751;--chrome-bg: #11252f;--doc-bg: #0b1b23;--font-mono: var(--workspace-mono);--danger: #ef7979;--success: #69bca6;--viz-kind-1: #749ab9;--viz-kind-2: #a39b73;--viz-kind-3: #8ca89c;--viz-kind-4: #ac90a9;--viz-kind-5: #b2937b;--viz-kind-6: #749ab9;--viz-kind-7: #a39b73;--viz-kind-8: #8ca89c;--viz-kind-9: #ac90a9;--viz-kind-10: #b2937b}:root[data-theme=light]{color-scheme:light}:root{--font-family-mono: var(--workspace-mono);--font-family-sans: var(--workspace-font);--syntax-keyword: var(--main);--syntax-function: var(--main);--syntax-identifier: var(--foreground);--syntax-string: var(--success);--syntax-number: var(--danger);--syntax-operator: var(--main);--syntax-comment: var(--chrome-text);--warning: var(--danger);--viz-op-pipeline: var(--syntax-keyword);--viz-op-sql: var(--success);--viz-op-specialist: var(--viz-kind-4);--rvbbit-accent: var(--main)}@supports (color: color-mix(in lab,red,red)){:root{--syntax-keyword: color-mix(in oklch,var(--main) 35%,var(--foreground))}}@supports (color: color-mix(in lab,red,red)){:root{--syntax-function: color-mix(in oklch,var(--main) 25%,var(--foreground))}}@supports (color: color-mix(in lab,red,red)){:root{--syntax-string: color-mix(in oklch,var(--success) 65%,var(--foreground))}}@supports (color: color-mix(in lab,red,red)){:root{--syntax-number: color-mix(in oklch,var(--danger) 60%,var(--foreground))}}@supports (color: color-mix(in lab,red,red)){:root{--syntax-operator: color-mix(in oklch,var(--main) 35%,var(--foreground))}}html,body,#data-root{margin:0;width:100%;height:100%;overflow:hidden;background:transparent;color:var(--workspace-text);font-family:var(--workspace-font);font-size:14px;-webkit-font-smoothing:antialiased}*{scrollbar-width:thin;scrollbar-color:var(--chrome-border) var(--background)}*{box-sizing:border-box}button,input,textarea,select{font:inherit}button,summary{cursor:pointer}button:disabled{cursor:default;opacity:.5}button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible,summary:focus-visible{outline:2px solid var(--main);outline-offset:2px}.data-app{display:flex;flex-direction:column;height:100%;min-width:0}.data-body{flex:1;min-height:0;overflow:auto;padding:12px var(--workspace-gutter) 24px}.actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:10px}.data-body label{display:block;font:500 12px/1.5 var(--workspace-font);color:var(--workspace-muted)}.data-body .workspace-field{margin-top:8px}.data-body textarea{resize:vertical;min-height:108px}.data-body select.workspace-field{font-size:13px;min-height:36px;padding:8px 10px;box-shadow:none;border:1px solid var(--workspace-line);background:transparent}.data-body select option{background:var(--workspace-bg);color:var(--workspace-text)}.area-label{margin-top:18px}.muted,small,.search-status{color:var(--workspace-muted);font-size:11px}.muted{line-height:1.5}.search-status{margin:22px 0 10px;font:11px/1.5 var(--workspace-mono)}.field-list{display:grid;grid-template-columns:minmax(80px,1fr) 2fr;gap:6px;font-size:12px;margin:12px 0}.field-list dt{overflow-wrap:anywhere}.field-list dd{margin:0;color:var(--workspace-muted)}.assumption{font-size:12px;color:var(--workspace-muted);border-left:1px solid var(--workspace-line);padding-left:12px}.data-result{height:clamp(300px,54vh,700px);min-height:280px;display:flex;flex-direction:column;border:1px solid var(--workspace-line);border-radius:var(--workspace-radius);overflow:hidden;margin-top:20px}.data-result>.flex{min-height:0;flex:1}.result-caption{display:flex;gap:8px;justify-content:space-between;padding:10px 12px;font:11px/1.5 var(--workspace-mono);border-bottom:1px solid var(--workspace-line);background:transparent;flex:none}.error{display:flex;gap:8px;align-items:center;justify-content:space-between;padding:10px 12px;border-left:2px solid var(--danger);margin-bottom:14px;line-height:1.5}.notice{color:var(--main)}.record-panel header{display:flex;justify-content:space-between;align-items:center;gap:8px}.record-backdrop{position:fixed;inset:0;background:#0008;z-index:100;display:flex;padding:20px}.record-panel{background:var(--workspace-bg);border:1px solid var(--workspace-line);border-radius:12px;padding:18px;overflow:auto;width:100%;height:100%}.record-panel dt{color:var(--main);margin-top:12px}.record-panel dd{margin:4px 0 0}.record-panel pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.record-panel h2{font-size:16px}.data-tile-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,154px),1fr));gap:8px;align-items:stretch;container:data-tiles / inline-size}.data-tile{display:flex;flex-direction:column;gap:7px;min-width:0;width:100%;padding:12px;text-align:left;border:1px solid var(--chrome-border);border-radius:var(--workspace-radius);background:var(--workspace-raised);transition:background .18s,border-color .18s}@supports (color: color-mix(in lab,red,red)){.data-tile{background:color-mix(in oklch,var(--workspace-raised) 22%,transparent)}}.data-tile:hover{border-color:var(--workspace-line-hot);background:var(--workspace-wash)}.data-tile strong{display:flex;align-items:flex-start;justify-content:space-between;gap:6px;min-width:0;font-size:14px;font-weight:500;line-height:1.4;color:var(--workspace-bright)}.data-tile-title{min-width:0;min-height:2.8em;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}.data-tile-open{flex:none;color:var(--main)}.data-tile p{margin:0;color:var(--chrome-text);font-size:12px;line-height:1.5;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}.data-tile-meta{display:flex;flex-direction:column;gap:3px;min-width:0;margin-top:auto;padding-top:3px}.data-tile-meta small{display:block;font-size:11px;line-height:1.4}.data-tile-source{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.dataset-matches{display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere;font-size:10px;color:var(--main)}.area-card-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,260px),1fr));gap:14px;align-items:stretch;padding:0 3px 4px 0}.area-card{position:relative;display:flex;flex-direction:column;gap:10px;min-width:0;padding:17px;text-align:left;color:var(--workspace-text);border:1px solid var(--workspace-line-hot);border-radius:var(--workspace-radius);background:linear-gradient(135deg,var(--main),transparent 65%),var(--workspace-raised);box-shadow:3px 4px 0 -1px var(--workspace-bg),3px 4px 0 0 var(--workspace-line);transition:background .18s,border-color .18s}@supports (color: color-mix(in lab,red,red)){.area-card{background:linear-gradient(135deg,color-mix(in oklch,var(--main) 8%,transparent),transparent 65%),color-mix(in oklch,var(--workspace-raised) 32%,transparent)}}.area-card:hover{border-color:var(--main);background:linear-gradient(135deg,var(--main),transparent 75%),var(--workspace-wash)}@supports (color: color-mix(in lab,red,red)){.area-card:hover{background:linear-gradient(135deg,color-mix(in oklch,var(--main) 12%,transparent),transparent 75%),var(--workspace-wash)}}.area-card-eyebrow{display:flex;align-items:center;gap:7px;color:var(--main);font:10px/1.4 var(--workspace-mono);text-transform:uppercase;letter-spacing:.08em}.area-card-arrow{margin-left:auto;flex:none}.area-card-title{color:var(--workspace-bright);font:500 19px/1.3 var(--workspace-font);overflow-wrap:anywhere}.area-card-scope{display:flex;align-items:center;flex-wrap:wrap;gap:6px;font:11px/1.5 var(--workspace-mono);color:var(--main)}.area-card-about{margin:0;font-size:12px;line-height:1.6;color:var(--workspace-muted);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}.area-card-topics{display:flex;gap:5px;flex-wrap:wrap;align-items:flex-start;min-width:0}.area-card-topics>span{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font:10px/1.5 var(--workspace-font);color:var(--workspace-muted);background:var(--workspace-wash);border-radius:4px;padding:3px 6px}.area-card-question{display:flex;gap:8px;padding-top:12px;margin-top:auto;border-top:1px solid var(--workspace-line);font:12px/1.5 var(--workspace-font);min-width:0}.area-card-question>svg{color:var(--main);flex:none;margin-top:2px}.area-card-question>span{min-width:0}.area-card-question small{display:block;font-size:10px;margin-bottom:4px}.area-card-question>span>span{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}.area-card-foot{display:flex;flex-wrap:wrap;gap:5px 12px;color:var(--workspace-muted);font:10px/1.5 var(--workspace-mono)}@container data-tiles (max-width:315px){.data-tile-title{min-height:0}}.data-viewer .data-result{height:100%;min-height:0;margin:0;border:0;border-radius:0}.data-viewer .result-grid-scrollport{background:transparent}.data-sql code{font:inherit}.data-sql .tok-keyword,.data-sql .tok-atom{color:var(--main);font-weight:650}.data-sql .tok-typeName,.data-sql .tok-namespace{color:var(--success)}@supports (color: color-mix(in lab,red,red)){.data-sql .tok-typeName,.data-sql .tok-namespace{color:color-mix(in oklch,var(--success) 65%,var(--foreground))}}.data-sql .tok-string,.data-sql .tok-string2{color:var(--main)}@supports (color: color-mix(in lab,red,red)){.data-sql .tok-string,.data-sql .tok-string2{color:color-mix(in oklch,var(--main) 58%,var(--foreground))}}.data-sql .tok-number,.data-sql .tok-bool{color:var(--danger)}@supports (color: color-mix(in lab,red,red)){.data-sql .tok-number,.data-sql .tok-bool{color:color-mix(in oklch,var(--danger) 65%,var(--foreground))}}.data-sql .tok-operator{color:var(--success)}@supports (color: color-mix(in lab,red,red)){.data-sql .tok-operator{color:color-mix(in oklch,var(--success) 55%,var(--foreground))}}.data-sql .tok-comment{color:var(--chrome-text);font-style:italic}.data-navigation{display:flex;align-items:center;gap:8px;flex:none;min-width:0;min-height:var(--workspace-header-height);padding:14px var(--workspace-gutter);background:transparent}.data-nav-history,.data-nav-actions{display:flex;align-items:center;gap:2px;flex:none}.data-navigation button{display:grid;place-items:center;flex:none;width:32px;height:34px;padding:0;border:0;border-radius:6px;background:transparent;color:var(--chrome-text)}.data-navigation button:not(:disabled):hover{color:var(--main);background:var(--main)}@supports (color: color-mix(in lab,red,red)){.data-navigation button:not(:disabled):hover{background:color-mix(in oklch,var(--main) 8%,transparent)}}.data-navigation button:disabled{opacity:.25}.data-navigation button[aria-pressed=true]{color:var(--main)}.data-nav-back svg{transform:rotate(180deg)}.data-navigation .data-nav-sql{display:flex;gap:4px;width:auto;padding:0 6px;font:11px var(--workspace-mono)}.data-toolbar .data-sql-escape{gap:1px;opacity:.65;padding:4px;min-width:28px}.data-toolbar .data-sql-escape:hover{opacity:1}.data-body-sql{container:sql-workspace / inline-size}.sql-workbench{display:flex;flex-direction:column;flex:1;min-height:0;min-width:0}.sql-title{display:flex;align-items:center;gap:8px;flex:none;color:var(--workspace-muted);margin-bottom:8px}.sql-title input{min-width:0;width:100%;border:0;background:transparent;color:var(--workspace-text);padding:6px 2px;font-size:13px}.sql-editor-pane{height:clamp(150px,30vh,300px);min-height:100px;max-height:55vh;resize:vertical;overflow:hidden;flex:none;border:1px solid var(--workspace-line);border-radius:var(--workspace-radius);background:var(--workspace-raised)}@supports (color: color-mix(in lab,red,red)){.sql-editor-pane{background:color-mix(in oklch,var(--workspace-raised) 20%,transparent)}}.sql-editor-pane .cm-editor{outline:none}.sql-editor-pane:focus-within{border-color:var(--workspace-line-hot)}.sql-actions{flex:none;flex-wrap:wrap;gap:4px;padding:9px 0;border-bottom:1px solid var(--workspace-line)}.sql-actions button{gap:5px}.sql-actions .sql-run{color:var(--workspace-bright);background:var(--workspace-wash)}.sql-key-hint{color:var(--workspace-muted);font:10px var(--workspace-mono);opacity:.6;margin-left:4px}.sql-results{display:flex;flex-direction:column;flex:1;min-height:180px;min-width:0;overflow:auto;padding-bottom:8px}.sql-result-tools{display:flex;align-items:center;justify-content:space-between;gap:8px;flex:none;padding:8px 0}.sql-result-tools [role=tablist]{display:flex;gap:4px}.sql-result-tools button{padding:4px 7px;border:0;border-bottom:1px solid transparent;border-radius:0;background:transparent;color:var(--workspace-muted);font-size:12px}.sql-result-tools button[aria-selected=true]{color:var(--workspace-text);border-color:var(--workspace-line-hot)}.sql-result-tools small,.sql-plan-caption{font:11px/1.5 var(--workspace-mono);color:var(--workspace-muted)}.sql-plan-caption{margin:0 0 8px}.sql-results>.data-result{flex:1;min-height:220px;margin:0;height:auto}.sql-results>.data-loading{flex:none;margin:0;min-height:36px;padding:0 0 8px}.sql-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;flex:1;min-height:160px;padding:24px;text-align:center;color:var(--workspace-muted)}.sql-empty>svg{opacity:.45}.sql-empty p{margin:0;color:var(--workspace-text)}.sql-empty small{max-width:290px;font-size:12px;line-height:1.6}.sql-explain>div{width:100%;min-width:0;min-height:0;flex:1}.sql-explain{display:flex;flex:1;min-height:260px;overflow:hidden;border:1px solid var(--workspace-line);border-radius:var(--workspace-radius)}@container sql-workspace (max-width:390px){.sql-key-hint{display:none}.sql-actions button{padding:5px 6px;font-size:12px}}.data-navigation h1{flex:1;min-width:0;margin:0;font-size:15px;font-weight:500;line-height:1.4;overflow:hidden;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}.data-navigation h1.data-nav-placeholder{font-size:12px;font-weight:400;color:var(--chrome-text)}.query-error{color:var(--danger)}.data-loading{display:flex;align-items:center;gap:12px;min-height:88px;margin:16px 0;padding:8px 0;border:0;background:transparent;color:var(--workspace-muted);line-height:1.5}.data-loading canvas{display:block;flex:none}.data-loading span{min-width:0;overflow-wrap:anywhere}.data-loading-compact{min-height:20px;margin:0;padding:0;border:0;border-radius:0;background:transparent;gap:8px}.search-status{min-height:20px;display:flex;align-items:center}.entry-search{display:flex;align-items:flex-end;gap:8px}.entry-search label{flex:1;min-width:0}.entry-search button{flex:none;min-height:48px}.entry-note{padding:10px 12px;border-left:2px solid var(--main);background:transparent;line-height:1.5}@media(max-width:360px){.data-navigation{padding:14px 10px;gap:5px}.data-navigation button{width:30px}.data-body{padding:14px 12px}.result-caption{flex-wrap:wrap}.actions .muted{width:100%}}.data-search-section{margin:0 0 26px}.data-search-section>h2{display:flex;align-items:center;gap:8px;margin:0 0 12px;font:500 14px/1.4 var(--workspace-font);color:var(--workspace-bright)}.data-search-section>h2>span{color:var(--workspace-muted);font:11px/1.4 var(--workspace-mono)}.data-search-section+.data-search-section{padding-top:18px;border-top:1px solid var(--workspace-line)}.metric-results{--panel: var(--background);--panel-2: var(--chrome-bg);--void: var(--background);--bone: var(--foreground);--bone-bright: var(--foreground);--fog: var(--chrome-text);--dim: var(--chrome-text);--line: var(--chrome-border);--jade: var(--main);--amber: var(--main);--sans: var(--data-font,var(--font-superior-sans,system-ui,sans-serif));--mono: var(--data-font-mono,var(--font-superior-mono,monospace));--serif: var(--font-superior-serif,Georgia,serif);--rvbbit-type-small: 11px;--rvbbit-type-body: 14px}.data-metric-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,174px),1fr));gap:8px}.data-metric-tile{min-width:0}.metric-results .metric-gallery-card{border:1px solid var(--workspace-line);border-radius:var(--workspace-radius)}.metric-results .metric-gallery-card,.metric-results .metric-card-content{min-height:250px;height:250px}.metric-results .metric-card-content{padding:13px}.metric-results .metric-card-value{top:14px;right:13px;max-width:calc(100% - 26px);font-size:clamp(28px,15cqw,48px)}.metric-results .metric-card-chart{inset:38px 0 128px}.metric-results .metric-card-title{height:42px;font-size:18px;line-height:1.17;margin-bottom:0}.metric-results .metric-card-description{margin-bottom:0;height:32px}.metric-results .metric-card-foot{gap:6px;margin-top:8px;letter-spacing:0;text-transform:none;font-size:10px}.metric-results .metric-card-delta{font-size:10px;padding:4px 6px}.metric-results .metric-card-status{top:13px;left:13px}:root[data-theme=light] .metric-results .metric-card-content,:root[data-theme=light] .metric-results .metric-card-delta{text-shadow:none}.data-body-workspace{padding-top:0;padding-bottom:0;display:flex;flex-direction:column}.cube-workbench{display:grid;flex:1;min-width:0;min-height:0;grid-template-columns:minmax(0,1fr);grid-template-rows:auto auto auto auto auto auto minmax(140px,1fr) auto;grid-template-areas:"toolbar" "fields" "columns" "rows" "values" "filters" "result" "options";column-gap:14px}.cube-toolbar{grid-area:toolbar}.data-toolbar{display:flex;align-items:center;gap:3px;min-height:38px;padding:0 0 6px}.data-toolbar button,.cube-fields-close,.data-inspector header button{display:inline-flex;align-items:center;justify-content:center;flex:none;min-height:30px;padding:5px 8px;border:0;border-radius:5px;background:transparent;color:var(--workspace-muted);font-size:12px}.data-toolbar button:hover:not(:disabled),.data-toolbar button[aria-pressed=true],.cube-fields-close:hover,.data-inspector header button:hover{color:var(--main);background:var(--workspace-wash)}.data-toolbar .data-publish{margin-left:auto;color:var(--workspace-text);border:1px solid var(--workspace-line);white-space:nowrap}.cube-sidebar{display:contents}.cube-fields{grid-area:fields;display:none;min-height:0;min-width:0;max-height:220px;padding:8px 0 12px}.cube-fields-open{display:flex;flex-direction:column}.cube-fields header{display:flex;align-items:center;justify-content:space-between;flex:none}.cube-fields header strong{font-size:12px;font-weight:500}.cube-fields header small{margin-left:5px}.cube-fields .workspace-field{flex:none;min-height:30px;padding:6px 8px;font-size:11px;margin:5px 0}.cube-field-catalog{overflow:auto;min-height:0;flex:1;padding:0 4px 3px 0}.cube-field-group{margin:8px 0}.cube-field-group h3{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--workspace-muted);margin:8px 0}.cube-field-list,.cube-field-actions{display:flex;gap:5px;flex-wrap:wrap}.cube-field{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;border:1px solid var(--workspace-line);border-radius:999px;padding:4px 8px;font:11px var(--workspace-mono);color:var(--main);background:var(--workspace-wash);cursor:grab;text-align:left}.cube-field.cube-measure{color:var(--success)}.cube-field[aria-pressed=true]{border-color:var(--main)}.cube-field-actions{flex:none;padding-top:8px;align-items:center}.cube-field-actions>span{width:100%;font-size:11px;color:var(--workspace-muted);overflow-wrap:anywhere}.cube-field-actions .workspace-control{padding:4px 8px;min-height:26px;font-size:11px}.cube-lane{position:relative;display:flex;gap:8px;align-items:flex-start;min-width:0;min-height:32px;padding:5px 0;border-top:1px solid var(--workspace-line);transition:background .15s}.cube-lane-rows{grid-area:rows}.cube-lane-columns{grid-area:columns}.cube-lane-values{grid-area:values}.cube-lane-filters{grid-area:filters}.cube-lane-over{background:var(--workspace-wash);outline:1px dashed var(--main)}.cube-lane-label{flex:none;width:48px;font:11px/22px var(--workspace-font);color:var(--workspace-muted)}.cube-pills{display:flex;flex:1;min-width:0;gap:5px;flex-wrap:wrap;align-items:center}.cube-pills>.relative{max-width:100%;position:static}.cube-empty{font-size:11px;line-height:22px;color:var(--workspace-muted);opacity:.65}.cube-workbench .absolute{background:var(--workspace-bg);max-width:min(280px,calc(100vw - 48px))}.cube-pills .absolute{left:56px;right:auto;top:100%}.cube-shelf-foot{grid-area:options;position:relative;display:flex;gap:8px;align-items:center;justify-content:space-between;padding:8px 0;border-top:1px solid var(--workspace-line);min-width:0}.cube-shelf-foot>span{font-size:10px;color:var(--workspace-muted)}.cube-shelf-foot>.relative{position:static;max-width:100%}.cube-shelf-foot .absolute{inset:auto 0 100% auto}.cube-result{grid-area:result;display:flex;flex-direction:column;min-width:0;min-height:0;position:relative}.cube-result .data-result{flex:1;min-height:140px;height:auto;margin:0;border-radius:0;border:0;border-top:1px solid var(--workspace-line)}.cube-result .result-caption{padding:8px 0;gap:4px;font-size:10px;flex-wrap:wrap}.cube-result .data-loading{flex:none;padding:5px 0}.cube-result .query-error{font-size:12px;max-height:80px;overflow:auto;margin:6px 0}.cube-result-empty{display:grid;place-items:center;flex:1;font-size:12px;color:var(--workspace-muted);padding:20px;text-align:center}.data-origin{display:flex;gap:4px;align-items:center;padding:5px 0;font-size:11px;color:var(--workspace-muted);min-width:0}.data-origin button{border:0;padding:0;background:transparent;color:var(--main);white-space:nowrap;text-overflow:ellipsis;overflow:hidden;font:inherit}.meaning-area{min-width:0;overflow-wrap:anywhere}.area-about{font-size:14px;line-height:1.65;margin:0 0 12px;color:var(--workspace-text);white-space:pre-line}.area-links,.area-topic-list{display:flex;flex-wrap:wrap;gap:6px}.area-links{margin:0 0 20px}.area-links button,.area-question button,.area-topic-list button{background:transparent;border:0;color:var(--main);font:12px/1.5 var(--workspace-font);padding:5px 0;text-align:left}.area-links button{padding:4px 8px;border-bottom:1px solid var(--workspace-line)}.area-links button:hover,.area-question button:hover:not(:disabled){color:var(--workspace-bright)}.area-composer{margin:0 0 24px}.area-composer textarea{min-height:72px}.area-composer .actions{justify-content:space-between}.area-composer .actions .muted{flex:1;min-width:140px}.area-topic-list button{border:1px solid var(--workspace-line);border-radius:16px;padding:4px 10px;color:var(--workspace-muted)}.area-topic-list button[aria-pressed=true]{color:var(--main);background:var(--workspace-wash);border-color:var(--workspace-line-hot)}.area-topic-about{font-size:13px;line-height:1.6;color:var(--workspace-muted)}.area-question-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));column-gap:20px;row-gap:4px}.area-question{min-width:0;border-top:1px solid var(--workspace-line);padding:10px 0;display:flex;flex-direction:column}.area-question p{font-size:13px;line-height:1.6;margin:0 0 6px}.area-question>div{display:flex;flex-wrap:wrap;gap:4px 18px;margin-top:auto;padding-top:5px}.area-question h3{font-size:13px;font-weight:500;color:var(--workspace-bright);margin:0 0 6px}.area-question small{overflow-wrap:anywhere}.area-metric-idea p.muted{font:11px/1.6 var(--workspace-mono)}.area-metric-idea button,.area-breakdown button{display:inline-flex;align-items:center;gap:5px;min-height:30px}.data-inspector{flex:none;max-height:45%;overflow:auto;padding:10px 0;border-top:1px solid var(--workspace-line);font-size:12px}.data-inspector header{display:flex;align-items:center;justify-content:space-between;gap:8px}.data-inspector header strong{font-weight:500}.data-inspector .data-sql{font-size:11px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere}.data-inspector textarea{min-height:65px}.data-inspector form button{margin-top:6px}@media(min-width:580px){.cube-workbench{grid-template-columns:170px minmax(0,1fr);grid-template-rows:auto auto auto minmax(140px,1fr);grid-template-areas:"toolbar toolbar" "sidebar columns" "sidebar rows" "sidebar result"}.cube-toolbar .cube-fields-toggle,.cube-fields-close{display:none}.cube-sidebar{grid-area:sidebar;display:flex;flex-direction:column;min-height:0;min-width:0}.cube-fields{display:flex;flex-direction:column;max-height:60%;padding-top:5px;padding-bottom:12px;flex:0 1 auto}.cube-sidebar .cube-lane{flex:none}.cube-sidebar .cube-shelf-foot{margin-top:auto;flex:none}.cube-field-list{flex-direction:column;align-items:flex-start}.cube-field{width:100%}.cube-lane-values,.cube-lane-filters{flex-direction:column;gap:3px;padding:8px 0}.cube-lane-values .cube-pills,.cube-lane-filters .cube-pills{flex-direction:column;align-items:flex-start;width:100%}.cube-lane-values .absolute,.cube-lane-filters .absolute{left:0;top:auto;bottom:100%}.cube-shelf-foot .absolute{left:0;right:auto}.cube-result{border-left:1px solid var(--workspace-line);padding-left:12px}}@media(min-width:760px){.cube-workbench{grid-template-columns:190px minmax(0,1fr);column-gap:18px}}.query-workbench{display:flex;flex-direction:column;flex:1;min-width:0;min-height:0}.query-workbench .data-toolbar button{gap:5px}.query-canvas,.query-result{display:flex;flex-direction:column;flex:1;min-width:0;min-height:0}.query-result .data-result{flex:1;min-height:140px;height:auto;margin:0;border-radius:0;border:0;border-top:1px solid var(--workspace-line)}.query-result .result-caption{padding:8px 0;gap:4px;font-size:10px;flex-wrap:wrap}.query-result .data-loading{flex:none;padding:8px 0}.query-result .query-error,.query-read-only{flex:none;font-size:12px;line-height:1.5;max-height:90px;overflow:auto;margin:6px 0}.query-read-only{color:var(--workspace-muted)}.query-result-empty{display:grid;place-items:center;flex:1;min-height:60px;font-size:12px;color:var(--workspace-muted);padding:20px;text-align:center}.query-composer{flex:none;border-top:1px solid var(--workspace-line);padding:10px 0}.query-composer textarea.workspace-field{min-height:52px;max-height:140px;margin-top:5px;padding:8px 10px;line-height:1.5;font-size:12px}.query-composer .actions{margin-top:6px;justify-content:flex-end;gap:8px;flex-wrap:nowrap}.query-composer .actions .muted{flex:1;font-size:10px;line-height:1.4}.query-composer .workspace-control{min-height:30px;font-size:12px;flex:none;padding:5px 10px}.query-sources.data-inspector{display:flex;flex-direction:column;flex:none;min-height:0;min-width:0;max-height:35%;padding:6px 0 12px;border-bottom:1px solid var(--workspace-line);border-top:0}.query-source-list{overflow:auto;flex:1;min-height:0;padding-right:6px}.query-source+.query-source{border-top:1px solid var(--workspace-line);margin-top:14px;padding-top:10px}.query-source header{margin-top:8px}.query-source h2{font-size:13px;line-height:1.4;font-weight:500;margin:0;overflow-wrap:anywhere}.query-source p{font-size:12px;line-height:1.5;overflow-wrap:anywhere}.query-source small{display:block;font:10px/1.5 var(--workspace-mono);overflow-wrap:anywhere;color:var(--workspace-muted)}.query-add-source{flex:none;padding-top:8px}.query-add-source select.workspace-field{margin-top:4px;font-size:11px;padding:6px;min-height:30px}.query-assumptions{flex:none;max-height:90px;overflow:auto;font-size:11px;color:var(--workspace-muted)}.query-assumptions .assumption{margin:6px 0;font-size:11px}.query-result .clarification{flex:none;max-height:50%;overflow:auto;font-size:13px;line-height:1.5;padding:8px 0 14px}.clarification p{margin:0 0 8px}.clarification-options{display:flex;gap:6px;flex-wrap:wrap}.clarification .workspace-control{font-size:12px;padding:5px 10px;min-height:30px}.clarification form{display:flex;align-items:flex-end;gap:8px;margin-top:10px}.clarification label{flex:1;min-width:0}.clarification form button{flex:none}.clarification input.workspace-field{margin-top:4px;padding:8px;font-size:12px;min-height:34px}.data-sql{font-size:12px;line-height:1.65;tab-size:2;color:var(--foreground)}@media(min-width:580px){.query-canvas-sources{display:grid;grid-template-columns:190px minmax(0,1fr);column-gap:14px;grid-template-rows:minmax(0,1fr)}.query-sources.data-inspector{max-height:none;padding-right:12px;border-right:1px solid var(--workspace-line);border-bottom:0}.query-sources .field-list{display:block}.query-sources .field-list dt{margin-top:9px;color:var(--workspace-text)}.query-sources .field-list dd{font-size:11px;overflow-wrap:anywhere}}.area-breakdown dl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:5px 12px;margin:10px 0;font-size:12px;line-height:1.5}.area-breakdown dt{color:var(--workspace-muted);font-weight:500}.area-breakdown dd{margin:0;overflow-wrap:anywhere;min-width:0}@property --tw-translate-x{syntax: "*"; inherits: false; initial-value: 0;}@property --tw-translate-y{syntax: "*"; inherits: false; initial-value: 0;}@property --tw-translate-z{syntax: "*"; inherits: false; initial-value: 0;}@property --tw-rotate-x{syntax: "*"; inherits: false;}@property --tw-rotate-y{syntax: "*"; inherits: false;}@property --tw-rotate-z{syntax: "*"; inherits: false;}@property --tw-skew-x{syntax: "*"; inherits: false;}@property --tw-skew-y{syntax: "*"; inherits: false;}@property --tw-space-y-reverse{syntax: "*"; inherits: false; initial-value: 0;}@property --tw-border-style{syntax: "*"; inherits: false; initial-value: solid;}@property --tw-leading{syntax: "*"; inherits: false;}@property --tw-font-weight{syntax: "*"; inherits: false;}@property --tw-tracking{syntax: "*"; inherits: false;}@property --tw-ordinal{syntax: "*"; inherits: false;}@property --tw-slashed-zero{syntax: "*"; inherits: false;}@property --tw-numeric-figure{syntax: "*"; inherits: false;}@property --tw-numeric-spacing{syntax: "*"; inherits: false;}@property --tw-numeric-fraction{syntax: "*"; inherits: false;}@property --tw-shadow{syntax: "*"; inherits: false; initial-value: 0 0 #0000;}@property --tw-shadow-color{syntax: "*"; inherits: false;}@property --tw-shadow-alpha{syntax: "<percentage>"; inherits: false; initial-value: 100%;}@property --tw-inset-shadow{syntax: "*"; inherits: false; initial-value: 0 0 #0000;}@property --tw-inset-shadow-color{syntax: "*"; inherits: false;}@property --tw-inset-shadow-alpha{syntax: "<percentage>"; inherits: false; initial-value: 100%;}@property --tw-ring-color{syntax: "*"; inherits: false;}@property --tw-ring-shadow{syntax: "*"; inherits: false; initial-value: 0 0 #0000;}@property --tw-inset-ring-color{syntax: "*"; inherits: false;}@property --tw-inset-ring-shadow{syntax: "*"; inherits: false; initial-value: 0 0 #0000;}@property --tw-ring-inset{syntax: "*"; inherits: false;}@property --tw-ring-offset-width{syntax: "<length>"; inherits: false; initial-value: 0px;}@property --tw-ring-offset-color{syntax: "*"; inherits: false; initial-value: #fff;}@property --tw-ring-offset-shadow{syntax: "*"; inherits: false; initial-value: 0 0 #0000;}@property --tw-blur{syntax: "*"; inherits: false;}@property --tw-brightness{syntax: "*"; inherits: false;}@property --tw-contrast{syntax: "*"; inherits: false;}@property --tw-grayscale{syntax: "*"; inherits: false;}@property --tw-hue-rotate{syntax: "*"; inherits: false;}@property --tw-invert{syntax: "*"; inherits: false;}@property --tw-opacity{syntax: "*"; inherits: false;}@property --tw-saturate{syntax: "*"; inherits: false;}@property --tw-sepia{syntax: "*"; inherits: false;}@property --tw-drop-shadow{syntax: "*"; inherits: false;}@property --tw-drop-shadow-color{syntax: "*"; inherits: false;}@property --tw-drop-shadow-alpha{syntax: "<percentage>"; inherits: false; initial-value: 100%;}@property --tw-drop-shadow-size{syntax: "*"; inherits: false;}@property --tw-backdrop-blur{syntax: "*"; inherits: false;}@property --tw-backdrop-brightness{syntax: "*"; inherits: false;}@property --tw-backdrop-contrast{syntax: "*"; inherits: false;}@property --tw-backdrop-grayscale{syntax: "*"; inherits: false;}@property --tw-backdrop-hue-rotate{syntax: "*"; inherits: false;}@property --tw-backdrop-invert{syntax: "*"; inherits: false;}@property --tw-backdrop-opacity{syntax: "*"; inherits: false;}@property --tw-backdrop-saturate{syntax: "*"; inherits: false;}@property --tw-backdrop-sepia{syntax: "*"; inherits: false;}@property --tw-duration{syntax: "*"; inherits: false;}@keyframes pulse{50%{opacity:.5}}@layer properties{@supports ((-webkit-hyphens: none) and (not (margin-trim: inline))) or ((-moz-orient: inline) and (not (color:rgb(from red r g b)))){*,:before,:after,::backdrop{--tw-translate-x: 0;--tw-translate-y: 0;--tw-translate-z: 0;--tw-rotate-x: initial;--tw-rotate-y: initial;--tw-rotate-z: initial;--tw-skew-x: initial;--tw-skew-y: initial;--tw-space-y-reverse: 0;--tw-border-style: solid;--tw-leading: initial;--tw-font-weight: initial;--tw-tracking: initial;--tw-ordinal: initial;--tw-slashed-zero: initial;--tw-numeric-figure: initial;--tw-numeric-spacing: initial;--tw-numeric-fraction: initial;--tw-shadow: 0 0 #0000;--tw-shadow-color: initial;--tw-shadow-alpha: 100%;--tw-inset-shadow: 0 0 #0000;--tw-inset-shadow-color: initial;--tw-inset-shadow-alpha: 100%;--tw-ring-color: initial;--tw-ring-shadow: 0 0 #0000;--tw-inset-ring-color: initial;--tw-inset-ring-shadow: 0 0 #0000;--tw-ring-inset: initial;--tw-ring-offset-width: 0px;--tw-ring-offset-color: #fff;--tw-ring-offset-shadow: 0 0 #0000;--tw-blur: initial;--tw-brightness: initial;--tw-contrast: initial;--tw-grayscale: initial;--tw-hue-rotate: initial;--tw-invert: initial;--tw-opacity: initial;--tw-saturate: initial;--tw-sepia: initial;--tw-drop-shadow: initial;--tw-drop-shadow-color: initial;--tw-drop-shadow-alpha: 100%;--tw-drop-shadow-size: initial;--tw-backdrop-blur: initial;--tw-backdrop-brightness: initial;--tw-backdrop-contrast: initial;--tw-backdrop-grayscale: initial;--tw-backdrop-hue-rotate: initial;--tw-backdrop-invert: initial;--tw-backdrop-opacity: initial;--tw-backdrop-saturate: initial;--tw-backdrop-sepia: initial;--tw-duration: initial}}}@layer properties{*,:before,:after,::backdrop{--tw-translate-x: 0;--tw-translate-y: 0;--tw-translate-z: 0;--tw-rotate-x: initial;--tw-rotate-y: initial;--tw-rotate-z: initial;--tw-skew-x: initial;--tw-skew-y: initial;--tw-space-y-reverse: 0;--tw-border-style: solid;--tw-leading: initial;--tw-font-weight: initial;--tw-tracking: initial;--tw-ordinal: initial;--tw-slashed-zero: initial;--tw-numeric-figure: initial;--tw-numeric-spacing: initial;--tw-numeric-fraction: initial;--tw-shadow: 0 0 #0000;--tw-shadow-color: initial;--tw-shadow-alpha: 100%;--tw-inset-shadow: 0 0 #0000;--tw-inset-shadow-color: initial;--tw-inset-shadow-alpha: 100%;--tw-ring-color: initial;--tw-ring-shadow: 0 0 #0000;--tw-inset-ring-color: initial;--tw-inset-ring-shadow: 0 0 #0000;--tw-ring-inset: initial;--tw-ring-offset-width: 0px;--tw-ring-offset-color: #fff;--tw-ring-offset-shadow: 0 0 #0000;--tw-blur: initial;--tw-brightness: initial;--tw-contrast: initial;--tw-grayscale: initial;--tw-hue-rotate: initial;--tw-invert: initial;--tw-opacity: initial;--tw-saturate: initial;--tw-sepia: initial;--tw-drop-shadow: initial;--tw-drop-shadow-color: initial;--tw-drop-shadow-alpha: 100%;--tw-drop-shadow-size: initial;--tw-backdrop-blur: initial;--tw-backdrop-brightness: initial;--tw-backdrop-contrast: initial;--tw-backdrop-grayscale: initial;--tw-backdrop-hue-rotate: initial;--tw-backdrop-invert: initial;--tw-backdrop-opacity: initial;--tw-backdrop-saturate: initial;--tw-backdrop-sepia: initial;--tw-duration: initial}}', M6 = ':host{display:block;height:100%;min-height:0;color:var(--ink);font:14px/1.45 var(--font);--workspace-font:var(--font);--workspace-mono:var(--mono);--workspace-text:var(--ink);--workspace-bright:var(--ink);--workspace-muted:var(--muted);--workspace-line:var(--line);--workspace-line-hot:var(--accent);--workspace-bg:var(--panel);--workspace-raised:var(--panel);--workspace-accent:var(--accent);--workspace-inset:color-mix(in oklch,var(--panel) 88%,#000);--workspace-wash:color-mix(in srgb,var(--accent) 9%,transparent);--workspace-radius:7px;--workspace-control-height:32px;--workspace-control-font:500 12px/1.3 var(--font);--main:var(--accent);--foreground:var(--ink);--chrome-text:var(--muted);--chrome-border:var(--line);--chrome-bg:var(--panel);--doc-bg:var(--panel);--background:var(--page);--data-font:var(--font);--font-mono:var(--mono);--success:var(--rvbbit-success,#69bca6);--chart-3:var(--accent)}.meaning-pivot-surface{height:100%;min-width:0;min-height:0;container:meaning-worksheet / inline-size}.cube-workbench{height:100%;grid-template-columns:250px minmax(0,1fr);grid-template-rows:auto auto auto minmax(0,1fr);grid-template-areas:"toolbar toolbar" "sidebar columns" "sidebar rows" "sidebar result";column-gap:20px}.cube-toolbar{min-height:36px;padding:0 0 6px;flex-wrap:wrap}.meaning-worksheet-hint{margin-left:auto;color:var(--workspace-muted);font-size:11px}.cube-sidebar{grid-area:sidebar;display:flex;flex-direction:column;min-height:0;min-width:0;overflow:visible}.cube-fields{display:flex;flex-direction:column;min-height:0;max-height:none;flex:0 1 auto;padding-top:4px;padding-bottom:14px}.cube-field-catalog{overflow:auto;max-height:none}.cube-field-list{flex-direction:column;align-items:stretch}.cube-field{width:100%;font-size:12px;padding:5px 9px}.cube-toolbar .cube-fields-toggle,.cube-fields-close{display:none}.cube-lane{min-height:36px;padding:6px 0;align-self:start;width:100%}.cube-lane-label{font-size:12px;line-height:24px;width:56px}.cube-pills>.relative>.group{font-size:11px;padding:3px 8px;max-width:100%}.cube-sidebar .cube-lane{flex:none;flex-direction:column;gap:5px;padding:12px 0}.cube-sidebar .cube-pills{flex-direction:column;align-items:flex-start;width:100%}.cube-sidebar .cube-shelf-foot{margin-top:auto;flex:none}.cube-result{border-left:1px solid var(--workspace-line);padding-left:16px;min-width:0;min-height:0}.cube-result .meaning-result{margin:0;flex:1;min-height:0;border:0;overflow:hidden;background:var(--workspace-bg)}.meaning-result>.flex{min-height:0;flex:1}.meaning-result .result-grid-scrollport{min-height:0}.meaning-result-toolbar{display:flex;gap:12px;align-items:center;flex:none;border-bottom:1px solid var(--workspace-line);min-height:40px}.meaning-result-toolbar [role=tablist]{display:flex;gap:6px;flex:1;min-width:0;flex-wrap:wrap}.meaning-result-toolbar button{padding:10px 6px;border-bottom:2px solid transparent;color:var(--workspace-muted);font-size:12px}.meaning-result-toolbar button[aria-selected=true]{border-color:var(--main);color:var(--workspace-text)}.meaning-result-toolbar>span{font:11px var(--workspace-mono);color:var(--workspace-muted);white-space:nowrap}.meaning-result-footer{display:flex;align-items:center;gap:8px;flex:none;min-height:30px;padding:5px 0;border-top:1px solid var(--workspace-line);color:var(--workspace-muted);font-size:11px;flex-wrap:wrap}.meaning-result-footer>span{flex:1}.meaning-result-footer button,.meaning-row-selection button{padding:4px 7px;color:var(--main);border:1px solid var(--workspace-line);border-radius:4px;white-space:nowrap}.meaning-row-selection{display:flex;gap:8px;align-items:center;flex:none;padding:8px 0;font:11px var(--workspace-mono)}.meaning-row-selection>span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}.meaning-grid-empty{padding:24px;color:var(--workspace-muted)}.meaning-grid-error{color:var(--danger);font-size:12px;padding:8px 0}.cube-pills .absolute,.cube-shelf-foot .absolute{max-width:min(300px,calc(100cqw - 24px));z-index:60}@container meaning-worksheet (max-width:680px){.cube-workbench{grid-template-columns:minmax(0,1fr);grid-template-rows:auto auto auto auto auto auto minmax(220px,1fr) auto;grid-template-areas:"toolbar" "fields" "columns" "rows" "values" "filters" "result" "options";column-gap:0}.cube-sidebar{display:contents}.cube-fields{display:none;max-height:240px}.cube-fields-open{display:flex}.cube-toolbar .cube-fields-toggle,.cube-fields-close{display:inline-flex}.cube-field-list{flex-direction:row;flex-wrap:wrap}.cube-field{width:auto}.cube-lane-values,.cube-lane-filters,.cube-sidebar .cube-lane{flex-direction:row;gap:8px;padding:6px 0}.cube-sidebar .cube-pills{flex-direction:row;align-items:center}.cube-result{border:0;padding:0}.cube-shelf-foot{margin:0}.meaning-worksheet-hint{display:none}.cube-lane-values .absolute,.cube-lane-filters .absolute{left:0;bottom:auto;top:100%}}', fm = { 16: "bool", 20: "int8", 21: "int2", 23: "int4", 25: "text", 700: "float4", 701: "float8", 1700: "numeric", 1082: "date", 1114: "timestamp", 1184: "timestamptz", 114: "json", 3802: "jsonb" }, k6 = Object.fromEntries(Object.entries(fm).map(([i, f]) => [f, Number(i)]));
function z6(i) {
  const { catalog: f, spec: u, result: o, records: m, view: p, selection: h } = i, [v, x] = s.useState(!1), b = o?.spec.groupBy || [], D = { ...u, pivot: u.pivot && o?.spec.pivot?.column.name === u.pivot.column.name && o.spec.pivot.grain === u.pivot.grain ? { ...u.pivot, values: o.spec.pivot.values } : u.pivot }, C = s.useMemo(() => p === "records" ? (m?.fields || []).map((G) => ({ name: G.name, dataTypeId: k6[G.type] || 25, dataTypeName: G.display_type })) : (o?.columns || []).map((G) => {
    const Q = o?.column_types?.find((de) => de.name === G)?.oid || 25;
    return { name: G, dataTypeId: Q, dataTypeName: fm[Q] || `oid:${Q}` };
  }), [p, m?.fields, o?.columns, o?.column_types]), T = p === "records" ? m?.rows : o?.rows, L = (G) => i.onSelect(b.map((Q) => ({ field: Q.column.name, value: G[Q.column.name], ...Q.grain ? { grain: Q.grain } : {} })));
  return /* @__PURE__ */ g.jsxs(
    S6,
    {
      rowCount: !0,
      value: { catalog: f, spec: D, applied: "" },
      onChange: i.onChange,
      probe: i.probe,
      toolbar: /* @__PURE__ */ g.jsxs(g.Fragment, { children: [
        /* @__PURE__ */ g.jsx("button", { "aria-label": "Refresh data", title: "Refresh data", onClick: i.onRefresh, disabled: i.loading, children: /* @__PURE__ */ g.jsx(Fh, { className: "h-4 w-4" }) }),
        /* @__PURE__ */ g.jsx("span", { className: "meaning-worksheet-hint", children: "Drag fields to a shelf · click a pill to edit" })
      ] }),
      children: [
        /* @__PURE__ */ g.jsxs("div", { className: "meaning-result-toolbar", children: [
          /* @__PURE__ */ g.jsxs("div", { role: "tablist", "aria-label": "Data results", children: [
            /* @__PURE__ */ g.jsx("button", { role: "tab", "aria-selected": p === "results", onClick: () => i.onView("results"), children: "Query result" }),
            /* @__PURE__ */ g.jsxs("button", { role: "tab", "aria-selected": p === "records", onClick: () => i.onView("records"), children: [
              "Source rows",
              h?.length ? " · selected group" : ""
            ] })
          ] }),
          /* @__PURE__ */ g.jsx("span", { role: "status", children: i.loading ? "Updating…" : i.error ? "Not updated" : i.elapsed != null ? `${Math.round(i.elapsed)} ms` : "" })
        ] }),
        i.error && /* @__PURE__ */ g.jsx("p", { className: "meaning-grid-error", role: "alert", children: i.error }),
        p === "records" && h?.length && /* @__PURE__ */ g.jsxs("div", { className: "meaning-row-selection", children: [
          /* @__PURE__ */ g.jsx("span", { children: h.map((G) => `${G.field}: ${G.value ?? "NULL"}`).join(" · ") }),
          /* @__PURE__ */ g.jsx("button", { onClick: () => i.onSelect(null), children: "Clear selection" })
        ] }),
        /* @__PURE__ */ g.jsxs("section", { className: "data-result meaning-result", "aria-label": p === "records" ? "Source records" : "Query result", "aria-busy": i.loading, children: [
          T && /* @__PURE__ */ g.jsx(
            Gg,
            {
              columns: C,
              rows: T,
              transposed: v,
              onTransposedChange: x,
              filterLabel: "Search these rows",
              portalContainer: i.portalContainer,
              onOpenRow: p === "records" && !i.loading && !i.error ? ({ row: G }) => i.onOpenRecord(G) : void 0,
              extraCellMenuItems: p === "results" && b.length && !i.loading && !i.error ? ({ row: G }) => [{ id: "source-records", label: "Explore source rows", icon: xd, onSelect: () => L(G) }] : void 0
            },
            p
          ),
          !T && /* @__PURE__ */ g.jsx("div", { className: "meaning-grid-empty", children: i.loading ? "Reading source data…" : i.error ? "Edit the shelves or refresh to try again." : "Add a measure to Values, or browse Source rows." })
        ] }),
        /* @__PURE__ */ g.jsx("footer", { className: "meaning-result-footer", children: p === "records" ? /* @__PURE__ */ g.jsxs(g.Fragment, { children: [
          /* @__PURE__ */ g.jsxs("span", { children: [
            m?.rows.length ? `${(m.offset || 0) + 1}–${(m.offset || 0) + m.rows.length}` : "No rows",
            m?.has_more ? " · more available" : "",
            " · live preview"
          ] }),
          /* @__PURE__ */ g.jsx("button", { disabled: i.loading || !m?.offset, onClick: () => i.onPage((m?.offset || 0) - 40), children: "Previous rows" }),
          /* @__PURE__ */ g.jsx("button", { disabled: i.loading || !m?.has_more || (m?.offset || 0) >= 1e4, onClick: () => i.onPage((m?.offset || 0) + 40), children: "Next rows" })
        ] }) : /* @__PURE__ */ g.jsxs("span", { children: [
          o?.has_more ? "First 200 groups · use shelf filters or Top N to refine." : `${o?.rows.length ?? 0} result ${(o?.rows.length ?? 0) === 1 ? "row" : "rows"}.`,
          b.length ? " Right-click a cell to explore source rows." : ""
        ] }) })
      ]
    }
  );
}
function H6(i, f) {
  const u = i.attachShadow({ mode: "open" }), o = document.createElement("style");
  o.textContent = E6 + `
` + A6 + `
` + M6;
  const m = document.createElement("div");
  m.className = "meaning-pivot-surface";
  const p = document.createElement("div");
  p.className = "meaning-grid-portals", u.append(o, m, p);
  const h = Nh.createRoot(m), v = (x) => h.render(/* @__PURE__ */ g.jsx(z6, { ...x, portalContainer: p }));
  return v(f), { update: v, destroy: () => h.unmount() };
}
export {
  H6 as mountPivot
};
