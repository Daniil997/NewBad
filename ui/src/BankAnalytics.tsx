import { useEffect, useRef, useState } from "react";
import "./bankAnalytics.css";

type Document = { id: string; title: string; published_at: string; period: string; notes: string; topic: string; type: string; edited: number; revision: number };
type Passage = { id: string; text: string; title: string; document_id: string; published_at: string; reviewed: number; stale: boolean };
type Corpus = { documents: number; items: Document[]; matches: Passage[] };
type Detail = Document & { pages: { id: string; text: string }[]; reviewed_passages: { id: string; text: string }[] };
const TOPICS: Record<string, string> = {
  key_rate: "Ключевая ставка", inflation: "Инфляция", macro: "Макроэкономика", strategy: "Рынки и стратегия",
  bonds: "Облигации", consumption: "Потребление и рынок труда", gold: "Драгоценные металлы", metals: "Металлы",
};
const EMPTY_DRAFT = { title: "", published_at: "", period: "", notes: "", text: "", topic: "auto", reviewed_text: "" };

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (response.ok && response.status === 204) return undefined as T;
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) throw new Error(typeof data?.detail === "string" ? data.detail : "Не удалось выполнить запрос. Повторите попытку.");
  return data as T;
}

function BookIcon() {
  return <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Zm0 0v15" /></svg>;
}

