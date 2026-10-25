import type { BankRecommendation as Recommendation } from "./realTypes";

export const BANK_RECOMMENDATION_PLACEHOLDER = "Для этой новости пока нет подходящей оценки банка";

export function BankRecommendation({ recommendation }: { recommendation?: Recommendation | null }) {
  const placeholder = !recommendation?.text.trim();
  return <section className="detail-block bank-recommendation" aria-label="Что говорят аналитики банка">
    <h3>Что говорят аналитики банка</h3>
    <blockquote><span className="badge badge-bank_position">Банк</span>{" "}
      <span className="bank-recommendation-text">{placeholder ? BANK_RECOMMENDATION_PLACEHOLDER : recommendation!.text}</span>
    </blockquote>
    {!placeholder && recommendation?.origin === "corpus" ? <>
      <p className="hint small">Позиция банка на дату материала. Применение к вашей компании в разборе — отдельный вывод AI.</p>
      {recommendation.match_reason ? <p className="hint small">Почему подобрано: {recommendation.match_reason}</p> : null}
      {recommendation.sources?.map((source) => <details key={source.passage_id} className="real-report-evidence">
        <summary>{source.title} · {source.published_at}</summary>
        <p>{source.text}</p>
      </details>)}
    </> : null}
  </section>;
}
