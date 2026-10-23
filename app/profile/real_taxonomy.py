"""Initial controlled vocabulary for manually entered real-mode profiles."""

REAL_PROFILE_TAXONOMY = {
    "industry_tags": [
        {"id": "trade", "label": "Торговля", "description": "Оптовая или розничная продажа товаров."},
        {"id": "manufacturing", "label": "Производство", "description": "Выпуск продукции или промышленная переработка."},
        {"id": "construction", "label": "Строительство", "description": "Строительные, монтажные и ремонтные работы."},
        {"id": "transport_logistics", "label": "Транспорт и логистика", "description": "Перевозки, складирование и цепочки поставок."},
        {"id": "agriculture", "label": "Сельское хозяйство", "description": "Сельское хозяйство и связанные производства."},
        {"id": "it_communications", "label": "ИТ и связь", "description": "Разработка ПО, телекоммуникации и цифровые сервисы."},
        {"id": "business_services", "label": "Услуги для бизнеса", "description": "Профессиональные и корпоративные услуги."},
        {"id": "consumer_services", "label": "Услуги для потребителей", "description": "Услуги частным клиентам и населению."},
        {"id": "real_estate", "label": "Недвижимость", "description": "Операции с недвижимостью и управление объектами."},
        {"id": "energy_raw_materials", "label": "Энергетика и сырьё", "description": "Энергетические и сырьевые рынки или производство."},
        {"id": "health_education", "label": "Здравоохранение и образование", "description": "Медицинские, образовательные и социальные услуги."},
        {"id": "hospitality_tourism", "label": "Гостиницы и туризм", "description": "Размещение, общественное питание и туристические услуги."},
        {"id": "other", "label": "Другая отрасль", "description": "Укажите подробности в поле дополнительного контекста."},
    ],
    "focus_topics": [
        {"id": "rates_inflation", "label": "Ставки и инфляция", "description": "Ключевая ставка, стоимость денег и изменение цен."},
        {"id": "exchange_rates", "label": "Курсы валют", "description": "Курсовые изменения и валютное регулирование."},
        {"id": "commodity_prices", "label": "Сырьё и энергоресурсы", "description": "Цены на сырьё, металлы, энергию и топливо."},
        {"id": "tax_regulation", "label": "Налоги и регулирование", "description": "Налоговые изменения, требования и госполитика."},
        {"id": "logistics", "label": "Логистика и поставки", "description": "Транспортные маршруты, сроки и доступность поставок."},
        {"id": "international_trade", "label": "Внешняя торговля", "description": "Экспорт, импорт и международные рынки."},
        {"id": "labor_market", "label": "Рынок труда", "description": "Кадры, зарплаты и доступность специалистов."},
        {"id": "business_support", "label": "Поддержка бизнеса", "description": "Субсидии, льготные программы и госзакупки."},
        {"id": "consumer_demand", "label": "Спрос и потребление", "description": "Покупательский спрос и поведение клиентов."},
        {"id": "technology_security", "label": "Технологии и кибербезопасность", "description": "Цифровизация, автоматизация и цифровые риски."},
    ],
}
