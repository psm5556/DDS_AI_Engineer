import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { RequireAuth } from "./auth";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Sidebar } from "./components/Sidebar";
import { useSideLayout } from "./components/sideLayout";
import { useLiveInvalidation } from "./live";
import AdminPage from "./pages/AdminPage";
import DashboardPage from "./pages/DashboardPage";
import LoginPage from "./pages/LoginPage";
import SettingsPage from "./pages/SettingsPage";
import ToolsPage from "./pages/ToolsPage";
import SimulatorPage, { SharedSimulatorPage } from "./pages/SimulatorPage";
import WorkspacePage from "./pages/WorkspacePage";

/** 주소가 바뀌면 오류 상태를 초기화한다 */
function RouteBoundary({ children }: { children: React.ReactNode }) {
  const { pathname } = useLocation();
  return <ErrorBoundary key={pathname} label="이 화면">{children}</ErrorBoundary>;
}

/** 왼쪽 사이드바 + 작업 영역. 프로젝트 화면은 '대화(가운데) + 라이브 캔버스(오른쪽)'로 나뉜다 (WorkspacePage).
 *  상단바는 두지 않는다. PC Agent 상태는 사이드바 아래에 한 번만 보인다 */
function Shell({ children }: { children: React.ReactNode }) {
  const side = useSideLayout();
  useLiveInvalidation();
  // 화면 전환 등장: 같은 프로젝트 안에서 대화를 바꿀 때처럼 주소만 바뀌면 다시 재생하지 않는다
  const { pathname } = useLocation();
  const [, first, second] = pathname.split("/");
  const section = first === "projects" ? `projects/${second}` : first;
  return (
    <div className={`app-shell agentic ${side.collapsed ? "side-collapsed" : ""}`} style={{ "--side-w": `${side.width}px` } as React.CSSProperties}>
      <Sidebar layout={side} />
      <div className="app-main">
        <div key={section} className="page-fade"><RouteBoundary>{children}</RouteBoundary></div>
      </div>
    </div>
  );
}

const protect = (el: React.ReactNode) => <RequireAuth><Shell>{el}</Shell></RequireAuth>;

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={protect(<DashboardPage />)} />
      <Route path="/projects/:pid" element={protect(<WorkspacePage />)} />
      <Route path="/projects/:pid/new" element={protect(<WorkspacePage />)} />
      <Route path="/projects/:pid/c/:sid" element={protect(<WorkspacePage />)} />
      <Route path="/projects/:pid/*" element={<Navigate to=".." replace />} />
      <Route path="/simulator/:mid" element={protect(<SimulatorPage />)} />
      <Route path="/s/:token" element={protect(<SharedSimulatorPage />)} />
      <Route path="/settings" element={protect(<SettingsPage />)} />
      <Route path="/tools" element={protect(<ToolsPage />)} />
      <Route path="/admin" element={protect(<AdminPage />)} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
