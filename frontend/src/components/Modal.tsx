import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

// 모달이 겹쳐 열리면 Esc는 맨 위 모달만 닫는다
const openModals: number[] = [];
let modalSeq = 0;

export function Modal({ title, onClose, children, footer, wide }: {
  title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const id = ++modalSeq;
    openModals.push(id);
    const h = (e: KeyboardEvent) => { if (e.key === "Escape" && openModals[openModals.length - 1] === id) closeRef.current(); };
    window.addEventListener("keydown", h);
    return () => { window.removeEventListener("keydown", h); openModals.splice(openModals.indexOf(id), 1); };
  }, []);
  // body로 그려서 사이드바(sticky) 등 부모의 쌓임 순서에 가려지지 않게 한다
  return createPortal(
    <div className="modal-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose} aria-label="닫기" title="닫기"><X size={16} /></button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

export function Confirm({ title, message, confirmLabel, danger, onConfirm, onClose }: {
  title: string; message: ReactNode; confirmLabel: string; danger?: boolean; onConfirm: () => void; onClose: () => void;
}) {
  return (
    <Modal title={title} onClose={onClose} footer={<>
      <button onClick={onClose}>취소</button>
      <button className={danger ? "danger" : "primary"} onClick={() => { onConfirm(); onClose(); }}>{confirmLabel}</button>
    </>}>
      <div className="small">{message}</div>
    </Modal>
  );
}
