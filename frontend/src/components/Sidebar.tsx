export type WorkspaceView = "chat" | "messages" | "memory" | "emotion" | "knowledge" | "preferences" | "episodes" | "decisions" | "intentions" | "narratives" | "self-model" | "world-model" | "relationship" | "goals-needs" | "stats" | "debug" | "persona-connection";

interface SidebarProps {
  activeView: WorkspaceView;
  onViewChange: (view: WorkspaceView) => void;
  isOpen: boolean;
  onClose: () => void;
  backendStatus: "checking" | "connected" | "error";
  personaBindingState?: string;
  unreadCount?: number;
}

const NAVIGATION: Array<{ id: WorkspaceView; label: string }> = [
  { id: "chat", label: "Chat" },
  { id: "messages", label: "Messages" },
  { id: "memory", label: "Memory" },
  { id: "emotion", label: "Emotion" },
  { id: "knowledge", label: "Knowledge" },
  { id: "preferences", label: "Preferences" },
  { id: "episodes", label: "Episodes" },
  { id: "decisions", label: "Decisions" },
  { id: "intentions", label: "Intentions" },
  { id: "narratives", label: "Narrative" },
  { id: "self-model", label: "Self Model" },
  { id: "world-model", label: "World Model" },
  { id: "relationship", label: "Relationship" },
  { id: "goals-needs", label: "Goals & Needs" },
  { id: "stats", label: "Stats" },
  { id: "persona-connection", label: "Persona Connection" },
];

export function Sidebar({
  activeView,
  onViewChange,
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
          <span className="sidebar-subtitle">Persona console</span>
        </div>

        <nav className="workspace-nav" aria-label="MindCore workspace">
          {NAVIGATION.map((item) => (
            <button
              type="button"
              key={item.id}
              className={`workspace-nav-item ${
                activeView === item.id ? "workspace-nav-item-active" : ""
              }`}
              aria-current={activeView === item.id ? "page" : undefined}
              onClick={() => {
                onViewChange(item.id);
                onClose();
              }}
            >
              {item.label}{item.id === "chat" && unreadCount > 0 && <span className="proactive-unread" aria-label={`${unreadCount} unread proactive messages`}>{unreadCount}</span>}
            </button>
          ))}
        </nav>

        <div className="sidebar-status">
          <span className={`status-dot status-dot-${backendStatus}`} aria-hidden="true" />
          <span>{backendStatus === "connected" ? "Backend connected" : backendStatus === "error" ? "Backend unavailable" : "Checking backend"}</span>
        </div>
        {backendStatus === "connected" && personaBindingState !== "NOT_APPLICABLE" && <div className="sidebar-status">
          <span>{personaBindingState === "BOUND_MATCH" ? "Persona connected" : personaBindingState === "UNBOUND" ? "Persona not yet bound" : personaBindingState === "BOUND_MISMATCH" ? "Persona mismatch" : "Persona connection unavailable"}</span>
        </div>}
      </aside>
    </>
  );
}
