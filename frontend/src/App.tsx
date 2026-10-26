import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { RequireAuth } from "./auth";
import { AgentBadge } from "./components/AgentBadge";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Sidebar } from "./components/Sidebar";
import { useSideLayout } from "./components/sideLayout";
import { useLiveInvalidation } from "./live";
import AdminPage from "./pages/AdminPage";
import DashboardPage from "./pages/DashboardPage";
import LoginPage from "./pages/LoginPage";
import SettingsPage from "./pages/SettingsPage";
import SimulatorPage from "./pages/SimulatorPage";
import WorkspacePage from "./pages/WorkspacePage";

/** 주소가 바뀌면 오류 상태를 초기화한다 */
function RouteBoundary({ children }: { children: React.ReactNode }) {
  const { pathname } = useLocation();
  return <ErrorBoundary key={pathname} label="이 화면">{children}</ErrorBoundary>;
}

/** 왼쪽 사이드바 + 작업 영역. 프로젝트 화면은 '대화(가운데) + 라이브 캔버스(오른쪽)'로 나뉜다 (WorkspacePage).
 *  상단바는 두지 않는다 (DDS 디자인). badge: 일반 페이지 오른쪽 위에 PC Agent 상태 */
function Shell({ children, badge = true }: { children: React.ReactNode; badge?: boolean }) {
  const side = useSideLayout();
  useLiveInvalidation();
  return (
    <div className={`app-shell agentic ${side.collapsed ? "side-collapsed" : ""}`} style={{ "--side-w": `${side.width}px` } as React.CSSProperties}>
      <Sidebar layout={side} />
      <div className="app-main">
        {badge && <div className="main-topright"><AgentBadge /></div>}
        <RouteBoundary>{children}</RouteBoundary>
      </div>
    </div>
  );
}

const protect = (el: React.ReactNode, badge = true) => <RequireAuth><Shell badge={badge}>{el}</Shell></RequireAuth>;

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={protect(<DashboardPage />, false)} />
      <Route path="/projects/:pid" element={protect(<WorkspacePage />, false)} />
      <Route path="/projects/:pid/new" element={protect(<WorkspacePage />, false)} />
      <Route path="/projects/:pid/c/:sid" element={protect(<WorkspacePage />, false)} />
      <Route path="/projects/:pid/*" element={<Navigate to=".." replace />} />
      <Route path="/simulator/:mid" element={protect(<SimulatorPage />)} />
      <Route path="/settings" element={protect(<SettingsPage />)} />
      <Route path="/admin" element={protect(<AdminPage />)} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
