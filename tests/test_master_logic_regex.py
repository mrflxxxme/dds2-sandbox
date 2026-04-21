"""
Unit tests for master_logic.py regex patterns and enrichment functions.
100% coverage for all regex patterns (positive + negative cases).
No DB required — pure unit tests.
"""


# ─── Import regex patterns directly from master_logic ────────────────────────

from backend.etl.master_logic import (
    RE_CONTRACT,
    RE_DEPOSIT_INTEREST,
    RE_DEPOSIT_PLACE,
    RE_DEPOSIT_RETURN,
    RE_FULFILLMENT,
    RE_LOAN_DISBURSEMENT,
    RE_LOAN_INTEREST,
    RE_LOAN_REPAY,
    RE_LOGISTICS,
    RE_UNK,
    enrich_purpose,
)

# ─── RE_CONTRACT ──────────────────────────────────────────────────────────────


class TestReContract:
    """Tests for CONTRACT number extraction regex."""

    def test_contract_no_dot(self):
        m = RE_CONTRACT.search("CONTRACT NO.20250707 on goods")
        assert m is not None
        assert m.group(1) == "20250707"

    def test_contract_no_space_only(self):
        m = RE_CONTRACT.search("CONTRACT 20260317 annex")
        assert m is not None
        assert m.group(1) == "20260317"

    def test_contract_lowercase(self):
        m = RE_CONTRACT.search("contract no. 20250801 payment")
        assert m is not None
        assert m.group(1) == "20250801"

    def test_contract_uppercase_no_dot(self):
        m = RE_CONTRACT.search("CONTRACT20260331")
        assert m is not None
        assert m.group(1) == "20260331"

    # Negative cases
    def test_no_match_contract_of_sale(self):
        assert RE_CONTRACT.search("contract of sale") is None

    def test_no_match_no_contract(self):
        assert RE_CONTRACT.search("no contract found") is None

    def test_no_match_dashed_number(self):
        # CONTRACT-123 — dashed, less than 6 digits
        assert RE_CONTRACT.search("CONTRACT-12345") is None

    def test_no_match_short_number(self):
        # Less than 6 digits
        assert RE_CONTRACT.search("CONTRACT NO.12345") is None

    def test_no_match_empty(self):
        assert RE_CONTRACT.search("") is None


# ─── RE_UNK ───────────────────────────────────────────────────────────────────


class TestReUnk:
    """Tests for УНК (unique contract number) extraction regex."""

    def test_unk_full_format(self):
        m = RE_UNK.search("УНК 24093000/0423/0000/2/1 оплата")
        assert m is not None
        assert m.group(1) == "24093000/0423/0000/2/1"

    def test_unk_simple_number(self):
        m = RE_UNK.search("Платёж по УНК 123456789")
        assert m is not None
        assert m.group(1) == "123456789"

    def test_unk_lowercase(self):
        m = RE_UNK.search("унк 123/456")
        assert m is not None
        assert m.group(1) == "123/456"

    # Negative cases
    def test_no_match_unknown_word(self):
        assert RE_UNK.search("unknown contract") is None

    def test_no_match_unka_suffix(self):
        # УНКа — not a match
        assert RE_UNK.search("УНКа 123456") is None

    def test_no_match_empty(self):
        assert RE_UNK.search("") is None


# ─── RE_DEPOSIT_PLACE ─────────────────────────────────────────────────────────


class TestReDepositPlace:
    """Tests for deposit placement regex."""

    def test_deposit_place_basic(self):
        assert RE_DEPOSIT_PLACE.search("Размещение средств в депозит 1М") is not None

    def test_deposit_place_extended(self):
        assert RE_DEPOSIT_PLACE.search("Размещение средств в депозит по договору №5") is not None

    def test_deposit_place_lowercase(self):
        assert RE_DEPOSIT_PLACE.search("размещение средств в депозит") is not None

    # Negative cases
    def test_no_match_return(self):
        assert RE_DEPOSIT_PLACE.search("Возврат депозита") is None

    def test_no_match_interest(self):
        assert RE_DEPOSIT_PLACE.search("Уплата процентов по депозиту") is None

    def test_no_match_empty(self):
        assert RE_DEPOSIT_PLACE.search("") is None


# ─── RE_DEPOSIT_RETURN ────────────────────────────────────────────────────────


