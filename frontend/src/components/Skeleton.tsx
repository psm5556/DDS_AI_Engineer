/** 불러오는 동안의 뼈대 화면. 빨리 끝나면 깜빡이지 않게 잠깐(--dur-wait) 뒤에 나타난다 — 지연은 CSS가 맡는다. */
export function Skeleton({ rows = 3, page }: { rows?: number; page?: boolean }) {
  const body = (
    <div className="skeleton" role="status" aria-label="불러오는 중">
      {Array.from({ length: rows }, (_, i) => <i key={i} />)}
    </div>
  );
  return page ? <div className="page">{body}</div> : body;
}
