import { useState, type ReactNode } from "react";
import { Modal } from "./Modal";
import { PinInput } from "./PinInput";

/** 옵션을 켜도 AI에 연결되지 않는 동안 쓰는 문구. 사용자·관리자 화면이 함께 쓴다. 연결이 생기면 여기와 쓰는 곳을 지운다 */
export const OPTION_PENDING_TEXT = "옵션 연결은 준비 중입니다. 지금은 선택만 저장되고 AI에게 전달되지 않습니다.";

/** 되돌리기 어렵거나 다른 관문을 무너뜨리는 결정은 PIN을 다시 받는다 */
export function PinModal({ title, confirm, danger, busy, disabled, children, onClose, onConfirm }: {
  title: string; confirm: string; danger?: boolean; busy?: boolean; disabled?: boolean; children?: ReactNode; onClose: () => void; onConfirm: (pin: string) => void;
}) {
  const [pin, setPin] = useState("");
  return (
    <Modal title={title} onClose={onClose} footer={<>
      <button onClick={onClose}>취소</button>
      <button className={danger ? "danger" : "primary"} disabled={pin.length !== 6 || busy || disabled} onClick={() => onConfirm(pin)}>{confirm}</button>
    </>}>
      {children && <div className="small market-modal-text">{children}</div>}
      <div className="field"><span className="lbl">내 PIN</span><PinInput value={pin} onChange={setPin} autoFocus label="내 PIN" /></div>
    </Modal>
  );
}
