'use client';

import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';

// ─── Types ──────────────────────────────────────────────────────────────────

export type Lang = 'ru' | 'zh';

type TranslationDict = Record<string, string>;

// ─── Translations ───────────────────────────────────────────────────────────

const ru: TranslationDict = {
    // ── Page header & tabs ──
    page_title: 'Цепочка поставок',
    page_subtitle: 'Фабричные заказы, машины, обзор',
    tab_orders: '📦 Фабричные заказы',
    tab_vehicles: '🚛 Машины',
    tab_suppliers: '📊 Обзор',

    // ── Shared filter ──
    filter_all: 'Все',
    vehicles_filter_empty: 'Нет машин с выбранными статусами',

    // ── Shared status labels ──
    status_forming: 'Формируется',
    status_shipped: 'Отгружен',
    status_customs: 'Таможня',
    status_dispatched: 'Отправлена',
    status_delivered: 'Принята',

    factory_status_forming: 'Формируется',
    factory_status_distributed: 'Распределён',
    factory_status_closed: 'Закрыт',

    // ── Container & transport ──
    container_truck1: 'Авто 13.5м',
    container_truck2: 'Авто 13.6м',
    container_20ft: '20 фут',
    container_40ft: '40 фут',
    container_40ft_hc: '40 фут HC',
    container_gazelle: 'Газель',
    transport_auto: 'АВТО',
    transport_container: 'КОНТЕЙНЕР',

    // ── Country ──
    country_china: '🇨🇳 Китай',
    country_russia: '🇷🇺 Россия',
    vehicle_country: 'Страна поставщика',

    // ── Status transitions ──
    transition_to_customs: 'На таможню',
    transition_dispatched: 'Отправлена',
    transition_deliver: 'Принять',
    btn_ship: 'Отгрузить',

    // ── Common buttons ──
    btn_create: 'Создать',
    btn_save: 'Сохранить',
    btn_cancel: 'Отмена',
    btn_delete: 'Удалить',
    btn_edit: 'Ред.',
    btn_del: 'Уд.',
    btn_retry: 'Повторить',
    btn_refresh: 'Обновить',
    btn_close: '✕ Закрыть',
    btn_add: 'Добавить',

    // ── Common messages ──
    msg_error: 'Ошибка',
    msg_loading: 'Загрузка...',
    msg_loading_error: 'Ошибка загрузки',
    msg_saving: 'Сохранение...',
    msg_creating: 'Создаём...',
    msg_save_error: 'Ошибка сохранения',
    msg_delete_error: 'Ошибка удаления',
    msg_no_data: 'Нет данных',

    // ── Common units ──
    unit_pcs: 'шт',
    unit_kg: 'кг',
    unit_m3: 'м³',
    unit_days: 'дн.',
    unit_from: 'от',
    unit_to: 'до',

    // ── Common columns ──
    col_number: 'Номер',
    col_supplier: 'Поставщик',
    col_items_count: 'Позиций',
    col_qty: 'Кол-во',
    col_boxes: 'Мест',
    col_volume_m3: 'Объём м³',
    col_weight_kg: 'Вес, кг',
    col_amount: 'Стоимость',
    col_status: 'Статус',
    col_progress: 'Прогресс',
    col_ready_date: 'Готовность',
    col_created_date: 'Создан',
    col_barcode: 'Баркод',
    col_article: 'Артикул',
    col_category: 'Категория',
    col_price_cny: 'Цена ¥',
    col_sum_cny: 'Сумма ¥',
    col_price: 'Цена',
    col_sum: 'Сумма',
    col_box_spec: 'Коробка (см)',
    col_pcs_per_box: 'Шт/кор',
    col_weight_1pc: 'Вес (кг)',
    col_allocation: 'Распределение',
    col_actions: 'Действия',
    col_order: 'Заказ',
    col_container: 'Контейнер',
    col_items: 'Позиции',
    col_invoice: 'Инвойс',
    col_weight: 'Вес',
    col_cost: 'Стоимость',
    col_shipped_date: 'Отправлена',
    col_arrival_date: 'Прибытие',
    col_available: 'Доступно',
    col_total: 'Всего',
    col_distributed: 'Разбито',
    col_remaining: 'Остаток',
    col_vehicle_no: 'Машина (номер)',
    col_from_order: 'Из заказа',
    col_name: 'Название',
    col_country: 'Страна',
    col_currency: 'Валюта',
    col_delivery_terms: 'Сроки доставки',
    col_note: 'Примечание',

    // ── KPI ──
    kpi_orders: 'Заказов',
    kpi_total_weight: 'Общий вес',
    kpi_total_volume: 'Общий объём',
    kpi_total_amount: 'Сумма заказов',
    kpi_factory_orders: 'Фабричные заказы',
    kpi_vehicles: 'Машины',
    kpi_items: 'Позиции',
    kpi_sum_cny: 'Сумма (CNY)',
    kpi_suppliers_total: 'Всего поставщиков',
    kpi_china: 'Китай',
    kpi_russia: 'Россия',

    // ── Factory orders tab ──
    orders_title: 'Фабричные заказы',
    orders_empty: 'Нет фабричных заказов',
    orders_filter_all: 'Все поставщики',
    orders_btn_create: '+ Создать заказ',
    orders_confirm_delete: 'Удалить заказ?',
    orders_edit_title: 'Редактировать заказ',
    orders_excel_filename: 'Фабричные_заказы',
    orders_col_distributed: 'Распределено',
    orders_all_allocated: '✓ все',
    orders_remaining: '→ ост.',

    // ── Create order form ──
    create_order_title: 'Новый фабричный заказ',
    create_order_number: 'Номер заказа *',
    create_order_number_placeholder: 'ЗАК-001',
    create_order_supplier_select: 'Поставщик из справочника',
    create_order_supplier_name: 'Поставщик / Фабрика',
    create_order_supplier_placeholder: 'Название',
    create_order_ready_date: 'Дата готовности (план)',
    create_order_note: 'Заметка',
    create_order_note_placeholder: 'Комментарий',
    create_order_unselected: '-- Не выбран --',

    // ── Split to vehicles modal ──
    split_title: 'Разбить на машины',
    split_empty: 'Нет позиций для разбивки. Добавьте позиции в заказ.',
    split_validation: 'Укажите хотя бы одну позицию для разбивки',
    split_btn: 'Разбить',

    // ── Order items section ──
    items_title: 'Позиции заказа',
    items_collapse: 'Свернуть',
    items_add: '+ Добавить позиции',
    items_paste_hint: 'или вставьте данные из Excel (Ctrl+V)',
    items_paste_header: 'Добавить позиции',
    items_paste_sub: 'вставьте из Excel: Баркод, Кол-во, Цена, Коробка, Шт/кор, Вес',
    items_save_count: 'Сохранить',

    // ── Vehicles tab ──
    vehicles_title: 'Машины',
    vehicles_empty: 'Нет машин. Создайте первую!',
    vehicles_btn_create: '+ Новая машина',
    vehicles_open: 'Открыть →',
    vehicles_confirm_status: 'Сменить статус машины',

    // ── Create vehicle form ──
    vehicle_form_title: 'Новая машина',
    vehicle_form_number: 'Номер машины *',
    vehicle_form_number_placeholder: 'М-001',
    vehicle_form_container_type: 'Тип контейнера',
    vehicle_form_pickup_date: 'Дата забора (план)',
    vehicle_form_invoice: 'Инвойс',
    vehicle_form_order_no: 'Номер заказа',
    vehicle_form_delivery_cny: 'Перевозка ¥',
    vehicle_form_delivery_usd: 'Перевозка $',
    vehicle_form_delivery_rub: 'Перевозка ₽',
    vehicle_form_delivery_rub_hint: '0 = бесплатная доставка',
    vehicle_form_rate_cny: 'Курс ¥/₽',
    vehicle_form_rate_usd: 'Курс $/₽',
    vehicle_form_rate_eur: 'Курс €/₽',
    vehicle_form_warehouse: 'Склад назначения',
    vehicle_form_note: 'Заметка',

    // ── Vehicle items ──
    vehicle_items_empty: 'Нет позиций в машине',
    vehicle_items_confirm_delete: 'Удалить позицию из машины?',
    vehicle_items_remove: 'Удалить из машины',
    vehicle_items_clear_all: 'Удалить все',
    vehicle_items_confirm_clear_all: 'Удалить ВСЕ позиции из машины? Это действие нельзя отменить.',
    vehicle_items_recalc: '🔄 Пересчитать себестоимость',
    vehicle_items_recalcing: 'Пересчёт...',
    vehicle_items_recalc_error: 'Ошибка пересчёта',
    price_resync_btn: '🔄 Пересинхр. цены',
    price_resync_title: 'Пересинхронизация цен из заказов',
    price_resync_loading: 'Загрузка...',
    price_resync_empty: 'Все цены уже совпадают с заказами',
    price_resync_warning_status: 'Машина уже отправлена — пересчёт изменит себестоимость и БДР',
    price_resync_unlinked: 'позиций без привязки к заказу (не будут изменены)',
    price_resync_changes: 'Изменится позиций',
    price_resync_delta: 'Итоговое изменение',
    price_resync_apply: 'Применить',
    price_resync_applying: 'Применение...',
    price_resync_cancel: 'Отмена',
    price_resync_col_old: 'Старая ¥',
    price_resync_col_new: 'Новая ¥',
    price_resync_col_delta: 'Δ Сумма ¥',
    price_resync_applied: 'Обновлено позиций:',
    paste_in_order: 'В заказе',
    paste_missing_price: 'без цены — заполните цену для добавления',
    paste_price_mismatch: 'с отличающейся от заказа ценой',
    paste_mismatch_title: 'Цены отличаются от заказа',
    paste_mismatch_count: 'Расхождений',
    paste_col_order: 'В заказе ¥',
    paste_col_paste: 'Введено ¥',
    paste_mismatch_hint: 'Выберите: добавить с ценой из заказа (paste игнорируется) или переписать цены в заказе на введённые и добавить.',
    paste_keep_order: 'Использовать цены заказа',
    paste_overwrite_order: 'Переписать в заказе и добавить',

    // ── Vehicle cost summary ──
    cost_goods: 'Товар',
    cost_delivery: 'Доставка',
    cost_duty: 'Пошлина',
    cost_vat: 'НДС',
    cost_total: 'Итого',
    cost_container: 'Контейнер',
    cost_shipping: 'Перевозка',
    cost_weight: 'Вес',
    cost_arrival: 'Ожидаемое прибытие',
    cost_invoice: 'Инвойс',

    // ── Add items to vehicle modal ──
    add_items_title: 'Добавить товар в',
    add_items_subtitle: 'Выберите позиции из фабричных заказов',
    add_items_empty: 'Нет доступных позиций',
    add_items_pos: 'поз.',
    add_items_adding: 'Добавляем...',
    add_items_count: 'Добавить',

    // ── Overview tab ──
    overview_status_title: 'Машины по статусам',
    overview_no_status_data: 'Нет данных по статусам',

    // ── Suppliers tab ──
    suppliers_title: 'Поставщики',
    suppliers_empty: 'Нет поставщиков. Создайте первого!',
    suppliers_btn_create: '+ Создать поставщика',
    suppliers_confirm_delete: 'Удалить поставщика',
    suppliers_edit_title: 'Редактировать поставщика',
    suppliers_new_title: 'Новый поставщик',
    suppliers_form_name: 'Название *',
    suppliers_form_name_placeholder: 'Название компании',
    suppliers_form_country: 'Страна',
    suppliers_form_country_china: 'Китай',
    suppliers_form_country_russia: 'Россия',
    suppliers_form_currency: 'Валюта',
    suppliers_form_min_days: 'Срок мин. (дней)',
    suppliers_form_max_days: 'Срок макс. (дней)',
    suppliers_form_note: 'Примечание',
    suppliers_form_note_placeholder: 'Доп. информация о поставщике',

    // ── Supplier catalog ──
    catalog_back_to_list: '← Все поставщики',
    catalog_subtitle: 'Ассортимент за всё время',
    catalog_kpi_orders: 'Заказов',
    catalog_kpi_sku: 'Уникальных SKU',
    catalog_kpi_qty: 'Кол-во всего',
    catalog_kpi_amount: 'Сумма заказов',
    catalog_search_placeholder: '🔍 Поиск по баркоду, артикулу, предмету, бренду…',
    catalog_subject_all: 'Все предметы',
    catalog_brand_all: 'Все бренды',
    catalog_status_filter_all: 'Все товары',
    catalog_status_filter_not_distributed: 'Не распределено',
    catalog_status_filter_in_transit: 'В пути',
    catalog_status_filter_delivered: 'Отгружено',
    catalog_col_barcode: 'Баркод',
    catalog_col_brand: 'Бренд',
    catalog_col_article: 'Артикул',
    catalog_col_size: 'Размер',
    catalog_col_per_box: 'В кор.',
    catalog_col_weight: 'Вес, кг',
    catalog_col_total_qty: 'Всего, шт',
    catalog_col_last_price: 'Посл. цена',
    catalog_col_avg_price: 'Ср. цена',
    catalog_col_amount: 'Сумма',
    catalog_col_delivered: 'Отгружено',
    catalog_col_orders_count: 'Заказов',
    catalog_subject_empty: 'Без предмета',
    catalog_sku_unit: 'SKU',
    catalog_history_title: 'История заказов',
    catalog_history_col_order: 'Заказ',
    catalog_history_col_date: 'Дата',
    catalog_history_col_qty: 'Кол-во',
    catalog_history_col_price: 'Цена',
    catalog_history_col_amount: 'Сумма',
    catalog_history_col_vehicle: 'Машина',
    catalog_history_col_status: 'Статус',
    catalog_status_delivered: 'Доставлено',
    catalog_status_in_transit: 'В пути',
    catalog_status_at_factory: 'На фабрике',
    catalog_empty: 'У поставщика пока нет заказов',
    catalog_delivered_label: 'Отгружено',
    catalog_excel_filename: 'Ассортимент',

    // ── Factory order detail (factory-orders/[id]) ──
    detail_back: '← Назад к списку',
    detail_order: 'Заказ',
    detail_not_found: 'Заказ не найден',
    detail_badge_undistributed: 'Не распред.',
    detail_badge_distributed: 'Распределён',
    detail_tab_items: 'Позиции',
    detail_tab_history: 'История',
    detail_filter_all: 'Все',
    detail_filter_undistributed: 'Не распределено',
    detail_filter_distributed: 'Распределено',
    detail_history_empty: 'Нет записей',

    detail_event_created: 'Заказ создан',
    detail_event_status_change: 'Смена статуса',
    detail_event_distributed: 'Распределение',
    detail_event_items_added: 'Позиции добавлены',
    detail_event_items_removed: 'Позиции удалены',

    detail_field_order_number: 'Номер заказа',
    detail_field_supplier: 'Поставщик',
    detail_field_ready_date: 'Дата готовности',
    detail_field_note: 'Заметка',

    detail_kpi_items: 'Позиций',
    detail_kpi_qty: 'Кол-во',
    detail_kpi_boxes: 'Мест',
    detail_kpi_volume: 'Объём',
    detail_kpi_weight: 'Вес',
    detail_kpi_amount: 'Стоимость',
    detail_kpi_distribution: 'Распределение',

    detail_items_title: 'Позиции',
    detail_items_empty: 'Нет позиций',
    detail_confirm_delete_item: 'Удалить позицию?',
    detail_delete_selected: 'Удалить выбранные',
    detail_confirm_delete_selected: 'Удалить выбранные позиции ({count} шт)? Это действие нельзя отменить.',
    detail_delete_selected_error: 'Не удалось удалить {count} позиций (уже распределены по машинам)',
    detail_add_items: '+ Добавить',
    detail_paste_hint: 'или вставьте данные из Excel (Ctrl+V)',
    detail_paste_header: 'Добавить позиции',
    detail_paste_sub: 'вставьте из Excel: Баркод, Кол-во, Цена, Коробка, Шт/кор, Вес',
    detail_total: 'Итого',
    paste_specs_btn: 'Вставить габариты',
    paste_specs_header: 'Обновить габариты',
    paste_specs_sub: 'вставьте из буфера (Tab): Баркод, Коробка, Шт/кор, Вес',
    paste_specs_hint: 'Кликните сюда и вставьте данные из буфера (Ctrl+V)',
    paste_specs_apply: 'Применить',
    paste_specs_matched: 'Найден',
    paste_specs_not_found: 'Нет',
    paste_specs_pasted: 'Вставлено {total} строк. Найдено: {matched} из {total}',
    paste_specs_result: 'Обновлено {updated} позиций.',
    paste_specs_result_nf: 'Не найдено: {count} баркодов (пропущены).',
    paste_specs_found: 'Найдено',
    paste_specs_nf_label: 'Не найдено',

    detail_col_subject: 'Название',
    detail_col_mix: 'Микс',
    detail_mix_group: 'Микс-группа',

    // ── Vehicle detail (vehicles/[...orderNo]) ──
    vdetail_back: '← Назад к списку',
    vdetail_vehicle: 'Машина',
    vdetail_not_found: 'Машина не найдена',
    vdetail_confirm_delete: 'Удалить машину',
    vdetail_deleting: 'Удаление...',

    vdetail_tab_items: '📦 Позиции',
    vdetail_tab_cost: '💰 Себестоимость',
    vdetail_tab_docs: '📎 Документы',
    vdetail_tab_history: '📋 История',

    vdetail_doc_type_invoice: 'Инвойс',
    vdetail_doc_type_order: 'Заказ',
    vdetail_doc_type_customs: 'ДТ',
    vdetail_doc_type_contract: 'Контракт',
    vdetail_doc_type_other: 'Прочее',

    vdetail_docs_title: 'Документы',
    vdetail_docs_upload: 'Загрузить файл',
    vdetail_docs_empty: 'Нет документов',
    vdetail_docs_col_type: 'Тип',
    vdetail_docs_col_file: 'Файл',
    vdetail_docs_col_uploaded: 'Загружено',
    vdetail_docs_download: 'Скачать',
    vdetail_docs_upload_error: 'Ошибка загрузки файла',

    vdetail_history_title: 'История статусов',
    vdetail_history_empty: 'Нет записей',

    vdetail_items_add: '+ Добавить товар',
    vdetail_confirm_status: 'Сменить статус?',
    vdetail_ship_confirm: 'Отгрузить?',

    vdetail_field_container: 'Контейнер',
    vdetail_field_ship_date: 'Дата отгрузки',
    vdetail_field_arrival: 'Ожидаемое прибытие',
    vdetail_field_customs_no: 'Номер ДТ',
    vdetail_field_delivery_cost: 'Перевозка',

    // ── Shipment matrix ──
    matrix_mode_catalog: 'Каталог',
    matrix_mode_shipment: 'Отгрузочная карта',
    matrix_col_barcode: 'Баркод',
    matrix_col_article: 'Артикул',
    matrix_col_subject: 'Предмет',
    matrix_col_brand: 'Бренд',
    matrix_col_total_qty: 'Всего, шт',
    matrix_col_boxes: 'Коробок',
    matrix_col_remaining: 'Остаток',
    matrix_col_pct: 'Отгружено %',
    matrix_kpi_total: 'Всего к отгрузке',
    matrix_kpi_in_vehicles: 'В машинах',
    matrix_kpi_really_shipped: 'Уехало',
    matrix_kpi_shipped: 'Отгружено',
    matrix_kpi_remaining: 'Остаток',
    matrix_kpi_boxes: 'Коробок',
    matrix_filter_unshipped: 'Только неотгруженные',
    matrix_filter_search: 'Поиск по баркоду / артикулу',
    matrix_filter_subjects_all: 'Все предметы',
    matrix_filter_brands_all: 'Все бренды',
    matrix_filter_subjects_count: 'Предметов: {count}',
    matrix_filter_brands_count: 'Брендов: {count}',
    matrix_empty: 'Нет данных по отгрузкам',
    matrix_empty_filtered: 'Ничего не найдено по выбранным фильтрам',
    matrix_total_row: 'Итого',
    matrix_of_total: 'из {total}',
    matrix_click_vehicle_hint: 'Кликните, чтобы открыть машину',
};

