/**
 * Подсказка на выключенных кнопках записи FBS.
 *
 * Регресс, который она закрывает: GET /fbs/mode падал на транзиенте (окно
 * деплоя отдаёт 502/504), `mode` оставался null, и восемь кнопок записи на
 * БОЕВОМ контуре утверждали «Режим safe: запись отключена… переключите
 * WB_FBS_MODE». Пользователь шёл крутить переменную окружения, хотя запись
 * была разрешена, а не доехал один запрос.
 */
import { describe, expect, it } from 'vitest';
import type { FbsModeInfo } from '@/types/api';
import {
    MODE_UNKNOWN_HINT,
    WRITE_DISABLED_HINT,
    writeDisabledHint,
} from '@/app/(main)/p/[slug]/warehouse/fbs/fbsShared';

function mode(over: Partial<FbsModeInfo>): FbsModeInfo {
    return {
        mode: 'safe',
        write_enabled: false,
        api_base: 'https://marketplace-api.wildberries.ru',
        has_key: true,
        has_sandbox_key: false,
        ...over,
    };
}

describe('writeDisabledHint', () => {
    it('режим не загружен → говорит про загрузку, а не про safe', () => {
        expect(writeDisabledHint(null)).toBe(MODE_UNKNOWN_HINT);
        expect(writeDisabledHint(null)).not.toContain('safe');
    });

    it('режим safe реально загружен → прежний текст про WB_FBS_MODE', () => {
        expect(writeDisabledHint(mode({ mode: 'safe' }))).toBe(WRITE_DISABLED_HINT);
    });

    it('текст для prod остаётся определённым (подсказка видна только на выключенной кнопке)', () => {
        expect(writeDisabledHint(mode({ mode: 'prod', write_enabled: true }))).toBe(WRITE_DISABLED_HINT);
    });
});
