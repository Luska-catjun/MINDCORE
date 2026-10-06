import Markdown from "react-markdown";
import { openPublicUrl, safePublicUrl } from "../services/productSupport";
export function ReleaseNotes({ markdown }: { markdown: string }) {
  return <div className="release-notes"><Markdown skipHtml components={{ a: ({ href, children }) => href && safePublicUrl(href) ? <a href={href} onClick={(event) => { event.preventDefault(); void openPublicUrl(href).catch(() => undefined); }}>{children}</a> : <span>{children}</span>, img: () => null }}>{markdown}</Markdown></div>;
}
export function ReleaseNotesModal({ version, markdown, onClose }: { version: string; markdown: string; onClose: () => void }) {
  return <div className="product-modal-backdrop" onClick={onClose}><section role="dialog" aria-modal="true" aria-label="업데이트 내용" className="product-modal" onClick={(event) => event.stopPropagation()}><h2>업데이트 내용</h2><p className="workspace-muted">버전 {version}</p><ReleaseNotes markdown={markdown || "상세 업데이트 내용을 불러오지 못했습니다."} /><div className="product-modal-actions"><button onClick={onClose}>닫기</button></div></section></div>;
}
