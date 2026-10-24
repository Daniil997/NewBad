"""Regression checks for CBR article cleanup and escaped RSS whitespace."""

from datetime import date

from app.ingest.news import NewsSource, _html_to_text, import_news


SOURCE = NewsSource(
    id="cbr_cleanup",
    name="Банк России",
    url="https://www.cbr.ru/rss/RssPress",
    article_mode="rss_only",
    min_interval_s=0,
)


def test_plain_rss_text_removes_literal_escape_sequences_and_footer():
    raw = (
        r"Банк России опубликовал уточнения по срочной версии RUONIA.\n \n"
        r"Уточнения вступают в силу после публикации.\n"
        r"Ответственное структурное подразделение: Департамент статистики\n"
        r"Страница была полезной?"
    )
    cleaned = _html_to_text(raw)
    assert "срочной версии RUONIA" in cleaned
    assert "Уточнения вступают" in cleaned
    assert r"\n" not in cleaned
    assert "Ответственное структурное подразделение" not in cleaned
    assert "Департамент статистики" not in cleaned
    assert "Страница была полезной" not in cleaned


def test_real_article_is_kept_even_with_site_feedback_controls():
    article = " ".join(
        ["Банк России сообщил сведения о денежно-кредитных условиях и ожиданиях участников рынка."] * 7
    )
    page = (
        "<html><body><article class='article-body'><p>"
        + article
        + "</p></article>"
        + "<div>Страница была полезной?</div>"
        + "<div>Последнее обновление страницы: 24.10.2026</div>"
        + "</body></html>"
    )
    text = _html_to_text(page)
    assert "денежно-кредитных условиях" in text
    assert "Последнее обновление" not in text
    assert "Страница была полезной" not in text
    assert r"\n" not in text


def test_feed_import_does_not_persist_site_chrome(tmp_path):
    description = (
        "Банк России опубликовал сообщение о финансовом рынке. "
        + r"\n Ответственное структурное подразделение: Департамент статистики"
        + r"\n Страница была полезной?"
    )
    rss = (
        "<rss><channel><item>"
        "<title>Важное сообщение регулятора о финансовом рынке</title>"
        "<link>https://www.cbr.ru/press/event/?id=cleanup</link>"
        "<pubDate>Sat, 24 Oct 2026 12:00:00 +0300</pubDate>"
        "<description>" + description + "</description>"
        "</item></channel></rss>"
    )
    result = import_news(
        tmp_path,
        source=SOURCE,
        fetch=lambda: rss,
        loaded_on=date(2026, 10, 24),
        min_interval_s=0,
    )
    assert result.status == "imported"
    assert len(result.items) == 1
    assert result.items[0].text == "Банк России опубликовал сообщение о финансовом рынке."
    assert "Департамент статистики" not in result.items[0].text
