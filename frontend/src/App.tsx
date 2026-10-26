import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { RequireAuth } from "./auth";
import { AgentPanel } from "./chat/AgentPanel";
import { AgentBadge } from "./components/AgentBadge";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Sidebar } from "./components/Sidebar";
import { useConversaLayout, useSideLayout } from "./components/sideLayout";
import { useLiveInvalidation } from "./live";
import AdminPage from "./pages/AdminPage";
import DashboardPage from "./pages/DashboardPage";
import LoginPage from "./pages/LoginPage";
import ProjectPage from "./pages/ProjectPage";
import SettingsPage from "./pages/SettingsPage";
import SimulatorPage from "./pages/SimulatorPage";

/** 주소가 바뀌면 오류 상태를 초기화한다 */
function RouteBoundary({ children }: { children: React.ReactNode }) {
  const { pathname } = useLocation();
  return <ErrorBoundary key={pathname} label="이 화면">{children}</ErrorBoundary>;
}

/** 왼쪽 프로젝트 목록 + 가운데 작업 영역 + 오른쪽 에이전트 채팅. 상단바는 두지 않는다 (DDS 디자인) */
function Shell({ children }: { children: React.ReactNode }) {
  const side = useSideLayout();
  const cv = useConversaLayout();
  useLiveInvalidation();
  return (
    <div className={`app-shell ${side.collapsed ? "side-collapsed" : ""} ${cv.collapsed ? "cv-collapsed" : ""}`}
      style={{ "--side-w": `${side.width}px`, "--cv-w": `${cv.width}px` } as React.CSSProperties}>
      <Sidebar layout={side} />
      <div className="app-main">
        <div className="main-topright"><AgentBadge /></div>
        <RouteBoundary>{children}</RouteBoundary>
      </div>
      <ErrorBoundary label="에이전트 채팅"><AgentPanel layout={cv} /></ErrorBoundary>
    </div>
  );
}

const protect = (el: React.ReactNode) => <RequireAuth><Shell>{el}</Shell></RequireAuth>;

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={protect(<DashboardPage />)} />
      <Route path="/projects/:pid/*" element={protect(<ProjectPage />)} />
      <Route path="/simulator/:mid" element={protect(<SimulatorPage />)} />
      <Route path="/settings" element={protect(<SettingsPage />)} />
      <Route path="/admin" element={protect(<AdminPage />)} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