class TestReDepositReturn:
    """Tests for deposit return regex."""

    def test_deposit_return_basic(self):
        assert RE_DEPOSIT_RETURN.search("Возврат депозита по договору") is not None

    def test_deposit_return_lowercase(self):
        assert RE_DEPOSIT_RETURN.search("возврат депозита") is not None

    # Negative cases
    def test_no_match_placement(self):
        assert RE_DEPOSIT_RETURN.search("Размещение средств в депозит") is None

    def test_no_match_interest(self):
        assert RE_DEPOSIT_RETURN.search("Уплата процентов по депозиту") is None

    def test_no_match_empty(self):
        assert RE_DEPOSIT_RETURN.search("") is None


# ─── RE_DEPOSIT_INTEREST ──────────────────────────────────────────────────────


class TestReDepositInterest:
    """Tests for deposit interest regex."""

    def test_deposit_interest_basic(self):
        assert RE_DEPOSIT_INTEREST.search("Уплата процентов по депозиту") is not None

    def test_deposit_interest_extended(self):
        assert RE_DEPOSIT_INTEREST.search("Уплата процентов по депозиту за март 2026") is not None

    def test_deposit_interest_lowercase(self):
        assert RE_DEPOSIT_INTEREST.search("уплата процентов по депозиту") is not None

    # Negative cases
    def test_no_match_loan_interest(self):
        assert RE_DEPOSIT_INTEREST.search("Проценты по займу") is None

    def test_no_match_placement(self):
        assert RE_DEPOSIT_INTEREST.search("Размещение средств в депозит") is None

    def test_no_match_empty(self):
        assert RE_DEPOSIT_INTEREST.search("") is None


# ─── RE_LOAN_DISBURSEMENT ─────────────────────────────────────────────────────


class TestReLoanDisbursement:
    """Tests for loan disbursement regex."""

    def test_loan_disbursement_predostavlenie(self):
        assert RE_LOAN_DISBURSEMENT.search("Предоставление денежных средств по Договору займа №3") is not None

    def test_loan_disbursement_vydacha(self):
        assert RE_LOAN_DISBURSEMENT.search("Выдача займа по Договору №5") is not None

    def test_loan_disbursement_perevod(self):
        assert RE_LOAN_DISBURSEMENT.search("Перевод по договору займа 123/2026") is not None

    def test_loan_disbursement_lowercase(self):
        assert RE_LOAN_DISBURSEMENT.search("предоставление по договору займа") is not None

    # Negative cases
    def test_no_match_repayment(self):
        assert RE_LOAN_DISBURSEMENT.search("Возврат займа по договору") is None

    def test_no_match_deposit(self):
        assert RE_LOAN_DISBURSEMENT.search("Размещение в депозит") is None

    def test_no_match_empty(self):
        assert RE_LOAN_DISBURSEMENT.search("") is None


# ─── RE_LOAN_REPAY ────────────────────────────────────────────────────────────


class TestReLoanRepay:
    """Tests for loan repayment regex."""

    def test_loan_repay_oplata(self):
        assert RE_LOAN_REPAY.search("Оплата по договору займа") is not None

    def test_loan_repay_vozvrat(self):
        assert RE_LOAN_REPAY.search("Возврат по договору займа №5/2024") is not None

    def test_loan_repay_lowercase(self):
        assert RE_LOAN_REPAY.search("возврат по договору займа") is not None

    # Negative cases
    def test_no_match_disbursement(self):
        assert RE_LOAN_REPAY.search("Выдача займа по договору") is None

    def test_no_match_deposit(self):
        assert RE_LOAN_REPAY.search("Размещение депозита") is None

    def test_no_match_empty(self):
        assert RE_LOAN_REPAY.search("") is None


# ─── RE_LOAN_INTEREST ─────────────────────────────────────────────────────────


class TestReLoanInterest:
    """Tests for loan interest regex."""

    def test_loan_interest_basic(self):
        assert RE_LOAN_INTEREST.search("Уплата процентов по договору займа") is not None

    def test_loan_interest_short_form(self):
        assert RE_LOAN_INTEREST.search("Уплата процентов по займу №3/2026") is not None

    def test_loan_interest_lowercase(self):
        assert RE_LOAN_INTEREST.search("уплата процентов по займу") is not None

    # Negative cases
    def test_no_match_deposit_interest(self):
        assert RE_LOAN_INTEREST.search("Проценты по депозиту") is None

    def test_no_match_empty(self):
        assert RE_LOAN_INTEREST.search("") is None


# ─── RE_FULFILLMENT ───────────────────────────────────────────────────────────


