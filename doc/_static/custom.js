window.addEventListener("DOMContentLoaded", () => {
	const headerButtons = document.querySelector(".article-header-buttons");

	if (headerButtons) {
		const discordBtn = document.createElement("a");

		discordBtn.title = "Join our Discord server";
		discordBtn.href = "https://discord.com/invite/9fMpq3tc8u";
		discordBtn.target = "_blank";
		discordBtn.className = "btn btn-sm";

		/* The Discord SVG icon is from Bootstrap Icons: https://icons.getbootstrap.com/icons/discord/ */
		discordBtn.innerHTML = `<span class="btn__icon-container"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" class="svg-inline--fa" viewBox="0 0 16 16"><path d="M13.545 2.907a13.2 13.2 0 0 0-3.257-1.011.05.05 0 0 0-.052.025c-.141.25-.297.577-.406.833a12.2 12.2 0 0 0-3.658 0 8 8 0 0 0-.412-.833.05.05 0 0 0-.052-.025c-1.125.194-2.22.534-3.257 1.011a.04.04 0 0 0-.021.018C.356 6.024-.213 9.047.066 12.032q.003.022.021.037a13.3 13.3 0 0 0 3.995 2.02.05.05 0 0 0 .056-.019q.463-.63.818-1.329a.05.05 0 0 0-.01-.059l-.018-.011a9 9 0 0 1-1.248-.595.05.05 0 0 1-.02-.066l.015-.019q.127-.095.248-.195a.05.05 0 0 1 .051-.007c2.619 1.196 5.454 1.196 8.041 0a.05.05 0 0 1 .053.007q.121.1.248.195a.05.05 0 0 1-.004.085 8 8 0 0 1-1.249.594.05.05 0 0 0-.03.03.05.05 0 0 0 .003.041c.24.465.515.909.817 1.329a.05.05 0 0 0 .056.019 13.2 13.2 0 0 0 4.001-2.02.05.05 0 0 0 .021-.037c.334-3.451-.559-6.449-2.366-9.106a.03.03 0 0 0-.02-.019m-8.198 7.307c-.789 0-1.438-.724-1.438-1.612s.637-1.613 1.438-1.613c.807 0 1.45.73 1.438 1.613 0 .888-.637 1.612-1.438 1.612m5.316 0c-.788 0-1.438-.724-1.438-1.612s.637-1.613 1.438-1.613c.807 0 1.451.73 1.438 1.613 0 .888-.631 1.612-1.438 1.612" /></svg></span>`;

		headerButtons.prepend(discordBtn);
	}

	// Add RSS feed link to the blog README page only
	if (window.location.pathname.endsWith('/blog/README.html')) {
		const rssLink = document.createElement('link');
		rssLink.rel = 'alternate';
		rssLink.type = 'application/rss+xml';
		rssLink.title = 'PyRIT Blog RSS Feed';
		rssLink.href = 'https://microsoft.github.io/PyRIT/blog/rss.xml';

		document.head.appendChild(rssLink);
	}
});
(function(cfg) {
	function e() {
        cfg.onInit && cfg.onInit(n)
    }
    var x, w, D, t, E, n, C = window, O = document, b = C.location, q = "script", I = "ingestionendpoint", L = "disableExceptionTracking", j = "ai.device.";
    "instrumentationKey"[x = "toLowerCase"](),
    w = "crossOrigin",
    D = "POST",
    t = "appInsightsSDK",
    E = cfg.name || "appInsights",
    (cfg.name || C[t]) && (C[t] = E),
    n = C[E] || function(g) {
        var f = !1
          , m = !1
          , h = {
            initialize: !0,
            queue: [],
            sv: "8",
            version: 2,
            config: g
        };
        function v(e, t) {
            var n = {}
              , i = "Browser";
            function a(e) {
                e = "" + e;
                return 1 === e.length ? "0" + e : e
            }
            return n[j + "id"] = i[x](),
            n[j + "type"] = i,
            n["ai.operation.name"] = b && b.pathname || "_unknown_",
            n["ai.internal.sdkVersion"] = "javascript:snippet_" + (h.sv || h.version),
            {
                time: (i = new Date).getUTCFullYear() + "-" + a(1 + i.getUTCMonth()) + "-" + a(i.getUTCDate()) + "T" + a(i.getUTCHours()) + ":" + a(i.getUTCMinutes()) + ":" + a(i.getUTCSeconds()) + "." + (i.getUTCMilliseconds() / 1e3).toFixed(3).slice(2, 5) + "Z",
                iKey: e,
                name: "Microsoft.ApplicationInsights." + e.replace(/-/g, "") + "." + t,
                sampleRate: 100,
                tags: n,
                data: {
                    baseData: {
                        ver: 2
                    }
                },
                ver: undefined,
                seq: "1",
                aiDataContract: undefined
            }
        }
        var n, i, t, a, y = -1, T = 0, S = ["js.monitor.azure.com", "js.cdn.applicationinsights.io", "js.cdn.monitor.azure.com", "js0.cdn.applicationinsights.io", "js0.cdn.monitor.azure.com", "js2.cdn.applicationinsights.io", "js2.cdn.monitor.azure.com", "az416426.vo.msecnd.net"], o = g.url || cfg.src, r = function() {
            return s(o, null)
        };
        function s(d, t) {
            if ((n = navigator) && (~(n = (n.userAgent || "").toLowerCase()).indexOf("msie") || ~n.indexOf("trident/")) && ~d.indexOf("ai.3") && (d = d.replace(/(\/)(ai\.3\.)([^\d]*)$/, function(e, t, n) {
                return t + "ai.2" + n
            })),
            !1 !== cfg.cr)
                for (var e = 0; e < S.length; e++)
                    if (0 < d.indexOf(S[e])) {
                        y = e;
                        break
                    }
            var n, i = function(e) {
                var a, t, n, i, o, r, s, c, u, l;
                h.queue = [],
                m || (0 <= y && T + 1 < S.length ? (a = (y + T + 1) % S.length,
                p(d.replace(/^(.*\/\/)([\w\.]*)(\/.*)$/, function(e, t, n, i) {
                    return t + S[a] + i
                })),
                T += 1) : (f = m = !0,
                s = d,
                !0 !== cfg.dle && (c = (t = function() {
                    var e, t = {}, n = g.connectionString;
                    if (n)
                        for (var i = n.split(";"), a = 0; a < i.length; a++) {
                            var o = i[a].split("=");
                            2 === o.length && (t[o[0][x]()] = o[1])
                        }
                    return t[I] || (e = (n = t.endpointsuffix) ? t.location : null,
                    t[I] = "https://" + (e ? e + "." : "") + "dc." + (n || "services.visualstudio.com")),
                    t
                }()).instrumentationkey || g.instrumentationKey || "",
                t = (t = (t = t[I]) && "/" === t.slice(-1) ? t.slice(0, -1) : t) ? t + "/v2/track" : g.endpointUrl,
                t = g.userOverrideEndpointUrl || t,
                (n = []).push((i = "SDK LOAD Failure: Failed to load Application Insights SDK script (See stack for details)",
                o = s,
                u = t,
                (l = (r = v(c, "Exception")).data).baseType = "ExceptionData",
                l.baseData.exceptions = [{
                    typeName: "SDKLoadFailed",
                    message: i.replace(/\./g, "-"),
                    hasFullStack: !1,
                    stack: i + "\nSnippet failed to load [" + o + "] -- Telemetry is disabled\nHelp Link: https://go.microsoft.com/fwlink/?linkid=2128109\nHost: " + (b && b.pathname || "_unknown_") + "\nEndpoint: " + u,
                    parsedStack: []
                }],
                r)),
                n.push((l = s,
                i = t,
                (u = (o = v(c, "Message")).data).baseType = "MessageData",
                (r = u.baseData).message = 'AI (Internal): 99 message:"' + ("SDK LOAD Failure: Failed to load Application Insights SDK script (See stack for details) (" + l + ")").replace(/\"/g, "") + '"',
                r.properties = {
                    endpoint: i
                },
                o)),
                s = n,
                c = t,
                JSON && ((u = C.fetch) && !cfg.useXhr ? u(c, {
                    method: D,
                    body: JSON.stringify(s),
                    mode: "cors"
                }) : XMLHttpRequest && ((l = new XMLHttpRequest).open(D, c),
                l.setRequestHeader("Content-type", "application/json"),
                l.send(JSON.stringify(s)))))))
            }, a = function(e, t) {
                m || setTimeout(function() {
                    !t && h.core || i()
                }, 500),
                f = !1
            }, p = function(e) {
                var n = O.createElement(q)
                  , e = (n.src = e,
                t && (n.integrity = t),
                n.setAttribute("data-ai-name", E),
                cfg[w]);
                return !e && "" !== e || "undefined" == n[w] || (n[w] = e),
                n.onload = a,
                n.onerror = i,
                n.onreadystatechange = function(e, t) {
                    "loaded" !== n.readyState && "complete" !== n.readyState || a(0, t)
                }
                ,
                cfg.ld && cfg.ld < 0 ? O.getElementsByTagName("head")[0].appendChild(n) : setTimeout(function() {
                    O.getElementsByTagName(q)[0].parentNode.appendChild(n)
                }, cfg.ld || 0),
                n
            };
            p(d)
        }
        cfg.sri && (n = o.match(/^((http[s]?:\/\/.*\/)\w+(\.\d+){1,5})\.(([\w]+\.){0,2}js)$/)) && 6 === n.length ? (d = "".concat(n[1], ".integrity.json"),
        i = "@".concat(n[4]),
        l = window.fetch,
        t = function(e) {
            if (!e.ext || !e.ext[i] || !e.ext[i].file)
                throw Error("Error Loading JSON response");
            var t = e.ext[i].integrity || null;
            s(o = n[2] + e.ext[i].file, t)
        }
        ,
        l && !cfg.useXhr ? l(d, {
            method: "GET",
            mode: "cors"
        }).then(function(e) {
            return e.json()["catch"](function() {
                return {}
            })
        }).then(t)["catch"](r) : XMLHttpRequest && ((a = new XMLHttpRequest).open("GET", d),
        a.onreadystatechange = function() {
            if (a.readyState === XMLHttpRequest.DONE)
                if (200 === a.status)
                    try {
                        t(JSON.parse(a.responseText))
                    } catch (e) {
                        r()
                    }
                else
                    r()
        }
        ,
        a.send())) : o && r();
        try {
            h.cookie = O.cookie
        } catch (k) {}
        function e(e) {
            for (; e.length; )
                !function(t) {
                    h[t] = function() {
                        var e = arguments;
                        f || h.queue.push(function() {
                            h[t].apply(h, e)
                        })
                    }
                }(e.pop())
        }
        var c, u, l = "track", d = "TrackPage", p = "TrackEvent", l = (e([l + "Event", l + "PageView", l + "Exception", l + "Trace", l + "DependencyData", l + "Metric", l + "PageViewPerformance", "start" + d, "stop" + d, "start" + p, "stop" + p, "addTelemetryInitializer", "setAuthenticatedUserContext", "clearAuthenticatedUserContext", "flush"]),
        h.SeverityLevel = {
            Verbose: 0,
            Information: 1,
            Warning: 2,
            Error: 3,
            Critical: 4
        },
        (g.extensionConfig || {}).ApplicationInsightsAnalytics || {});
        return !0 !== g[L] && !0 !== l[L] && (e(["_" + (c = "onerror")]),
        u = C[c],
        C[c] = function(e, t, n, i, a) {
            var o = u && u(e, t, n, i, a);
            return !0 !== o && h["_" + c]({
                message: e,
                url: t,
                lineNumber: n,
                columnNumber: i,
                error: a,
                evt: C.event
            }),
            o
        }
        ,
        g.autoExceptionInstrumented = !0),
        h
    }(cfg.cfg),
    (C[E] = n).queue && 0 === n.queue.length ? (n.queue.push(e),
    n.trackPageView({})) : e();
}
)({
    src: "https://js.monitor.azure.com/scripts/b/ai.3.gbl.min.js",
    // name: "appInsights", // Global SDK Instance name defaults to "appInsights" when not supplied
    // ld: 0, // Defines the load delay (in ms) before attempting to load the sdk. -1 = block page load and add to head. (default) = 0ms load after timeout,
    // useXhr: 1, // Use XHR instead of fetch to report failures (if available),
    // dle: true, // Prevent the SDK from reporting load failure log
    crossOrigin: "anonymous",
    // When supplied this will add the provided value as the cross origin attribute on the script tag
    // onInit: null, // Once the application insights instance has loaded and initialized this callback function will be called with 1 argument -- the sdk instance (DON'T ADD anything to the sdk.queue -- As they won't get called)
    // sri: false, // Custom optional value to specify whether fetching the snippet from integrity file and do integrity check
    cfg: {
        // Application Insights Configuration
        connectionString: "InstrumentationKey=d7b012c3-867f-4a43-afe4-ee9ba961f4fb;IngestionEndpoint=https://westus-0.in.applicationinsights.azure.com/;LiveEndpoint=https://westus.livediagnostics.monitor.azure.com/;ApplicationId=040097a7-729e-4c60-8e43-a2cae1986e15"
    }
});
