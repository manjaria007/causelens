import { jsxs as s, Fragment as f, jsx as t } from "react/jsx-runtime";
import { useState as h, useEffect as g, Component as p } from "react";
import { useChatLauncher as y, ChatEmbed as b } from "@kirocrew/app-sdk";
import { PageHeader as k, Card as m, CardTitle as u, Btn as x, EmptyState as v } from "@kirocrew/app-sdk/ui";
import { FileWarning as w } from "lucide-react";
const c = [
  {
    key: "detective",
    agent: "causelens-detective",
    label: "Detective",
    role: "Checks data quality, confirms the change is real, and finds when it started and where it is concentrated. It does not say why.",
    starter: "Why did checkout conversion fall? Check the data first, confirm the change and localize it."
  },
  {
    key: "scientist",
    agent: "causelens-scientist",
    label: "Scientist",
    role: "Tests each logged event against a named hypothesis and estimates the effect of a supported one, with its checks, assumptions and evidence strength.",
    starter: "Test the candidate explanations for the checkout conversion change, using the windows the Detective found."
  },
  {
    key: "experiment-designer",
    agent: "causelens-experiment-designer",
    label: "Experiment designer",
    role: "Recommends the randomized experiment that would settle what the data cannot, sized by the power tools.",
    starter: "Design the experiment that would settle the checkout conversion question, using the same windows."
  }
], C = "detective";
function T(e) {
  return c.find((r) => r.key === e) ?? c[0];
}
function L(e) {
  return `causelens-${e}`;
}
const l = "/apps/causelens/ui/console.html";
function d(e) {
  return "error" in e ? { kind: "missing", detail: `The reference console could not be loaded (${e.error}).` } : e.ok ? e.contentType && !e.contentType.toLowerCase().includes("text/html") ? { kind: "missing", detail: "The reference console file is not an HTML page." } : { kind: "ready" } : { kind: "missing", detail: `The reference console is not available in this install (HTTP ${e.status ?? "error"}).` };
}
const i = "Static reference result", E = "This console was written by the repository's template reasoner from the reference dataset's logged tool results. It is not the output of a native Crew agent or of a hosted model, and it does not update when the agents run.", N = "Answers here come from the native CauseLens agents. Every number they state should come from a recorded MCP tool call shown in the transcript; if a tool fails or the evidence is insufficient, the agent should say so and recommend an experiment rather than name a cause.";
class S extends p {
  constructor() {
    super(...arguments), this.state = { error: null };
  }
  static getDerivedStateFromError(r) {
    return { error: r instanceof Error ? r.message : String(r) };
  }
  render() {
    return this.state.error ? /* @__PURE__ */ s("p", { role: "alert", className: "text-sm", children: [
      "The ",
      this.props.what,
      " could not be shown: ",
      this.state.error
    ] }) : this.props.children;
  }
}
function A() {
  const [e, r] = h(C), n = T(e), { openChat: a } = y();
  return /* @__PURE__ */ s(m, { children: [
    /* @__PURE__ */ t(u, { children: "Investigate with the native agents" }),
    /* @__PURE__ */ t("p", { className: "text-sm text-muted mb-3", children: N }),
    /* @__PURE__ */ t("div", { role: "tablist", "aria-label": "CauseLens agent", className: "flex flex-wrap gap-2 mb-2", children: c.map((o) => /* @__PURE__ */ t(
      "button",
      {
        type: "button",
        role: "tab",
        "aria-selected": o.key === e,
        onClick: () => r(o.key),
        className: o.key === e ? "px-3 py-1 rounded border font-semibold" : "px-3 py-1 rounded border text-muted",
        children: o.label
      },
      o.key
    )) }),
    /* @__PURE__ */ s("p", { className: "text-sm mb-3", children: [
      /* @__PURE__ */ t("strong", { children: n.label }),
      " (",
      n.agent,
      "): ",
      n.role
    ] }),
    /* @__PURE__ */ t(S, { what: "chat", children: /* @__PURE__ */ t(b, { slotKey: L(n.key), agent: n.agent, placeholder: n.starter, startAtBottom: !0 }, n.key) }),
    /* @__PURE__ */ t("div", { className: "mt-2", children: /* @__PURE__ */ s(x, { onClick: () => a({ agent: n.agent, message: n.starter, autoSend: !1 }), children: [
      "Open ",
      n.label,
      " in a full chat (draft, not sent)"
    ] }) })
  ] });
}
function I() {
  const [e, r] = h({ kind: "checking" });
  return g(() => {
    let n = !0;
    return fetch(l, { method: "GET", credentials: "same-origin" }).then((a) => n && r(d({ ok: a.ok, status: a.status, contentType: a.headers.get("content-type") }))).catch((a) => n && r(d({ error: a instanceof Error ? a.message : String(a) }))), () => {
      n = !1;
    };
  }, []), /* @__PURE__ */ s(m, { children: [
    /* @__PURE__ */ s(u, { children: [
      "Reference console (",
      i.toLowerCase(),
      ")"
    ] }),
    /* @__PURE__ */ s("p", { className: "text-sm text-muted mb-3", children: [
      /* @__PURE__ */ s("strong", { children: [
        i,
        "."
      ] }),
      " ",
      E
    ] }),
    e.kind === "checking" && /* @__PURE__ */ t("p", { className: "text-sm text-muted", children: "Loading the reference console…" }),
    e.kind === "missing" && /* @__PURE__ */ t(v, { icon: /* @__PURE__ */ t(w, { size: 20 }), title: "Reference console unavailable", subtitle: e.detail }),
    e.kind === "ready" && /* @__PURE__ */ t(
      "iframe",
      {
        title: `CauseLens reference console (${i.toLowerCase()})`,
        src: l,
        sandbox: "",
        referrerPolicy: "no-referrer",
        style: { width: "100%", height: "70vh", border: "1px solid var(--border, #d5dae3)", borderRadius: 8 }
      }
    )
  ] });
}
function _() {
  return /* @__PURE__ */ s(f, { children: [
    /* @__PURE__ */ t(k, { title: "CauseLens", subtitle: "Why did a metric move? Three agents, read-only deterministic analytics, and no overclaiming." }),
    /* @__PURE__ */ s("div", { className: "px-6 pb-8 overflow-y-auto flex-1 min-h-0 flex flex-col gap-4", children: [
      /* @__PURE__ */ t(A, {}),
      /* @__PURE__ */ t(I, {})
    ] })
  ] });
}
export {
  _ as default
};
