import { useState } from "react";
import type { WorkspaceView } from "./Sidebar";
export type DataView = Exclude<WorkspaceView, "chat" | "persona-connection">;
export const DATA_GROUPS: { label: string; items: { id: DataView; label: string }[] }[] = [
  { label: "대화 데이터", items: [{ id: "messages", label: "메시지" }] },
  { label: "기억", items: [{ id: "memory", label: "장기 기억" }, { id: "episodes", label: "에피소드" }, { id: "narratives", label: "서사" }] },
  { label: "상태", items: [{ id: "emotion", label: "감정" }, { id: "goals-needs", label: "목표와 욕구" }, { id: "decisions", label: "결정" }, { id: "intentions", label: "의도" }] },
  { label: "지식", items: [{ id: "knowledge", label: "학습 지식" }, { id: "world-model", label: "세계 모델" }] },
  { label: "관계", items: [{ id: "relationship", label: "관계 기록" }, { id: "preferences", label: "선호" }] },
  { label: "자아", items: [{ id: "self-model", label: "자아 모델" }] },
  { label: "진단", items: [{ id: "stats", label: "통계" }, { id: "debug", label: "디버그" }] },
];
export function DataNavigation({ view, onChange }: { view: DataView; onChange: (view: DataView) => void }) {
  const group = DATA_GROUPS.find(value => value.items.some(item => item.id === view))!;
  const [remembered, setRemembered] = useState<Record<string, DataView>>({});
  const choose = (next: DataView) => { setRemembered(values => ({ ...values, [DATA_GROUPS.find(value => value.items.some(item => item.id === next))!.label]: next })); onChange(next); };
  return <header className="data-navigation"><h1>데이터 관리</h1><nav aria-label="데이터 분류" className="underline-nav data-primary-nav">{DATA_GROUPS.map(value => <button key={value.label} aria-current={value === group ? "page" : undefined} onClick={() => choose(remembered[value.label] ?? value.items[0].id)}>{value.label}</button>)}</nav><nav aria-label="데이터 항목" className="underline-nav data-secondary-nav">{group.items.map(item => <button key={item.id} aria-current={item.id === view ? "page" : undefined} onClick={() => choose(item.id)}>{item.label}</button>)}</nav></header>;
}