const zh: TranslationDict = {
    // ── Page header & tabs ──
    page_title: '供应链',
    page_subtitle: '工厂订单、车辆、概览',
    tab_orders: '📦 工厂订单',
    tab_vehicles: '🚛 车辆',
    tab_suppliers: '📊 概览',

    // ── Shared filter ──
    filter_all: '全部',
    vehicles_filter_empty: '没有符合所选状态的车辆',

    // ── Shared status labels ──
    status_forming: '组装中',
    status_shipped: '已发货',
    status_customs: '海关',
    status_dispatched: '已派送',
    status_delivered: '已签收',

    factory_status_forming: '组装中',
    factory_status_distributed: '已分配',
    factory_status_closed: '已关闭',

    // ── Container & transport ──
    container_truck1: '卡车 13.5米',
    container_truck2: '卡车 13.6米',
    container_20ft: '20英尺',
    container_40ft: '40英尺',
    container_40ft_hc: '40英尺 HC',
    container_gazelle: '嘎斯车',
    transport_auto: '卡车',
    transport_container: '集装箱',

    // ── Country ──
    country_china: '🇨🇳 中国',
    country_russia: '🇷🇺 俄罗斯',
    vehicle_country: '供应商国家',

    // ── Status transitions ──
    transition_to_customs: '报关',
    transition_dispatched: '已派送',
    transition_deliver: '签收',
    btn_ship: '发货',

    // ── Common buttons ──
    btn_create: '创建',
    btn_save: '保存',
    btn_cancel: '取消',
    btn_delete: '删除',
    btn_edit: '编辑',
    btn_del: '删除',
    btn_retry: '重试',
    btn_refresh: '刷新',
    btn_close: '✕ 关闭',
    btn_add: '添加',

    // ── Common messages ──
    msg_error: '错误',
    msg_loading: '加载中...',
    msg_loading_error: '加载失败',
    msg_saving: '保存中...',
    msg_creating: '创建中...',
    msg_save_error: '保存失败',
    msg_delete_error: '删除失败',
    msg_no_data: '暂无数据',

    // ── Common units ──
    unit_pcs: '件',
    unit_kg: 'kg',
    unit_m3: 'm³',
    unit_days: '天',
    unit_from: '自',
    unit_to: '至',

    // ── Common columns ──
    col_number: '编号',
    col_supplier: '供应商',
    col_items_count: '品项数',
    col_qty: '数量',
    col_boxes: '箱数',
    col_volume_m3: '体积 m³',
    col_weight_kg: '重量, kg',
    col_amount: '金额',
    col_status: '状态',
    col_progress: '进度',
    col_ready_date: '完成日期',
    col_created_date: '创建日期',
    col_barcode: '条码',
    col_article: '货号',
    col_category: '分类',
    col_price_cny: '单价 ¥',
    col_sum_cny: '金额 ¥',
    col_price: '单价',
    col_sum: '金额',
    col_box_spec: '箱规 (cm)',
    col_pcs_per_box: '件/箱',
    col_weight_1pc: '重量 (kg)',
    col_allocation: '分配情况',
    col_actions: '操作',
    col_order: '订单',
    col_container: '集装箱',
    col_items: '品项',
    col_invoice: '发票号',
    col_weight: '重量',
    col_cost: '金额',
    col_shipped_date: '已派送',
    col_arrival_date: '到达日期',
    col_available: '可用',
    col_total: '总计',
    col_distributed: '已分配',
    col_remaining: '剩余',
    col_vehicle_no: '车辆（编号）',
    col_from_order: '来源订单',
    col_name: '名称',
    col_country: '国家',
    col_currency: '货币',
    col_delivery_terms: '交货期',
    col_note: '备注',

    // ── KPI ──
    kpi_orders: '订单数',
    kpi_total_weight: '总重量',
    kpi_total_volume: '总体积',
    kpi_total_amount: '订单总额',
    kpi_factory_orders: '工厂订单',
    kpi_vehicles: '车辆',
    kpi_items: '品项',
    kpi_sum_cny: '金额 (CNY)',
    kpi_suppliers_total: '供应商总数',
    kpi_china: '中国',
    kpi_russia: '俄罗斯',

    // ── Factory orders tab ──
    orders_title: '工厂订单',
    orders_empty: '暂无工厂订单',
    orders_filter_all: '全部供应商',
    orders_btn_create: '+ 新建订单',
    orders_confirm_delete: '确认删除订单？',
    orders_edit_title: '编辑订单',
    orders_excel_filename: '工厂订单',
    orders_col_distributed: '已分配',
    orders_all_allocated: '✓ 全部',
    orders_remaining: '→ 剩余',

    // ── Create order form ──
    create_order_title: '新建工厂订单',
    create_order_number: '订单编号 *',
    create_order_number_placeholder: 'ORD-001',
    create_order_supplier_select: '从目录选择供应商',
    create_order_supplier_name: '供应商 / 工厂',
    create_order_supplier_placeholder: '名称',
    create_order_ready_date: '预计完成日期',
    create_order_note: '备注',
    create_order_note_placeholder: '说明',
    create_order_unselected: '-- 未选择 --',

    // ── Split to vehicles modal ──
    split_title: '分配到车辆',
    split_empty: '暂无可分配的品项，请先添加订单品项。',
    split_validation: '请至少选择一个品项进行分配',
    split_btn: '分配',

    // ── Order items section ──
    items_title: '订单品项',
    items_collapse: '收起',
    items_add: '+ 添加品项',
    items_paste_hint: '或从Excel粘贴数据 (Ctrl+V)',
    items_paste_header: '添加品项',
    items_paste_sub: '从Excel粘贴: 条码、数量、单价、箱规、件/箱、重量',
    items_save_count: '保存',

    // ── Vehicles tab ──
    vehicles_title: '车辆',
    vehicles_empty: '暂无车辆，请创建！',
    vehicles_btn_create: '+ 新建车辆',
    vehicles_open: '查看 →',
    vehicles_confirm_status: '确认更改车辆状态',

    // ── Create vehicle form ──
    vehicle_form_title: '新建车辆',
    vehicle_form_number: '车辆编号 *',
    vehicle_form_number_placeholder: 'V-001',
    vehicle_form_container_type: '集装箱类型',
    vehicle_form_pickup_date: '计划提货日期',
    vehicle_form_invoice: '发票号',
    vehicle_form_order_no: '订单编号',
    vehicle_form_delivery_cny: '运费 ¥',
    vehicle_form_delivery_usd: '运费 $',
    vehicle_form_delivery_rub: '运费 ₽',
    vehicle_form_delivery_rub_hint: '0 = 免费送货',
    vehicle_form_rate_cny: '汇率 ¥/₽',
    vehicle_form_rate_usd: '汇率 $/₽',
    vehicle_form_rate_eur: '汇率 €/₽',
    vehicle_form_warehouse: '目的仓库',
    vehicle_form_note: '备注',

    // ── Vehicle items ──
    vehicle_items_empty: '车辆中暂无品项',
    vehicle_items_confirm_delete: '确认从车辆中删除品项？',
    vehicle_items_remove: '从车辆中删除',
    vehicle_items_clear_all: '全部删除',
    vehicle_items_confirm_clear_all: '确认删除车辆中的所有品项？此操作不可撤销。',
    vehicle_items_recalc: '🔄 重新计算成本',
    vehicle_items_recalcing: '计算中...',
    vehicle_items_recalc_error: '重新计算失败',
    price_resync_btn: '🔄 同步价格',
    price_resync_title: '从订单同步价格',
    price_resync_loading: '加载中...',
    price_resync_empty: '所有价格已与订单一致',
    price_resync_warning_status: '车辆已发运 — 重算将影响成本与BDR',
    price_resync_unlinked: '个未关联订单的品项（不变更）',
    price_resync_changes: '将变更品项数',
    price_resync_delta: '总变动',
    price_resync_apply: '应用',
    price_resync_applying: '应用中...',
    price_resync_cancel: '取消',
    price_resync_col_old: '原价 ¥',
    price_resync_col_new: '新价 ¥',
    price_resync_col_delta: 'Δ 金额 ¥',
    price_resync_applied: '已更新品项数:',
    paste_in_order: '订单中',
    paste_missing_price: '缺少价格 — 请填写后添加',
    paste_price_mismatch: '项价格与订单不同',
    paste_mismatch_title: '价格与订单不一致',
    paste_mismatch_count: '差异数',
    paste_col_order: '订单 ¥',
    paste_col_paste: '输入 ¥',
    paste_mismatch_hint: '选择：使用订单价格添加（忽略输入）或用输入价格覆盖订单并添加。',
    paste_keep_order: '使用订单价格',
    paste_overwrite_order: '覆盖订单并添加',

    // ── Vehicle cost summary ──
    cost_goods: '货物',
    cost_delivery: '运费',
    cost_duty: '关税',
    cost_vat: '增值税',
    cost_total: '合计',
    cost_container: '集装箱',
    cost_shipping: '运费',
    cost_weight: '重量',
    cost_arrival: '预计到达',
    cost_invoice: '发票号',

    // ── Add items to vehicle modal ──
    add_items_title: '添加货物到',
    add_items_subtitle: '从工厂订单中选择品项',
    add_items_empty: '暂无可用品项',
    add_items_pos: '项',
    add_items_adding: '添加中...',
    add_items_count: '添加',

    // ── Overview tab ──
    overview_status_title: '车辆状态分布',
    overview_no_status_data: '暂无状态数据',

    // ── Suppliers tab ──
    suppliers_title: '供应商',
    suppliers_empty: '暂无供应商，请创建！',
    suppliers_btn_create: '+ 新建供应商',
    suppliers_confirm_delete: '确认删除供应商',
    suppliers_edit_title: '编辑供应商',
    suppliers_new_title: '新建供应商',
    suppliers_form_name: '名称 *',
    suppliers_form_name_placeholder: '公司名称',
    suppliers_form_country: '国家',
    suppliers_form_country_china: '中国',
    suppliers_form_country_russia: '俄罗斯',
    suppliers_form_currency: '货币',
    suppliers_form_min_days: '最短交期（天）',
    suppliers_form_max_days: '最长交期（天）',
    suppliers_form_note: '备注',
    suppliers_form_note_placeholder: '供应商其他信息',

    // ── Supplier catalog ──
    catalog_back_to_list: '← 返回供应商列表',
    catalog_subtitle: '全部历史品项',
    catalog_kpi_orders: '订单',
    catalog_kpi_sku: '独立SKU',
    catalog_kpi_qty: '总数量',
    catalog_kpi_amount: '订单总额',
    catalog_search_placeholder: '🔍 按条码、货号、品名、品牌搜索…',
    catalog_subject_all: '全部品类',
    catalog_brand_all: '全部品牌',
    catalog_status_filter_all: '全部商品',
    catalog_status_filter_not_distributed: '未分配',
    catalog_status_filter_in_transit: '在途中',
    catalog_status_filter_delivered: '已发货',
    catalog_col_barcode: '条码',
    catalog_col_brand: '品牌',
    catalog_col_article: '货号',
    catalog_col_size: '尺寸',
    catalog_col_per_box: '每箱',
    catalog_col_weight: '重量,kg',
    catalog_col_total_qty: '总件数',
    catalog_col_last_price: '最新单价',
    catalog_col_avg_price: '均价',
    catalog_col_amount: '金额',
    catalog_col_delivered: '已发货',
    catalog_col_orders_count: '订单数',
    catalog_subject_empty: '未分类',
    catalog_sku_unit: 'SKU',
    catalog_history_title: '订单历史',
    catalog_history_col_order: '订单',
    catalog_history_col_date: '日期',
    catalog_history_col_qty: '数量',
    catalog_history_col_price: '单价',
    catalog_history_col_amount: '金额',
    catalog_history_col_vehicle: '车辆',
    catalog_history_col_status: '状态',
    catalog_status_delivered: '已签收',
    catalog_status_in_transit: '在途',
    catalog_status_at_factory: '工厂中',
    catalog_empty: '该供应商暂无订单',
    catalog_delivered_label: '已发货',
    catalog_excel_filename: '供应商品项',

    // ── Factory order detail (factory-orders/[id]) ──
    detail_back: '← 返回列表',
    detail_order: '订单',
    detail_not_found: '订单未找到',
    detail_badge_undistributed: '未分配',
    detail_badge_distributed: '已分配',
    detail_tab_items: '品项',
    detail_tab_history: '历史记录',
    detail_filter_all: '全部',
    detail_filter_undistributed: '未分配',
    detail_filter_distributed: '已分配',
    detail_history_empty: '暂无记录',

    detail_event_created: '订单已创建',
    detail_event_status_change: '状态变更',
    detail_event_distributed: '分配',
    detail_event_items_added: '品项已添加',
    detail_event_items_removed: '品项已删除',

    detail_field_order_number: '订单编号',
    detail_field_supplier: '供应商',
    detail_field_ready_date: '完成日期',
    detail_field_note: '备注',

    detail_kpi_items: '品项数',
    detail_kpi_qty: '数量',
    detail_kpi_boxes: '箱数',
    detail_kpi_volume: '体积',
    detail_kpi_weight: '重量',
    detail_kpi_amount: '金额',
    detail_kpi_distribution: '分配',

    detail_items_title: '品项',
    detail_items_empty: '暂无品项',
    detail_confirm_delete_item: '确认删除品项？',
    detail_delete_selected: '删除选中',
    detail_confirm_delete_selected: '确认删除选中的 {count} 个品项？此操作不可撤销。',
    detail_delete_selected_error: '无法删除 {count} 个品项（已分配到车辆）',
    detail_add_items: '+ 添加',
    detail_paste_hint: '或从Excel粘贴数据 (Ctrl+V)',
    detail_paste_header: '添加品项',
    detail_paste_sub: '从Excel粘贴: 条码、数量、单价、箱规、件/箱、重量',
    detail_total: '合计',
    paste_specs_btn: '粘贴箱规',
    paste_specs_header: '更新箱规',
    paste_specs_sub: '从剪贴板粘贴 (Tab): 条码、箱规、件/箱、重量',
    paste_specs_hint: '点击此处粘贴数据 (Ctrl+V)',
    paste_specs_apply: '应用',
    paste_specs_matched: '已匹配',
    paste_specs_not_found: '未找到',
    paste_specs_pasted: '粘贴了 {total} 行。找到: {matched} / {total}',
    paste_specs_result: '已更新 {updated} 个品项。',
    paste_specs_result_nf: '未找到: {count} 个条码（已跳过）。',
    paste_specs_found: '已匹配',
    paste_specs_nf_label: '未匹配',

    detail_col_subject: '名称',
    detail_col_mix: '混装',
    detail_mix_group: '混装组',

    // ── Vehicle detail (vehicles/[...orderNo]) ──
    vdetail_back: '← 返回列表',
    vdetail_vehicle: '车辆',
    vdetail_not_found: '车辆未找到',
    vdetail_confirm_delete: '确认删除车辆',
    vdetail_deleting: '删除中...',

    vdetail_tab_items: '📦 品项',
    vdetail_tab_cost: '💰 成本',
    vdetail_tab_docs: '📎 文件',
    vdetail_tab_history: '📋 历史',

    vdetail_doc_type_invoice: '发票',
    vdetail_doc_type_order: '订单',
    vdetail_doc_type_customs: '报关单',
    vdetail_doc_type_contract: '合同',
    vdetail_doc_type_other: '其他',

    vdetail_docs_title: '文件',
    vdetail_docs_upload: '上传文件',
    vdetail_docs_empty: '暂无文件',
    vdetail_docs_col_type: '类型',
    vdetail_docs_col_file: '文件',
    vdetail_docs_col_uploaded: '上传时间',
    vdetail_docs_download: '下载',
    vdetail_docs_upload_error: '文件上传失败',

    vdetail_history_title: '状态历史',
    vdetail_history_empty: '暂无记录',

    vdetail_items_add: '+ 添加货物',
    vdetail_confirm_status: '确认更改状态？',
    vdetail_ship_confirm: '确认发货？',

    vdetail_field_container: '集装箱',
    vdetail_field_ship_date: '发货日期',
    vdetail_field_arrival: '预计到达',
    vdetail_field_customs_no: '报关单号',
    vdetail_field_delivery_cost: '运费',

    // ── Shipment matrix ──
    matrix_mode_catalog: '目录',
    matrix_mode_shipment: '出货单',
    matrix_col_barcode: '条形码',
    matrix_col_article: '编号',
    matrix_col_subject: '品类',
    matrix_col_brand: '品牌',
    matrix_col_total_qty: '总数量',
    matrix_col_boxes: '箱数',
    matrix_col_remaining: '剩余',
    matrix_col_pct: '出货 %',
    matrix_kpi_total: '总出货量',
    matrix_kpi_in_vehicles: '在车辆中',
    matrix_kpi_really_shipped: '已出发',
    matrix_kpi_shipped: '已出货',
    matrix_kpi_remaining: '剩余',
    matrix_kpi_boxes: '总箱数',
    matrix_filter_unshipped: '仅未出货',
    matrix_filter_search: '按条形码/货号搜索',
    matrix_filter_subjects_all: '所有品类',
    matrix_filter_brands_all: '所有品牌',
    matrix_filter_subjects_count: '品类: {count}',
    matrix_filter_brands_count: '品牌: {count}',
    matrix_empty: '暂无出货数据',
    matrix_empty_filtered: '未找到符合筛选的项目',
    matrix_total_row: '合计',
    matrix_of_total: '共 {total}',
    matrix_click_vehicle_hint: '点击以打开车辆',
};

