import { jsxs as t, Fragment as a, jsx as e } from "react/jsx-runtime";
import { ChatEmbed as n } from "@kirocrew/app-sdk";
import { PageHeader as i, Card as s, CardTitle as r } from "@kirocrew/app-sdk/ui";
function d() {
  return /* @__PURE__ */ t(a, { children: [
    /* @__PURE__ */ e(
      i,
      {
        title: "CauseLens",
        subtitle: "Why did a metric move? Three agents, read-only deterministic analytics, and no overclaiming."
      }
    ),
    /* @__PURE__ */ t("div", { className: "px-6 pb-8 overflow-y-auto flex-1 min-h-0", children: [
      /* @__PURE__ */ t(s, { children: [
        /* @__PURE__ */ e(r, { children: "Investigate" }),
        /* @__PURE__ */ e("p", { className: "text-sm text-muted mb-3", children: 'Ask a question about the reference dataset, for example "Why did checkout conversion fall?". The causelens-detective, causelens-scientist and causelens-experiment-designer agents call the CauseLens tools; every number in their answers should come from a recorded tool call, and their Activity shows which.' }),
        /* @__PURE__ */ e(
          n,
          {
            slotKey: "causelens-investigation",
            agent: "causelens-detective",
            placeholder: "Why did checkout conversion fall?"
          }
        )
      ] }),
      /* @__PURE__ */ t(s, { children: [
        /* @__PURE__ */ e(r, { children: "Reference console (static)" }),
        /* @__PURE__ */ t("p", { className: "text-sm text-muted mb-3", children: [
          /* @__PURE__ */ e("strong", { children: "Static reference result." }),
          " This page was written by the repository's template reasoner from the reference dataset's logged tool results. It is not the output of a native Crew agent or of a hosted model."
        ] }),
        /* @__PURE__ */ e(
          "iframe",
          {
            title: "CauseLens reference console (static)",
            src: "/apps/causelens/ui/console.html",
            sandbox: "",
            style: { width: "100%", height: "70vh", border: "1px solid var(--border, #d5dae3)", borderRadius: 8 }
          }
        )
      ] })
    ] })
  ] });
}
export {
  d as default
};
