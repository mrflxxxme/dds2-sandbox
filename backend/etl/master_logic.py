# ruff: noqa: RUF001
"""
Master Logic: applies business rules to normalized transactions.
Produces: txn_id, cp_key, net, is_internal, is_fx, event_type2,
          is_cashflow2, purpose_tag, invoice_id, annex_id,
          cat_lvl1_2, cat_lvl2_2, status
"""

import hashlib
import re

import pandas as pd

# ─── Regex patterns ──────────────────────────────────────────────────────────

RE_FX = re.compile(
    r"конверси|fx|курс|cny.*rub|rub.*cny|покупка валют|продажа валют|currency",
    re.IGNORECASE,
)
RE_COMMISSION = re.compile(
    r"комисси|commission|swift|тариф|bank charge",
    re.IGNORECASE,
)
# Extended logistics regex (Phase 2 counterparties-loans)
RE_LOGISTICS = re.compile(
    r"invoice|forwarding|freight|transport|доставк|транспортн|перевоз|экспедиц|форвардин",
    re.IGNORECASE,
)
RE_ORDER = re.compile(
    r"annex|appendix|приложен|according to|contract|по договор",
    re.IGNORECASE,
)
RE_INVOICE_ID = re.compile(r"INVOICE\s*([A-Z0-9\-]+)", re.IGNORECASE)
RE_ANNEX_ID = re.compile(r"(?:ANNEX|APPENDIX|ПРИЛОЖЕН(?:ИЕ)?)\s*[№#]?\s*([0-9]+)", re.IGNORECASE)

# ─── Phase 2: Counterparties / Loans regex patterns ─────────────────────────

# China suppliers / foreign contracts
RE_CONTRACT = re.compile(r"CONTRACT\s*(?:NO\.?)?\s*(\d{6,})", re.IGNORECASE)
RE_UNK = re.compile(r"УНК\s+([\d/]+)", re.IGNORECASE)
RE_MT103 = re.compile(r"МТ103\s*реф\.?([A-Z0-9]+)", re.IGNORECASE)

# Deposits — do NOT count as cashflow (is_cashflow2 = 0 for place/return)
RE_DEPOSIT_PLACE = re.compile(r"Размещени\w*\s+средств\s+в\s+депозит", re.IGNORECASE)
RE_DEPOSIT_RETURN = re.compile(r"Возврат\s+депозита", re.IGNORECASE)
RE_DEPOSIT_INTEREST = re.compile(r"Уплата\s+процентов\s+по\s+депозит", re.IGNORECASE)

# Loans — link to Loan via contract_number
RE_LOAN_INTEREST = re.compile(r"Уплата\s+процентов\s+по.*займ", re.IGNORECASE)
# Disbursement = "Предоставление/Выдача/Перевод" + somewhere "договор" AND "займ"
RE_LOAN_DISBURSEMENT = re.compile(
    r"(?:Предоставлени|Выдач|Перевод)\w*.*?(?:(?:договор\w*.*?займ\w*)|(?:займ\w*.*?договор\w*))",
    re.IGNORECASE | re.DOTALL,
)
RE_LOAN_REPAY = re.compile(
    r"(?:Оплата|Возврат)\w*.*?(?:(?:договор\w*.*?займ\w*)|(?:займ\w*.*?договор\w*))",
    re.IGNORECASE | re.DOTALL,
)

# Fulfillment / logistics (category detection from purpose)
RE_FULFILLMENT = re.compile(r"упаковк|маркировк|фулфилмент|ФФ-\d|\bфф\s", re.IGNORECASE)


# ─── txn_id ──────────────────────────────────────────────────────────────────