const translations: Record<Lang, TranslationDict> = { ru, zh };

// ─── Context ────────────────────────────────────────────────────────────────

const STORAGE_KEY = 'dds_supply_lang';

interface I18nContextValue {
    lang: Lang;
    setLang: (l: Lang) => void;
    t: (key: string) => string;
}

const I18nContext = createContext<I18nContextValue>({
    lang: 'ru',
    setLang: () => {},
    t: (key) => key,
});

/** Currency symbol helper for supplier currency */
export function currencySymbol(currency?: string): string {
    if (currency === 'RUB') return '₽';
    return '¥';
}

/** Subject (category) translations: Russian → Chinese */
const subjectZh: Record<string, string> = {
    'Коврики для ванной': '浴室地垫',
    'Ковры': '地毯',
    'Наволочки декоративные': '装饰枕套',
    'Одеяла': '被子',
    'Пледы': '毛毯',
    'Покрывала': '床盖',
    'Чехлы для мебели': '家具罩',
    'Шторы интерьерные': '窗帘',
    'Шторы': '窗帘',
    'Полотенца': '毛巾',
    'Постельное бельё': '床上用品',
    'Подушки': '枕头',
    'Скатерти': '桌布',
    'Салфетки': '餐巾',
    'Матрасы': '床垫',
    'Наматрасники': '床垫保护罩',
    'Халаты': '浴袍',
    'Тапочки': '拖鞋',
    'Простыни': '床单',
    'Пододеяльники': '被套',
    'Наволочки': '枕套',
    'Комплекты постельного белья': '床品套件',
    'Занавески': '帘子',
    'Дорожки настольные': '桌旗',
    'Декоративные подушки': '装饰靠垫',
    'Без предмета': '未分类',
};