export function BankAnalytics({ onDirtyChange, onBusyChange }: {
  onDirtyChange: (value: boolean) => void; onBusyChange: (value: boolean) => void;
}) {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [corpus, setCorpus] = useState<Corpus | null>(null);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editId, setEditId] = useState("");
  const [editRevision, setEditRevision] = useState(0);
  const [editingSource, setEditingSource] = useState(false);
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const [baseline, setBaseline] = useState(EMPTY_DRAFT);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const submitting = useRef(false);
  const titleInput = useRef<HTMLInputElement>(null);
  const reader = useRef<HTMLElement>(null);
  const dirty = editing && JSON.stringify(draft) !== JSON.stringify(baseline);

  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { onBusyChange(busy); }, [busy, onBusyChange]);
  useEffect(() => () => { onDirtyChange(false); onBusyChange(false); }, [onDirtyChange, onBusyChange]);
  useEffect(() => { if (editing) titleInput.current?.focus(); }, [editing]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    void request<Corpus>(`/api/bank/analytics?q=${encodeURIComponent(search)}`, { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return;
        setCorpus(data); setSelected((current) => data.items.some((item) => item.id === current) ? current : data.items[0]?.id || "");
      })
      .catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [search, revision]);
  useEffect(() => {
    setDetail(null);
    if (!selected) { setDetailLoading(false); return; }
    const controller = new AbortController();
    setDetailLoading(true);
    void request<Detail>(`/api/bank/analytics/${encodeURIComponent(selected)}`, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setDetail(data); })
      .catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selected, revision]);

  function leaveEditor() {
    if (busy || (dirty && !window.confirm("Есть несохранённый материал. Закрыть без сохранения?"))) return false;
    setEditing(false); setEditId(""); setDraft(EMPTY_DRAFT); setBaseline(EMPTY_DRAFT); setFormError("");
    return true;
  }
  function editMaterial() {
    if (!detail || busy) return;
    const values = {
      title: detail.title, published_at: detail.published_at, period: detail.period, notes: detail.notes,
      text: detail.pages.map((page) => page.text).join("\n\n"), topic: TOPICS[detail.topic] ? detail.topic : "auto",
      reviewed_text: detail.type === "manual" ? "" : detail.reviewed_passages.map((item) => item.text).join("\n\n"),
    };
    setEditId(detail.id); setEditRevision(detail.revision); setEditingSource(detail.type !== "manual"); setDraft(values); setBaseline(values);
    setEditing(true); setFormError(""); setNotice("");
  }
  async function removeMaterial() {
    if (!detail || submitting.current) return;
    if (!window.confirm(`Удалить материал «${detail.title}» из общей библиотеки? Он больше не будет использоваться в новых подборках для всех профилей.`)) return;
    submitting.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const csrf = await request<{ csrf_token: string }>("/api/auth/csrf");
      await request<void>(`/api/bank/analytics/${encodeURIComponent(detail.id)}?expected_revision=${detail.revision}`, {
        method: "DELETE", headers: { "X-CSRF-Token": csrf.csrf_token },
      });
      setCorpus((current) => current ? { ...current, documents: current.documents - 1, items: current.items.filter((item) => item.id !== detail.id), matches: current.matches.filter((item) => item.document_id !== detail.id) } : current);
      setSelected(""); setDetail(null); setRevision((value) => value + 1);
      setNotice("Материал удалён из библиотеки и больше не участвует в подборе аналитики. Сохранённые разборы сохраняют историю своих источников.");
    } catch (reason) { setError((reason as Error).message); }
    finally { submitting.current = false; setBusy(false); }
  }
  function select(id: string) {
    if (!leaveEditor()) return;
    setSelected(id); setError(""); setNotice("");
    if (window.matchMedia("(max-width: 1050px)").matches) reader.current?.scrollIntoView({ block: "start" });
  }
  async function save() {
    if (submitting.current) return;
    if (!draft.title.trim() || draft.text.trim().length < 40) {
      setFormError("Укажите название и добавьте текст аналитики длиной от 40 символов."); return;
    }
    submitting.current = true; setBusy(true); setFormError(""); setNotice("");
    try {
      const csrf = await request<{ csrf_token: string }>("/api/auth/csrf");
      const saved = await request<{ id: string }>(editId ? `/api/bank/analytics/${encodeURIComponent(editId)}` : "/api/bank/analytics", {
        method: editId ? "PUT" : "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf.csrf_token },
        body: JSON.stringify({ ...draft, ...(editId ? { expected_revision: editRevision } : {}) }),
      });
      setSelected(saved.id); setDraft(EMPTY_DRAFT); setBaseline(EMPTY_DRAFT); setEditing(false); setEditId(""); setQuery(""); setSearch("");
      setRevision((value) => value + 1);
      setNotice(`${editId ? "Изменения сохранены" : "Материал добавлен в общую библиотеку"}. При следующем открытии ленты аналитика будет подобрана с учётом актуальной версии материала.`);
    } catch (reason) { setFormError((reason as Error).message); }
    finally { submitting.current = false; setBusy(false); }
  }

  return <div className="bank-library">
    <header className="bank-library-intro">
      <div className="bank-library-intro-copy"><span className="bank-library-icon"><BookIcon /></span><div>
        <h3>Библиотека материалов <span className="bank-count">{corpus?.documents ?? "…"}</span></h3>
        <p>Позиция банка — в контексте событий и вашей компании.</p>
      </div></div>
      <button type="button" className="dev-button" disabled={editing || busy} onClick={() => { setEditing(true); setEditId(""); setDraft(EMPTY_DRAFT); setBaseline(EMPTY_DRAFT); setNotice(""); setFormError(""); }}><span aria-hidden="true">＋</span>Добавить материал</button>
    </header>
    {error ? <div className="dev-alert is-error" role="alert">{error}<button type="button" className="dev-button is-secondary" onClick={() => setRevision((value) => value + 1)}>Повторить загрузку</button></div> : null}
    {notice ? <p className="dev-alert is-success" role="status">{notice}</p> : null}
    <section className="dev-card bank-search">
      <form className="bank-search-form" onSubmit={(event) => { event.preventDefault(); setSearch(query.trim()); setRevision((value) => value + 1); }}>
        <label>Поиск по содержанию<input type="search" maxLength={2000} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Ключевая ставка, инфляция, спрос…" /></label>
        <button type="submit" className="dev-button is-secondary" disabled={loading}>{loading ? "Ищем…" : "Найти"}</button>
      </form>
      {search && !loading && corpus ? <div className="bank-search-results">
        <div className="bank-search-heading"><p role="status">Найдено фрагментов: {corpus.matches.length}</p><button type="button" className="dev-text-button" onClick={() => { setSearch(""); setQuery(""); }}>Сбросить поиск</button></div>
        {!corpus.matches.length ? <p className="dev-description">Попробуйте другую тему или добавьте новый материал.</p> : null}
        {corpus.matches.map((match) => <details key={match.id}>
          <summary>{match.title}<small>{match.published_at}{match.stale ? " · Исторический материал" : ""}</small></summary>
          {!match.reviewed ? <p className="dev-description">Распознанный фрагмент. Автоматический анализ использует отдельно проверенные оценки.</p> : null}
          <p className="bank-passage">{match.text}</p><button type="button" className="dev-text-button" disabled={busy} onClick={() => select(match.document_id)}>Открыть материал →</button>
        </details>)}
      </div> : null}
    </section>
    <div className="bank-library-grid">
      <section className="dev-card bank-materials" aria-label="Список материалов">
        <div className="bank-materials-heading"><h3>Все материалы</h3><span>По дате публикации</span></div>
        {loading && !corpus ? <p className="dev-description" role="status">Загружаем библиотеку…</p> : null}
        {corpus && !corpus.items.length ? <p className="dev-description">Библиотека пока пуста. Добавьте первый материал.</p> : null}
        <ul>{corpus?.items.map((item) => <li key={item.id}><button type="button" disabled={busy} aria-current={!editing && selected === item.id ? "true" : undefined} onClick={() => select(item.id)}>
          <span className="bank-material-topic">{TOPICS[item.topic] || "Аналитика"}{item.type === "manual" ? " · Вручную" : ""}</span>
          <strong>{item.title}</strong><time dateTime={item.published_at}>{item.published_at}</time>
        </button></li>)}</ul>
      </section>
      <section className="dev-card bank-reader" ref={reader} aria-label={editing ? editId ? "Редактирование материала" : "Добавление материала" : "Содержание материала"} aria-busy={busy || detailLoading}>
        {editing ? <form onSubmit={(event) => { event.preventDefault(); void save(); }}>
          <div className="bank-reader-heading"><span className="bank-kicker">{editId ? "Обновляем библиотеку" : "Расширяем библиотеку"}</span><h3>{editId ? "Редактирование материала" : "Новый материал"}</h3><p className="dev-description">{editId ? "Изменения применяются к общей библиотеке и учитываются при следующем подборе аналитики для компаний." : "Вставьте текст аналитики банка. Материал будет доступен всем профилям, а его применение будет зависеть от контекста компании."}</p></div>
          <fieldset className="bank-form-fields" disabled={busy}>
            <label>Название материала<input ref={titleInput} required maxLength={240} value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} placeholder="Инфляция, 25–31 августа" /></label>
            <div className="bank-form-row">
              <label>Дата публикации<input type={editId && baseline.published_at.length === 7 ? "month" : "date"} required value={draft.published_at} onChange={(event) => setDraft({ ...draft, published_at: event.target.value })} /></label>
              <label>Период<input maxLength={120} value={draft.period} onChange={(event) => setDraft({ ...draft, period: event.target.value })} placeholder="2026-08-25/2026-08-31" /></label>
            </div>
            <div className="bank-text-field"><label htmlFor="bank-material-text">Текст аналитики</label><textarea id="bank-material-text" aria-describedby="bank-material-text-help" required minLength={40} maxLength={100000} rows={10} value={draft.text} onChange={(event) => setDraft({ ...draft, text: event.target.value })} placeholder="Вставьте содержание материала: оценки, выводы и аргументы аналитиков банка…" /><small id="bank-material-text-help">{editId && editingSource ? "Полный текст материала. Для применения в выводах используются оценки из поля ниже." : "От 40 символов. Текст сохраняется целиком и используется как позиция банка."}</small></div>
            {editId && editingSource ? <div className="bank-text-field"><label htmlFor="bank-material-reviewed">Оценки для применения</label><textarea id="bank-material-reviewed" aria-describedby="bank-material-reviewed-help" maxLength={100000} rows={7} value={draft.reviewed_text} onChange={(event) => setDraft({ ...draft, reviewed_text: event.target.value })} /><small id="bank-material-reviewed-help">Проверьте эти фрагменты после изменения текста. Только они используются как позиция банка при анализе новостей. Пустое поле исключает материал из автоматических выводов.</small></div> : null}
            <details className="bank-extra-fields"><summary>Тема и примечание <span>Необязательно</span></summary><div>
              <label>Тема материала<select value={draft.topic} onChange={(event) => setDraft({ ...draft, topic: event.target.value })}><option value="auto">Определить по тексту</option>{Object.entries(TOPICS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select><small>Уточните тему, если материал затрагивает сразу несколько вопросов.</small></label>
              <div className="bank-text-field"><label htmlFor="bank-material-notes">Примечание</label><textarea id="bank-material-notes" maxLength={2000} rows={3} value={draft.notes} onChange={(event) => setDraft({ ...draft, notes: event.target.value })} placeholder="Например: оценки относятся к неделе 25–31 августа." /></div>
            </div></details>
          </fieldset>
          {formError ? <p className="dev-alert is-error" role="alert">{formError}</p> : null}
          <div className="bank-form-actions"><button type="submit" className="dev-button" disabled={busy || (Boolean(editId) && !dirty)}>{busy ? "Сохраняем…" : editId ? "Сохранить изменения" : "Сохранить материал"}</button><button type="button" className="dev-button is-secondary" disabled={busy} onClick={leaveEditor}>Отмена</button></div>
        </form> : detail ? <>
          <div className="bank-reader-heading"><div className="bank-reader-tags"><span className="bank-kicker">{TOPICS[detail.topic] || "Аналитика банка"}</span>{detail.edited ? <span className="bank-manual-badge">Изменено вручную</span> : detail.type === "manual" ? <span className="bank-manual-badge">Добавлено вручную</span> : null}</div><h3>{detail.title}</h3>
            <dl className="bank-metadata"><div><dt>Публикация</dt><dd><time dateTime={detail.published_at}>{detail.published_at}</time></dd></div>{detail.period && detail.period !== "unknown" ? <div><dt>Период</dt><dd>{detail.period}</dd></div> : null}</dl>
          </div>
          <div className="bank-material-actions"><button type="button" className="dev-button is-secondary" disabled={busy || detailLoading} onClick={editMaterial}>Редактировать</button><button type="button" className="dev-button is-danger" disabled={busy || detailLoading} onClick={() => void removeMaterial()}>{busy ? "Удаляем…" : "Удалить"}</button></div>
          {detail.notes ? <aside className="bank-note">{detail.notes}</aside> : null}
          {detail.type === "manual" ? <div className="bank-full-text"><h4>Текст материала</h4>{detail.pages.map((page) => <p className="bank-passage" key={page.id}>{page.text}</p>)}</div> : <>
            <h4>Проверенные оценки банка</h4>{detail.reviewed_passages.map((item) => <blockquote className="bank-quote" key={item.id}>{item.text}</blockquote>)}
            {!detail.reviewed_passages.length ? <p className="dev-description">Проверенные оценки пока не выделены. Для чтения доступен полный текст ниже.</p> : null}
            <details className="bank-full-text"><summary>Полный распознанный текст</summary><p className="dev-description">В таблицах и графиках возможны ошибки распознавания.</p>{detail.pages.map((page) => <p className="bank-passage" key={page.id}>{page.text}</p>)}</details>
          </>}
          <p className="bank-reader-footnote">Оценки относятся к дате публикации. Применение к компании и выводы AI показываются в карточках новостей.</p>
        </> : <div className="bank-reader-empty"><BookIcon /><h3>{detailLoading ? "Загружаем материал…" : "Выберите материал"}</h3><p>Здесь появятся содержание, оценки банка и сведения о публикации.</p></div>}
      </section>
    </div>
  </div>;
}