class TestReFulfillment:
    """Tests for fulfillment service regex."""

    def test_fulfillment_upakovka(self):
        assert RE_FULFILLMENT.search("упаковка товара ИП Кузнецов") is not None

    def test_fulfillment_markirovka(self):
        assert RE_FULFILLMENT.search("маркировка WB январь 2026") is not None

    def test_fulfillment_ff_number(self):
        assert RE_FULFILLMENT.search("Услуги по ФФ-12 за март") is not None

    def test_fulfillment_ff_space(self):
        assert RE_FULFILLMENT.search("фф услуги за февраль") is not None

    def test_fulfillment_fulfilment_word(self):
        assert RE_FULFILLMENT.search("фулфилмент отгрузка 2026") is not None

    # Negative cases — documented false-negatives (not matching as intended)
    def test_no_match_fundament(self):
        assert RE_FULFILLMENT.search("фундамент строительства") is None

    def test_no_match_fasovka(self):
        # фасовка — packaging but NOT fulfillment in our domain
        assert RE_FULFILLMENT.search("фасовка бумаг") is None


# ─── RE_LOGISTICS ─────────────────────────────────────────────────────────────


class TestReLogistics:
    """Tests for logistics regex."""

    def test_logistics_dostavka(self):
        assert RE_LOGISTICS.search("доставка груза из Иваново") is not None

    def test_logistics_transportnie(self):
        assert RE_LOGISTICS.search("транспортные услуги январь") is not None

    def test_logistics_ekspediciya(self):
        assert RE_LOGISTICS.search("экспедиция по маршруту") is not None

    def test_logistics_forwarding_en(self):
        assert RE_LOGISTICS.search("forwarding fee 2026") is not None

    def test_logistics_freight(self):
        assert RE_LOGISTICS.search("freight charges Q1") is not None

    def test_logistics_perevoz(self):
        assert RE_LOGISTICS.search("перевозка пиломатериалов") is not None

    # Documented false positive (we accept it as per TESTS.md note)
    def test_false_positive_pizza_documented(self):
        # NOTE: "доставка пиццы" matches — documented as known false positive
        # from TESTS.md: "ложное попадание — документируем"
        result = RE_LOGISTICS.search("доставка пиццы в офис")
        # We document that this DOES match (it's expected behaviour)
        assert result is not None  # known false positive — documented

    def test_no_match_empty(self):
        assert RE_LOGISTICS.search("") is None


# ─── enrich_purpose function ─────────────────────────────────────────────────


class TestEnrichPurpose:
    """Tests for the enrich_purpose function that applies all regex."""

    def test_enrich_contract_number(self):
        result = enrich_purpose("Оплата CONTRACT NO.20250707 за товар")
        assert result["contract_number"] == "20250707"
        assert result["unk_number"] is None
        assert result["event_type2_override"] is None

    def test_enrich_unk_number(self):
        result = enrich_purpose("УНК 24093000/0423/0000/2/1 за услуги")
        assert result["unk_number"] == "24093000/0423/0000/2/1"
        assert result["contract_number"] is None

    def test_enrich_deposit_place(self):
        result = enrich_purpose("Размещение средств в депозит 1М")
        assert result["event_type2_override"] == "DEPOSIT_PLACE"
        assert result["is_cashflow2_override"] == 0

    def test_enrich_deposit_return(self):
        result = enrich_purpose("Возврат депозита по договору №5")
        assert result["event_type2_override"] == "DEPOSIT_RETURN"
        assert result["is_cashflow2_override"] == 0

    def test_enrich_deposit_interest(self):
        result = enrich_purpose("Уплата процентов по депозиту за март")
        assert result["event_type2_override"] == "DEPOSIT_INTEREST"
        assert result["is_cashflow2_override"] == 1

    def test_enrich_loan_disbursement(self):
        result = enrich_purpose("Предоставление средств по Договору займа №3")
        assert result["loan_payment_type"] == "DISBURSEMENT"

    def test_enrich_loan_repay(self):
        result = enrich_purpose("Возврат по договору займа")
        assert result["loan_payment_type"] == "PRINCIPAL_REPAY"

    def test_enrich_loan_interest(self):
        result = enrich_purpose("Уплата процентов по займу")
        assert result["loan_payment_type"] == "INTEREST_PAY"

    def test_enrich_none_purpose(self):
        result = enrich_purpose(None)
        assert result["contract_number"] is None
        assert result["unk_number"] is None
        assert result["event_type2_override"] is None
        assert result["loan_payment_type"] is None

    def test_enrich_empty_purpose(self):
        result = enrich_purpose("")
        assert result["contract_number"] is None
        assert result["unk_number"] is None
        assert result["event_type2_override"] is None

    def test_enrich_regular_payment(self):
        result = enrich_purpose("Оплата за товар по счёту №123")
        assert result["contract_number"] is None
        assert result["unk_number"] is None
        assert result["event_type2_override"] is None
        assert result["loan_payment_type"] is None
