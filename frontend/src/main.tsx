import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider, QueryCache } from "@tanstack/react-query";
import { Toaster, toast } from "sonner";

import App from "./App";
import { ThemeProvider, useTheme } from "@/lib/theme";
// Bundled (not CDN) so the app keeps its identity offline. Variable font =
// one file for every weight the wordmark uses.
import "@fontsource-variable/archivo";
import "./styles.css";

// Surface query failures instead of rendering confidently-wrong empties.
// Toast only on the TRANSITION into the error state (polling queries keep
// failing every interval — re-toasting each cycle would loop forever), and
// share one toast id so an outage shows a single toast, not a stack.
const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (error, query) => {
      if (query.state.errorUpdateCount > 1) return;
      toast.error("Failed to load data", {
        id: "query-error",
        description:
          error instanceof Error ? error.message : "Backend unreachable?",
      });
    },
  }),
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
  },
});

function ThemedToaster() {
  const { resolvedTheme } = useTheme();
  return (
    <Toaster
      theme={resolvedTheme}
      position="bottom-right"
      richColors
      closeButton
    />
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <App />
        <ThemedToaster />
      </ThemeProvider>
    </QueryClientProvider>
  </React.StrictMode>
);