def make_txn_id(date, account, currency, counterparty_account, counterparty, income, expense, purpose) -> str:
    date_str = pd.Timestamp(date).strftime("%Y%m%d") if date is not None else "00000000"
    cp_acc = str(counterparty_account or counterparty or "").strip()[:50]
    purpose_part = str(purpose or "").strip().lower()[:80]
    inc = str(round(float(income or 0), 2))
    exp = str(round(float(expense or 0), 2))

    key = f"{date_str}|{account}|{currency}|{cp_acc}|{inc}|{exp}|{purpose_part}"
    # Return the key directly (readable, matches existing data format)
    return key


def make_txn_id_hash(date, account, currency, counterparty_account, counterparty, income, expense, purpose) -> str:
    """SHA1-based stable ID for deduplication."""
    raw = make_txn_id(date, account, currency, counterparty_account, counterparty, income, expense, purpose)
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()


# ─── cp_key ──────────────────────────────────────────────────────────────────


def make_cp_key(inn: str | None, counterparty: str | None) -> str | None:
    if inn and str(inn).strip():
        return str(inn).strip()
    if counterparty and str(counterparty).strip():
        return str(counterparty).strip().lower()
    return None


# ─── Purpose tag ─────────────────────────────────────────────────────────────


def get_purpose_tag(purpose: str | None) -> str:
    if not purpose:
        return "Другое"
    p = str(purpose)
    if RE_COMMISSION.search(p):
        return "Комиссия"
    if RE_LOGISTICS.search(p):
        return "Логистика"
    if RE_ORDER.search(p):
        return "Заказ"
    return "Другое"


def extract_invoice_id(purpose: str | None) -> str | None:
    if not purpose:
        return None
    m = RE_INVOICE_ID.search(str(purpose))
    return m.group(1).upper() if m else None


def extract_annex_id(purpose: str | None) -> str | None:
    if not purpose:
        return None
    m = RE_ANNEX_ID.search(str(purpose))
    return m.group(1) if m else None


# ─── Invoice number normalization (machine invoice_no ↔ statement invoice_id) ─

# Cyrillic homoglyphs → Latin: real machine invoice_no values mix scripts
# (e.g. «СС20260036» with Cyrillic С instead of Latin C). Applied after upper().
_CYR2LAT = str.maketrans("АВЕКМНОРСТХ", "ABEKMHOPCTX")


def normalize_invoice_token(value: str) -> str:
    """Normalize a single invoice token: trim, upper-case, Cyrillic→Latin."""
    return str(value).strip().upper().translate(_CYR2LAT)


def normalize_invoice_no(raw: str | None) -> list[str]:
    """Split a (possibly composite) invoice string into normalized tokens.

    Machines store one or several invoices in ``CostOrder.invoice_no`` joined by
    ``+`` (e.g. «CC20260033+BZ-260402+HD-W20260328-1»); values may carry stray
    whitespace and Cyrillic homoglyphs. Returns a de-duplicated, order-preserving
    list of normalized tokens for matching against ``transactions.invoice_id``.
    """
    if not raw:
        return []
    tokens: list[str] = []
    for part in re.split(r"[+,;]", str(raw)):
        token = normalize_invoice_token(part)
        if token and token not in tokens:
            tokens.append(token)
    return tokens


# ─── Phase 2: Purpose enrichment (contract_number, unk, deposits, loans) ─────


