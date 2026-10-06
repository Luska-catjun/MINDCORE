import { useState } from "react";
import type { WorkspaceView } from "./Sidebar";
export type DataView = Exclude<WorkspaceView, "chat" | "persona-connection">;
export const DATA_GROUPS: { label: string; items: { id: DataView; label: string }[] }[] = [
  { label: "대화 데이터", items: [{ id: "messages", label: "Messages" }] },
  { label: "기억", items: [{ id: "memory", label: "Memory" }, { id: "episodes", label: "Episodes" }, { id: "narratives", label: "Narrative" }] },
  { label: "상태", items: [{ id: "emotion", label: "Emotion" }, { id: "goals-needs", label: "Goals & Needs" }, { id: "decisions", label: "Decisions" }, { id: "intentions", label: "Intentions" }] },
  { label: "지식", items: [{ id: "knowledge", label: "Knowledge" }, { id: "world-model", label: "World Model" }] },
  { label: "관계", items: [{ id: "relationship", label: "Relationship" }, { id: "preferences", label: "Preferences" }] },
  { label: "자아", items: [{ id: "self-model", label: "Self Model" }] },
  { label: "진단", items: [{ id: "stats", label: "Stats" }, { id: "debug", label: "Debug" }] },
];
export function DataNavigation({ view, onChange }: { view: DataView; onChange: (view: DataView) => void }) {
  const group = DATA_GROUPS.find(value => value.items.some(item => item.id === view))!;
  const [remembered, setRemembered] = useState<Record<string, DataView>>({});
  const choose = (next: DataView) => { setRemembered(values => ({ ...values, [DATA_GROUPS.find(value => value.items.some(item => item.id === next))!.label]: next })); onChange(next); };
  return <header className="data-navigation"><h1>데이터 관리</h1><nav aria-label="데이터 분류" className="compact-subnav">{DATA_GROUPS.map(value => <button key={value.label} aria-current={value === group ? "page" : undefined} onClick={() => choose(remembered[value.label] ?? value.items[0].id)}>{value.label}</button>)}</nav><nav aria-label="데이터 항목" className="compact-subnav">{group.items.map(item => <button key={item.id} aria-current={item.id === view ? "page" : undefined} onClick={() => choose(item.id)}>{item.label}</button>)}</nav></header>;
}
