import type { BankRecommendation as Recommendation } from "./realTypes";

export const BANK_RECOMMENDATION_PLACEHOLDER = "Здесь будут рекомендации от Альфа-Бизнеса";

export function BankRecommendation({ recommendation }: { recommendation?: Recommendation | null }) {
  const placeholder = !recommendation?.text.trim();
  return <section className="detail-block bank-recommendation" aria-label="Что говорят аналитики банка">
    <h3>Что говорят аналитики банка</h3>
    <blockquote><span className="badge badge-bank_position">Банк</span>{" "}
      <span className="bank-recommendation-text">{placeholder ? BANK_RECOMMENDATION_PLACEHOLDER : recommendation!.text}</span>
      {!placeholder && recommendation?.page ? <span className="meta"> стр. {recommendation.page}</span> : null}
    </blockquote>
    {placeholder ? <p className="hint small">Заглушка: рекомендация по этой новости пока не добавлена.</p> : null}
  </section>;
}
