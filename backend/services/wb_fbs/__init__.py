# ruff: noqa: RUF001, RUF002, RUF003
"""
Сервисы WB FBS (Marketplace API): склады продавца, остатки, задания, поставки.

Модули домена:
- `client_factory` — ключ `wb_marketplace` из `IntegrationKey` → готовый `WbFbsClient`;
- `warehouse_service` — склады WB и их привязка к нашим складам;
- `stock_service` — расчёт и передача FBS-остатков;
- `orders_service`, `supplies_service` — сборочные задания и поставки;
- `locks` — распределённый лок трансляции (кнопка ‖ джоб);
- `contour` — метка контура в зеркале заданий (песочница не трогает боевые данные).

Реэкспорт здесь намеренно не делается: домен подтягивают точечно
(`from backend.services.wb_fbs.client_factory import get_fbs_client`), чтобы
импорт пакета не тащил всю доменную цепочку в scheduler-воркер.
"""
