# Изолированный ТЕСТ-РИГ крипто-инфры КриптоПро CSP (НЕ прод-образ).
# Цель: убедиться, что CSP ставится в python:3.11-slim (Debian) и csptest/cprocsp-curl
# работают, ДО интеграции слоя в Dockerfile.backend и до появления серверного ключа.
#
# Сборка (контекст = КОРЕНЬ репо, нужен docker/cryptopro/linux-amd64_deb.tgz):
#   docker build -f docker/cryptopro/Dockerfile -t dds-cryptopro-test .
# Проверка вручную:
#   docker run --rm dds-cryptopro-test cpconfig -license -view
#   docker run --rm dds-cryptopro-test csptest -keyset -enum_cont -verifycontext -fqcn
#
# Платформа: дистрибутив amd64, а локальный Mac — arm64 → форсируем linux/amd64
# (на Mac собирается под эмуляцией; прод-сервер amd64 нативно). В Dockerfile.backend
# при интеграции платформу НЕ пиннить — прод-CI уже amd64.
FROM --platform=linux/amd64 python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
# CryptoPro кладёт бинари в /opt/cprocsp/{bin,sbin}/amd64
ENV PATH="/opt/cprocsp/bin/amd64:/opt/cprocsp/sbin/amd64:${PATH}"

# Системные зависимости CSP
RUN apt-get update && apt-get install -y --no-install-recommends \
        procps tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/cp
COPY docker/cryptopro/linux-amd64_deb.tgz .

# Установка: install.sh ставит базовый набор (base+rdr+kc1+capilite+curl),
# затем CAdES отдельно (не-фатально — подпись идёт через csptest из базового CSP).
RUN tar xzf linux-amd64_deb.tgz \
    && cd linux-amd64_deb \
    && bash install.sh \
    && (dpkg -i cprocsp-pki-cades-64_*.deb || true) \
    && cd / && rm -rf /tmp/cp

# Смоук-тест (не валим сборку — читаем вывод в логе): версия CSP, статус лицензии (триал),
# перечисление контейнеров (пусто — это норма, ключа ещё нет).
RUN echo "=== csptest version ===" && (csptest 2>&1 | head -3 || true) \
    && echo "=== license ===" && (cpconfig -license -view 2>&1 || true) \
    && echo "=== cprocsp-curl ===" && (ls -l /opt/cprocsp/bin/amd64/curl 2>&1 || true) \
    && echo "=== enum containers (empty ok) ===" && (csptest -keyset -enum_cont -verifycontext -fqcn 2>&1 | head -5 || true)

CMD ["bash"]
