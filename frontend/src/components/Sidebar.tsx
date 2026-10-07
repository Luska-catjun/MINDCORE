export type WorkspaceView = "chat" | "messages" | "memory" | "emotion" | "knowledge" | "preferences" | "episodes" | "decisions" | "intentions" | "narratives" | "self-model" | "world-model" | "relationship" | "goals-needs" | "stats" | "debug" | "persona-connection";

export type ProductSection = "chat" | "data" | "settings" | "feedback";
interface SidebarProps {
  activeSection: ProductSection;
  onSectionChange: (section: ProductSection) => void;
  isOpen: boolean;
  onClose: () => void;
  backendStatus: "checking" | "connected" | "error";
  personaBindingState?: string;
  unreadCount?: number;
}

const NAVIGATION: Array<{ id: ProductSection; label: string }> = [
  { id: "chat", label: "대화" }, { id: "data", label: "데이터 관리" },
  { id: "settings", label: "앱 설정" }, { id: "feedback", label: "피드백" },
];

export function Sidebar({
  activeSection,
  onSectionChange,
  isOpen,
  onClose,
  backendStatus,
  personaBindingState = "NOT_APPLICABLE",
  unreadCount = 0,
}: SidebarProps) {
  return (
    <>
      {isOpen && <div className="sidebar-backdrop" onClick={onClose} />}

      <aside className={`sidebar ${isOpen ? "sidebar-open" : ""}`}>
        <div className="sidebar-header">
          <span className="sidebar-title">MINDCORE</span>
          <span className="sidebar-subtitle">나의 Persona</span>
        </div>

        <nav className="workspace-nav" aria-label="MindCore workspace">
          {NAVIGATION.map((item) => (
            <button
              type="button"
              key={item.id}
              className={`workspace-nav-item ${
                activeSection === item.id ? "workspace-nav-item-active" : ""
              }`}
              aria-current={activeSection === item.id ? "page" : undefined}
              onClick={() => {
                onSectionChange(item.id);
                onClose();
              }}
            >
              {item.label}{item.id === "chat" && unreadCount > 0 && <span className="proactive-unread" aria-label={`${unreadCount} unread proactive messages`}>{unreadCount}</span>}
            </button>
          ))}
        </nav>

        <div className="sidebar-status">
          <span className={`status-dot status-dot-${backendStatus}`} aria-hidden="true" />
          <span>{backendStatus === "connected" ? "백엔드 연결됨" : backendStatus === "error" ? "백엔드 연결 불가" : "백엔드 확인 중"}</span>
        </div>
        {backendStatus === "connected" && personaBindingState !== "NOT_APPLICABLE" && <div className="sidebar-status">
          <span>{personaBindingState === "BOUND_MATCH" ? "Persona 연결됨" : personaBindingState === "UNBOUND" ? "Persona 연결 대기" : personaBindingState === "BOUND_MISMATCH" ? "Persona 불일치" : "Persona 연결 불가"}</span>
        </div>}
      </aside>
    </>
  );
}
