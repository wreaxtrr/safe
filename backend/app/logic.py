import secrets
import string
from datetime import timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Deal, Event, User, utcnow

MAX_CODE_TRIES = 5


class DealError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message


def as_utc(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def log(db: Session, deal: Deal, type_: str, user: User | None = None, amount: int | None = None):
    db.add(Event(deal_id=deal.id, user_id=user.id if user else None, type=type_, amount=amount))


def new_deal_id():
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


def get_deal(db: Session, deal_id: str, lock: bool = False) -> Deal:
    query = select(Deal).where(Deal.id == deal_id)
    if lock:
        query = query.with_for_update()  # чтобы два запроса не меняли сделку одновременно
    deal = db.scalars(query).first()
    if not deal:
        raise DealError(404, "Сделка не найдена")
    return deal


def check_status(deal: Deal, *allowed: str):
    if deal.status not in allowed:
        raise DealError(409, f"Действие недоступно, статус сделки: {deal.status}")


def require_seller(deal: Deal, user: User):
    if user.id != deal.seller_id:
        raise DealError(403, "Это может сделать только продавец")


def require_buyer(deal: Deal, user: User):
    if user.id != deal.buyer_id:
        raise DealError(403, "Это может сделать только покупатель")


def pay_seller(db: Session, deal: Deal, type_: str):
    seller = db.get(User, deal.seller_id)
    seller.balance += deal.price
    deal.status = "done"
    log(db, deal, type_, amount=deal.price)


def refund(db: Session, deal: Deal, type_: str):
    buyer = db.get(User, deal.buyer_id)
    buyer.balance += deal.price
    deal.status = "refunded"
    log(db, deal, type_, amount=deal.price)


# ---- действия со сделкой ----

def create_deal(db: Session, seller: User, item: str, price: int, hours: int) -> Deal:
    deal = Deal(id=new_deal_id(), item=item, price=price, hours=hours, seller_id=seller.id)
    db.add(deal)
    db.flush()
    log(db, deal, "created", seller, price)
    return deal


def cancel(db: Session, deal: Deal, user: User):
    require_seller(deal, user)
    check_status(deal, "created")
    deal.status = "cancelled"
    log(db, deal, "cancelled", user)


def reserve(db: Session, deal: Deal, buyer: User):
    check_status(deal, "created")
    if buyer.id == deal.seller_id:
        raise DealError(403, "Нельзя купить свой же товар")
    if buyer.balance < deal.price:
        raise DealError(400, "Недостаточно цифровых рублей")

    buyer.balance -= deal.price
    deal.buyer_id = buyer.id
    deal.code = f"{secrets.randbelow(10**6):06d}"
    deal.expires_at = utcnow() + timedelta(hours=deal.hours)  # срок идёт с момента резерва
    deal.status = "reserved"
    log(db, deal, "reserved", buyer, deal.price)


def confirm(db: Session, deal: Deal, seller: User, code: str) -> str | None:
    """Возвращает текст ошибки, если код неверный. Попытку всё равно сохраняем."""
    require_seller(deal, seller)
    check_status(deal, "reserved")

    if code != deal.code:
        deal.code_tries += 1
        log(db, deal, "wrong_code", seller)
        # иначе продавец мог бы перебрать код и забрать деньги без передачи товара
        if deal.code_tries >= MAX_CODE_TRIES:
            deal.status = "dispute"
            log(db, deal, "dispute_auto", seller)
            return "Слишком много неверных попыток, сделка передана в спор"
        return f"Неверный код, осталось попыток: {MAX_CODE_TRIES - deal.code_tries}"

    pay_seller(db, deal, "released")
    return None


def refuse(db: Session, deal: Deal, buyer: User):
    require_buyer(deal, buyer)
    check_status(deal, "reserved")
    deal.status = "refusal"
    log(db, deal, "refused", buyer)


def accept_refusal(db: Session, deal: Deal, seller: User):
    require_seller(deal, seller)
    check_status(deal, "refusal")
    refund(db, deal, "refund_agreed")


def open_dispute(db: Session, deal: Deal, seller: User):
    require_seller(deal, seller)
    check_status(deal, "refusal")
    deal.status = "dispute"
    log(db, deal, "dispute", seller)


def resolve(db: Session, deal: Deal, to_buyer: bool):
    check_status(deal, "dispute")
    if to_buyer:
        refund(db, deal, "dispute_buyer")
    else:
        pay_seller(db, deal, "dispute_seller")


def expire_overdue(db: Session) -> int:
    """Автовозврат: если код так и не ввели до срока, деньги возвращаются покупателю."""
    now = utcnow()
    deals = db.scalars(select(Deal).where(Deal.status.in_(["reserved", "refusal"]))).all()
    count = 0
    for deal in deals:
        if deal.expires_at and as_utc(deal.expires_at) <= now:
            refund(db, deal, "expired")
            count += 1
    return count