def enrich_purpose(purpose: str | None) -> dict:
    """
    Apply Phase 2 regex to a transaction purpose string.

    Returns a dict with optional fields:
      - contract_number: str | None
      - unk_number: str | None
      - mt103_ref: str | None
      - event_type2_override: "DEPOSIT_PLACE" | "DEPOSIT_RETURN" | "DEPOSIT_INTEREST" | None
      - is_cashflow2_override: int | None  (0 for place/return, 1 for interest)
      - loan_payment_type: "DISBURSEMENT" | "PRINCIPAL_REPAY" | "INTEREST_PAY" | None

    Pure function; no side effects.
    """
    result: dict = {
        "contract_number": None,
        "unk_number": None,
        "mt103_ref": None,
        "event_type2_override": None,
        "is_cashflow2_override": None,
        "loan_payment_type": None,
    }
    if not purpose:
        return result
    text = str(purpose)

    # Contract number (foreign contracts only, min 6 digits)
    m = RE_CONTRACT.search(text)
    if m:
        result["contract_number"] = m.group(1)

    # UNK — currency control number
    m = RE_UNK.search(text)
    if m:
        result["unk_number"] = m.group(1)

    # MT103 ref
    m = RE_MT103.search(text)
    if m:
        result["mt103_ref"] = m.group(1)

    # Deposits — order matters (interest before return/place since they can co-occur)
    if RE_DEPOSIT_INTEREST.search(text):
        result["event_type2_override"] = "DEPOSIT_INTEREST"
        result["is_cashflow2_override"] = 1  # interest IS cashflow (income/expense)
    elif RE_DEPOSIT_RETURN.search(text):
        result["event_type2_override"] = "DEPOSIT_RETURN"
        result["is_cashflow2_override"] = 0  # return of principal is not cashflow
    elif RE_DEPOSIT_PLACE.search(text):
        result["event_type2_override"] = "DEPOSIT_PLACE"
        result["is_cashflow2_override"] = 0  # placement is not cashflow

    # Loans (interest first since it's most specific)
    if RE_LOAN_INTEREST.search(text):
        result["loan_payment_type"] = "INTEREST_PAY"
    elif RE_LOAN_REPAY.search(text):
        result["loan_payment_type"] = "PRINCIPAL_REPAY"
    elif RE_LOAN_DISBURSEMENT.search(text):
        result["loan_payment_type"] = "DISBURSEMENT"

    return result


# ─── Master Logic pipeline ───────────────────────────────────────────────────


