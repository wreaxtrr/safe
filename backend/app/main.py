import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import logic
from app.auth import get_current_user, require_admin
from app.db import Base, SessionLocal, engine, get_db
from app.models import Deal, Event, User, utcnow
from app.schemas import (CodeIn, DealCreate, DealOut, EventOut, ResolveIn,
                         UserCreate, UserOut, UserWithToken)

Base.metadata.create_all(bind=engine)

INDEX_HTML = Path(__file__).resolve().parents[2] / "index.html"


def run_expiry():
    try:
        with SessionLocal() as db:
            logic.expire_overdue(db)
            db.commit()
    except Exception as e:
        print("Ошибка автовозврата:", e)


async def expiry_loop():
    while True:
        await asyncio.to_thread(run_expiry)
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(expiry_loop())
    yield
    task.cancel()


app = FastAPI(title="Сейф API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def apply(db: Session, action):
    """Выполнить действие со сделкой: всё сохранить или, при ошибке, всё откатить."""
    try:
        result = action()
        db.commit()
        return result
    except logic.DealError as e:
        db.rollback()
        raise HTTPException(e.status_code, e.message)


@app.get("/")
def index():
    return FileResponse(INDEX_HTML)


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


# ---- пользователи ----

@app.post("/users", response_model=UserWithToken)
def register(data: UserCreate, db: Session = Depends(get_db)):
    user = User(name=data.name, token=secrets.token_hex(16))
    db.add(user)
    db.commit()
    return user


@app.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


# ---- сделки ----

@app.post("/deals", response_model=DealOut)
def create_deal(data: DealCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return apply(db, lambda: logic.create_deal(db, user, data.item, data.price, data.hours))


@app.get("/deals/{deal_id}", response_model=DealOut)
def get_deal(deal_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return apply(db, lambda: logic.get_deal(db, deal_id))


@app.get("/deals/{deal_id}/code")
def get_code(deal_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    def action():
        deal = logic.get_deal(db, deal_id)
        logic.require_buyer(deal, user)
        logic.check_status(deal, "reserved")
        return {"code": deal.code}
    return apply(db, action)


@app.post("/deals/{deal_id}/reserve", response_model=DealOut)
def reserve(deal_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    def action():
        deal = logic.get_deal(db, deal_id, lock=True)
        logic.reserve(db, deal, user)
        return deal
    return apply(db, action)


@app.post("/deals/{deal_id}/confirm", response_model=DealOut)
def confirm(deal_id: str, data: CodeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    def action():
        deal = logic.get_deal(db, deal_id, lock=True)
        return deal, logic.confirm(db, deal, user, data.code)

    deal, error = apply(db, action)
    if error:
        raise HTTPException(400, error)  # неудачная попытка уже сохранена
    return deal


@app.post("/deals/{deal_id}/refuse", response_model=DealOut)
def refuse(deal_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    def action():
        deal = logic.get_deal(db, deal_id, lock=True)
        logic.refuse(db, deal, user)
        return deal
    return apply(db, action)


@app.post("/deals/{deal_id}/accept-refusal", response_model=DealOut)
def accept_refusal(deal_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    def action():
        deal = logic.get_deal(db, deal_id, lock=True)
        logic.accept_refusal(db, deal, user)
        return deal
    return apply(db, action)


@app.post("/deals/{deal_id}/dispute", response_model=DealOut)
def dispute(deal_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    def action():
        deal = logic.get_deal(db, deal_id, lock=True)
        logic.open_dispute(db, deal, user)
        return deal
    return apply(db, action)


@app.post("/deals/{deal_id}/cancel", response_model=DealOut)
def cancel(deal_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    def action():
        deal = logic.get_deal(db, deal_id, lock=True)
        logic.cancel(db, deal, user)
        return deal
    return apply(db, action)


@app.get("/deals/{deal_id}/events", response_model=list[EventOut])
def deal_events(deal_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    deal = apply(db, lambda: logic.get_deal(db, deal_id))
    return db.scalars(select(Event).where(Event.deal_id == deal.id).order_by(Event.id)).all()


# ---- для банка и демо (нужен заголовок x-admin-key) ----

@app.post("/admin/deals/{deal_id}/resolve", response_model=DealOut, dependencies=[Depends(require_admin)])
def resolve(deal_id: str, data: ResolveIn, db: Session = Depends(get_db)):
    def action():
        deal = logic.get_deal(db, deal_id, lock=True)
        logic.resolve(db, deal, data.to_buyer)
        return deal
    return apply(db, action)


@app.post("/admin/deals/{deal_id}/expire-now", response_model=DealOut, dependencies=[Depends(require_admin)])
def expire_now(deal_id: str, db: Session = Depends(get_db)):
    """Для демо: сдвинуть срок сделки в прошлое и сразу запустить автовозврат."""
    def action():
        deal = logic.get_deal(db, deal_id, lock=True)
        logic.check_status(deal, "reserved", "refusal")
        deal.expires_at = utcnow() - timedelta(seconds=1)
        logic.expire_overdue(db)
        return deal
    return apply(db, action)


@app.get("/admin/metrics", dependencies=[Depends(require_admin)])
def metrics(db: Session = Depends(get_db)):
    def count(type_):
        return db.scalar(select(func.count()).select_from(Event).where(Event.type == type_))

    created = count("created")
    reserved = count("reserved")
    done = count("released") + count("dispute_seller")
    disputes = count("dispute") + count("dispute_auto")
    refunds = count("refund_agreed") + count("dispute_buyer") + count("expired")
    volume = db.scalar(select(func.coalesce(func.sum(Event.amount), 0)).where(Event.type.in_(["released", "dispute_seller"])))

    def share(a, b):
        return round(a / b, 3) if b else None

    return {
        "deals_created": created,
        "deals_reserved": reserved,
        "deals_done": done,
        "done_volume_rub": volume,
        "conversion_created_to_reserved": share(reserved, created),
        "conversion_reserved_to_done": share(done, reserved),
        "dispute_share": share(disputes, reserved),
        "refund_share": share(refunds, reserved),
    }
