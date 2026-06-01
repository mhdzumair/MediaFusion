// Mermaid is bundled by MkDocs Material — this file just ensures diagrams
// pick up the correct colour scheme when the page theme changes.
document$.subscribe(() => {
  const isDark = document.documentElement.getAttribute("data-md-color-scheme") === "slate";
  if (window.mermaid) {
    window.mermaid.initialize({ theme: isDark ? "dark" : "default" });
  }
});