/** Translate subject to Chinese for Chinese suppliers when UI is in Chinese, else return as-is */
export function translateSubject(subject: string, country?: string, lang?: string): string {
    if (lang === 'zh' && country === 'CHINA' && subjectZh[subject]) return subjectZh[subject];
    return subject;
}

export function LanguageProvider({ children }: { children: React.ReactNode }) {
    const [lang, setLangState] = useState<Lang>('ru');

    useEffect(() => {
        try {
            const stored = localStorage.getItem(STORAGE_KEY);
            if (stored === 'zh' || stored === 'ru') setLangState(stored);
        } catch {}
    }, []);

    const setLang = useCallback((l: Lang) => {
        setLangState(l);
        try { localStorage.setItem(STORAGE_KEY, l); } catch {}
    }, []);

    const t = useCallback((key: string): string => {
        return translations[lang][key] ?? translations.ru[key] ?? key;
    }, [lang]);

    return (
        <I18nContext.Provider value={{ lang, setLang, t }}>
            {children}
        </I18nContext.Provider>
    );
}

// ─── Hook ───────────────────────────────────────────────────────────────────

export function useT() {
    return useContext(I18nContext);
}

// ─── Toggle Component ───────────────────────────────────────────────────────

export function LanguageToggle() {
    const { lang, setLang } = useT();
    return (
        <div style={{
            display: 'inline-flex',
            borderRadius: 10,
            overflow: 'hidden',
            border: '2px solid var(--color-accent)',
            fontSize: 14,
            fontWeight: 700,
            boxShadow: '0 2px 8px rgba(0,113,227,0.18)',
        }}>
            <button
                onClick={() => setLang('ru')}
                style={{
                    padding: '6px 14px',
                    border: 'none',
                    cursor: 'pointer',
                    background: lang === 'ru' ? 'var(--color-accent)' : 'var(--color-bg-card)',
                    color: lang === 'ru' ? '#fff' : 'var(--color-accent)',
                    transition: 'all 0.2s',
                    letterSpacing: '0.02em',
                }}
            >
                🇷🇺 RU
            </button>
            <button
                onClick={() => setLang('zh')}
                style={{
                    padding: '6px 14px',
                    border: 'none',
                    borderLeft: '1px solid var(--color-accent)',
                    cursor: 'pointer',
                    background: lang === 'zh' ? 'var(--color-accent)' : 'var(--color-bg-card)',
                    color: lang === 'zh' ? '#fff' : 'var(--color-accent)',
                    transition: 'all 0.2s',
                    letterSpacing: '0.02em',
                }}
            >
                🇨🇳 中文
            </button>
        </div>
    );
}
