export type WorkspaceView = "chat" | "messages" | "memory" | "emotion" | "knowledge" | "preferences" | "episodes" | "decisions" | "intentions" | "narratives" | "self-model" | "world-model" | "relationship" | "goals-needs" | "stats" | "debug";

interface SidebarProps {
  activeView: WorkspaceView;
  onViewChange: (view: WorkspaceView) => void;
  isOpen: boolean;
  onClose: () => void;
  backendStatus: "checking" | "connected" | "error";
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
  { id: "debug", label: "Debug" },
];

export function Sidebar({
  activeView,
  onViewChange,
  isOpen,
  onClose,
  backendStatus,
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
              {item.label}
            </button>
          ))}
        </nav>

        <div className="sidebar-status">
          <span className={`status-dot status-dot-${backendStatus}`} aria-hidden="true" />
          <span>{backendStatus === "connected" ? "Backend connected" : backendStatus === "error" ? "Backend unavailable" : "Checking backend"}</span>
        </div>
      </aside>
    </>
  );
}
