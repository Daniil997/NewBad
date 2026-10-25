import { useEffect, useState } from "react";
import { ChatPanel } from "./RealParts";
import { formatDateTime, type ChatSession } from "./realTypes";

export function ChatWorkspace({ activeId, ownerId, csrf, companyName, onSelect, onCreate, onBack, creating }: {
  activeId: string; ownerId: string; csrf: string; companyName: string;
  onSelect: (id: string) => void; onCreate: () => void; onBack: () => void; creating: boolean;
}) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [more, setMore] = useState(false);
  const [revision, setRevision] = useState(0);
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  async function load(offset = 0, signal?: AbortSignal) {
    try {
      const response = await fetch(`/api/real/chat/sessions?offset=${offset}`, { signal });
      if (!response.ok) throw new Error("Не удалось загрузить список диалогов");
      const data = await response.json();
      if (signal?.aborted) return;
      setSessions((current) => offset ? [...current, ...data.sessions.filter((row: ChatSession) => !current.some((old) => old.id === row.id))] : data.sessions);
      setMore(data.has_more); setError("");
      if (!activeId && data.sessions.length) onSelect(data.sessions[0].id);
    } catch (reason) {
      if (!signal?.aborted) setError(reason instanceof Error ? reason.message : "Не удалось загрузить диалоги");
    } finally { if (!signal?.aborted) setLoading(false); }
  }

  useEffect(() => {
    const controller = new AbortController();
    void load(0, controller.signal);
    return () => controller.abort();
  }, [revision, activeId]);

  useEffect(() => { setRenaming(false); setConfirmDelete(false); setDeleteError(""); }, [activeId]);

  async function deleteChat() {
    if (deleting || !activeId) return;
    const deletedId = activeId;
    setDeleting(true); setDeleteError("");
    try {
      const response = await fetch(`/api/real/chat/sessions/${encodeURIComponent(deletedId)}`, {
        method: "DELETE", headers: { "X-CSRF-Token": csrf },
      });
      if (response.status === 405) {
        throw new Error("Сервер ещё работает на предыдущей версии. Перезапустите API и повторите удаление.");
      }
      if (!response.ok && response.status !== 404) {
        const data = await response.json().catch(() => null);
        throw new Error(data?.detail || "Не удалось удалить чат. Попробуйте ещё раз.");
      }
      try { localStorage.removeItem(`business-chat-draft:${ownerId}:${deletedId}`); } catch { /* optional */ }
      const remaining = sessions.filter((row) => row.id !== deletedId);
      setSessions(remaining); setConfirmDelete(false);
      onSelect(remaining[0]?.id || "");
      setRevision((value) => value + 1);
    } catch (reason) { setDeleteError(reason instanceof Error ? reason.message : "Не удалось удалить чат"); }
    finally { setDeleting(false); }
  }

  async function rename() {
    if (!title.trim()) return;
    try {
      const response = await fetch(`/api/real/chat/sessions/${encodeURIComponent(activeId)}`, {
        method: "PATCH", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({ title: title.trim() }),
      });
      if (!response.ok) throw new Error("Не удалось изменить название");
      setRenaming(false); setRevision((value) => value + 1);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось изменить название"); }
  }

  const filtered = sessions.filter((row) => `${row.title} ${row.preview || ""}`.toLocaleLowerCase("ru").includes(query.toLocaleLowerCase("ru")));
  return <div className="chat-workspace">
    <div className="chat-workspace-toolbar">
      <button type="button" className="btn-link" onClick={onBack} disabled={deleting}>← К новостям</button>
      <button type="button" className="btn btn-primary small" onClick={onCreate} disabled={creating || deleting}>Новый чат</button>
    </div>
    <div className="chat-workspace-grid">
      <aside className="chat-sessions" aria-label="Сохранённые диалоги">
        <h2>Мои чаты</h2>
        <input className="input" type="search" aria-label="Поиск диалогов" placeholder="Найти диалог" value={query} onChange={(event) => setQuery(event.target.value)} />
        {loading ? <p role="status" className="hint">Загружаем диалоги…</p> : null}
        <div className="chat-session-list">
          {filtered.map((row) => <button type="button" key={row.id} disabled={deleting} className={`chat-session${row.id === activeId ? " active" : ""}`} aria-current={row.id === activeId ? "true" : undefined} onClick={() => onSelect(row.id)}>
            <span className="chat-session-kind">{row.focused_news_id ? "По новости" : "О бизнесе"}</span>
            <strong>{row.title}</strong>
            <span className="chat-session-preview">{row.preview || "Начните разговор"}</span>
            <small>{formatDateTime(row.updated_at)} · {row.message_count} сообщ.</small>
          </button>)}
        </div>
        {!loading && !filtered.length ? <p className="hint">{query ? "Диалоги не найдены" : "Здесь сохранятся ваши разговоры"}</p> : null}
        {more ? <button type="button" className="btn-link" onClick={() => void load(sessions.length)}>Показать ещё</button> : null}
        {error ? <div role="alert"><p className="inline-error">{error}</p><button type="button" className="btn-link" onClick={() => setRevision((value) => value + 1)}>Повторить</button></div> : null}
      </aside>
      <div className="chat-conversation">
        {activeId ? <>
          {renaming ? <form className="chat-rename" onSubmit={(event) => { event.preventDefault(); void rename(); }}>
            <input className="input" aria-label="Название диалога" value={title} maxLength={180} onChange={(event) => setTitle(event.target.value)} autoFocus />
            <button className="btn btn-ghost small" disabled={!title.trim()}>Сохранить</button>
            <button type="button" className="btn-link" onClick={() => setRenaming(false)}>Отмена</button>
          </form> : <div className="chat-conversation-actions">
            <button type="button" className="btn-link" disabled={deleting} onClick={() => { setTitle(sessions.find((row) => row.id === activeId)?.title || ""); setRenaming(true); setConfirmDelete(false); }}>Изменить название</button>
            <button type="button" className="btn-link" disabled={deleting} onClick={() => { setConfirmDelete(true); setDeleteError(""); }}>Удалить чат</button>
          </div>}
          {confirmDelete ? <div className="chat-delete-confirm" role="group" aria-label="Удаление чата">
            <p>Удалить этот чат и всю его историю? Восстановить переписку не получится.</p>
            <div className="chat-conversation-actions">
              <button type="button" className="btn btn-primary small" disabled={deleting} onClick={() => void deleteChat()}>{deleting ? "Удаляем…" : "Удалить навсегда"}</button>
              <button type="button" className="btn btn-ghost small" disabled={deleting} onClick={() => setConfirmDelete(false)}>Отмена</button>
            </div>
            {deleteError ? <p className="inline-error" role="alert">{deleteError}</p> : null}
          </div> : null}
          <fieldset className="chat-conversation-content" disabled={deleting}>
            <ChatPanel key={activeId} sessionId={activeId} ownerId={ownerId} csrf={csrf} companyName={companyName} titleOverride={sessions.find((row) => row.id === activeId)?.title} onUpdated={() => setRevision((value) => value + 1)} />
          </fieldset>
        </> : <div className="chat-empty"><h2>Обсудим ваш бизнес</h2><p>Откройте сохранённый диалог или начните новый. Можно сообщить условия работы и показатели прямо в чате.</p></div>}
      </div>
    </div>
  </div>;
}
