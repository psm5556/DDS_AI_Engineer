import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./auth";
import { ToastProvider } from "./toast";
// 한글 Pretendard(OFL, 앱에 포함 — 외부 CDN 없음)
import "pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css";
import "./design/tokens.css";
import "./styles.css";
import "./layout.css";
import "./app.css";
import { applyTheme, initialTheme } from "./theme";

applyTheme(initialTheme()); // 그리기 전에 적용 (밝은 화면이 잠깐 보이는 깜빡임 방지)

const qc = new QueryClient({ defaultOptions: { queries: { staleTime: 10_000, retry: 1, refetchOnWindowFocus: false } } });

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AuthProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