def apply_master_logic(
    df: pd.DataFrame,
    our_accounts: set[str],  # account numbers where is_our_account=True
    customs_payee_accounts: set[str],  # account numbers where is_customs_payee=True
    cp_categories: dict[str, dict],  # {cp_key: {cat_lvl1, cat_lvl2}}
    overrides: dict[str, dict],  # {txn_id: {cat_lvl1, cat_lvl2}}
    synced_accounts: set[str] | None = None,  # own-accounts whose statement we actually import
) -> pd.DataFrame:
    """
    Input df must have NORM_COLS.
    Returns enriched df with all master logic columns.

    ``synced_accounts`` gates internal-transfer detection (see ``is_internal``
    below). When ``None`` the legacy behaviour is used — every own-account is
    treated as internal — kept for unit tests and ad-hoc calls.
    """
    result = df.copy()

    # ── txn_id ──
    result["txn_id"] = result.apply(
        lambda r: make_txn_id(
            r["date"],
            r["account"],
            r["currency"],
            r.get("counterparty_account"),
            r.get("counterparty"),
            r.get("income", 0),
            r.get("expense", 0),
            r.get("purpose"),
        ),
        axis=1,
    )

    # ── cp_key ──
    result["cp_key"] = result.apply(lambda r: make_cp_key(r.get("inn"), r.get("counterparty")), axis=1)

    # ── net ──
    result["income"] = pd.to_numeric(result["income"], errors="coerce").fillna(0)
    result["expense"] = pd.to_numeric(result["expense"], errors="coerce").fillna(0)
    result["net"] = result["income"] - result["expense"]

    # ── is_internal ──
    # An own-account triggers internal-transfer detection ONLY if we actually
    # import its statement (it is a "statement owner"). A registered own-account
    # we never sync — a "ghost" account, e.g. a WB transit account the bank API
    # does not expose — must NOT swallow real income: a credit arriving FROM such
    # an account is its first and only appearance in our books (the opposite leg
    # is never imported), i.e. real cashflow, not one leg of a tracked internal
    # movement. ``synced_accounts`` = own-accounts whose statements we import;
    # accounts owned by the current batch count too (first-ever import). When it
    # is None the caller opts into the legacy "every own-account is internal".
    if synced_accounts is None:
        internal_accounts = our_accounts
    else:
        batch_owners = set(result["account"].dropna().astype(str).str.strip())
        internal_accounts = {a for a in our_accounts if a in synced_accounts or a in batch_owners}
    result["is_internal"] = (
        result["counterparty_account"].apply(lambda a: bool(a and str(a).strip() in internal_accounts)).astype(int)
    )

    # ── is_fx ──
    result["is_fx"] = result["purpose"].apply(lambda p: bool(p and RE_FX.search(str(p)))).astype(int)

    # ── CUSTOMS_PAYMENT ──
    result["is_customs"] = (
        result["counterparty_account"].apply(lambda a: bool(a and str(a).strip() in customs_payee_accounts)).astype(int)
    )

    # ── event_type2 (priority: internal > fx > customs > oper) ──
    def _event_type2(row):
        if row["is_internal"]:
            return "INTERNAL_TRANSFER"
        if row["is_fx"]:
            return "FX_BUY"
        if row["is_customs"]:
            return "CUSTOMS_PAYMENT"
        return "OPER"

    result["event_type2"] = result.apply(_event_type2, axis=1)

    # ── is_cashflow2 ──
    result["is_cashflow2"] = result["event_type2"].apply(lambda e: 0 if e in ("INTERNAL_TRANSFER", "FX_BUY") else 1)

    # ── SRC_IMP enrichment ──
    result["purpose_tag"] = result["purpose"].apply(get_purpose_tag)
    result["invoice_id"] = result["purpose"].apply(extract_invoice_id)
    result["annex_id"] = result["purpose"].apply(extract_annex_id)

    # ── Phase 2 purpose enrichment (contract_number, unk, loans, deposits) ──
    # enrich_purpose() was defined and unit-tested but never wired here, so
    # contract_number / unk_number / loan_payment_type stayed permanently NULL
    # on every imported row (persist_df reads them from this df). Wire it now.
    enr = result["purpose"].apply(enrich_purpose)
    result["contract_number"] = enr.apply(lambda e: e["contract_number"])
    result["unk_number"] = enr.apply(lambda e: e["unk_number"])
    result["loan_payment_type"] = enr.apply(lambda e: e["loan_payment_type"])

    # Deposit override: a deposit between our own accounts is already
    # INTERNAL_TRANSFER; this only re-classifies deposits whose counter-account
    # is not registered as ours (still OPER) so they stop counting as cashflow.
    ev_override = enr.apply(lambda e: e["event_type2_override"])
    cf_override = enr.apply(lambda e: e["is_cashflow2_override"])
    dep_mask = ev_override.notna() & (result["event_type2"] == "OPER")
    if dep_mask.any():
        result.loc[dep_mask, "event_type2"] = ev_override[dep_mask]
        result.loc[dep_mask, "is_cashflow2"] = cf_override[dep_mask].astype(int)

    # ── Categories (priority: override > cp_category > empty) ──
    def _get_categories(row):
        txn_id = row["txn_id"]
        cp_key = row["cp_key"]

        if txn_id in overrides:
            ov = overrides[txn_id]
            return ov.get("cat_lvl1"), ov.get("cat_lvl2")
        if cp_key and cp_key in cp_categories:
            cp = cp_categories[cp_key]
            return cp.get("cat_lvl1"), cp.get("cat_lvl2")
        return None, None

    cats = result.apply(_get_categories, axis=1, result_type="expand")
    result["cat_lvl1_2"] = cats[0]
    result["cat_lvl2_2"] = cats[1]

    # ── status ──
    def _status(row):
        if row["is_cashflow2"] == 0:
            return "NO_CASHFLOW"
        if not row["cat_lvl1_2"]:
            return "UNASSIGNED"
        return "OK"

    result["status"] = result.apply(_status, axis=1)
    result["account_text"] = result["account"]

    # Drop helper column
    result = result.drop(columns=["is_customs"], errors="ignore")

    return result
