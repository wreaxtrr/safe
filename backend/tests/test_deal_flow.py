import os

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["ADMIN_KEY"] = "test-key"
if os.path.exists("test.db"):
    os.remove("test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)
ADMIN = {"x-admin-key": "test-key"}


def new_user(name):
    token = client.post("/users", json={"name": name}).json()["token"]
    return {"x-token": token}


def new_deal(seller, price=30000):
    r = client.post("/deals", json={"item": "PlayStation 5", "price": price, "hours": 24}, headers=seller)
    assert r.status_code == 200
    return r.json()["id"]


def balance(user):
    return client.get("/me", headers=user).json()["balance"]


def test_index_page():
    r = client.get("/")
    assert r.status_code == 200
    assert "Сейф" in r.text


def test_normal_deal():
    seller, buyer = new_user("Продавец"), new_user("Покупатель")
    deal = new_deal(seller)

    assert client.post(f"/deals/{deal}/reserve", headers=buyer).json()["status"] == "reserved"
    assert balance(buyer) == 20000

    code = client.get(f"/deals/{deal}/code", headers=buyer).json()["code"]
    r = client.post(f"/deals/{deal}/confirm", json={"code": code}, headers=seller)
    assert r.json()["status"] == "done"
    assert balance(seller) == 80000


def test_seller_cannot_see_code_or_confirm_without_reserve():
    seller, buyer = new_user("П"), new_user("К")
    deal = new_deal(seller)
    assert client.post(f"/deals/{deal}/confirm", json={"code": "123456"}, headers=seller).status_code == 409
    client.post(f"/deals/{deal}/reserve", headers=buyer)
    assert client.get(f"/deals/{deal}/code", headers=seller).status_code == 403


def test_buyer_cannot_confirm_for_seller():
    seller, buyer = new_user("П"), new_user("К")
    deal = new_deal(seller)
    client.post(f"/deals/{deal}/reserve", headers=buyer)
    code = client.get(f"/deals/{deal}/code", headers=buyer).json()["code"]
    assert client.post(f"/deals/{deal}/confirm", json={"code": code}, headers=buyer).status_code == 403


def test_wrong_code_limit_goes_to_dispute():
    seller, buyer = new_user("П"), new_user("К")
    deal = new_deal(seller)
    client.post(f"/deals/{deal}/reserve", headers=buyer)
    real = client.get(f"/deals/{deal}/code", headers=buyer).json()["code"]
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(5):
        r = client.post(f"/deals/{deal}/confirm", json={"code": wrong}, headers=seller)
        assert r.status_code == 400
    assert client.get(f"/deals/{deal}", headers=seller).json()["status"] == "dispute"


def test_auto_refund():
    seller, buyer = new_user("П"), new_user("К")
    deal = new_deal(seller)
    client.post(f"/deals/{deal}/reserve", headers=buyer)
    r = client.post(f"/admin/deals/{deal}/expire-now", headers=ADMIN)
    assert r.json()["status"] == "refunded"
    assert balance(buyer) == 50000


def test_refusal_and_dispute():
    seller, buyer = new_user("П"), new_user("К")
    deal = new_deal(seller)
    client.post(f"/deals/{deal}/reserve", headers=buyer)
    client.post(f"/deals/{deal}/refuse", headers=buyer)
    assert client.post(f"/deals/{deal}/dispute", headers=seller).json()["status"] == "dispute"
    r = client.post(f"/admin/deals/{deal}/resolve", json={"to_buyer": True}, headers=ADMIN)
    assert r.json()["status"] == "refunded"
    assert balance(buyer) == 50000


def test_not_enough_money():
    seller, buyer = new_user("П"), new_user("К")
    deal = new_deal(seller, price=60000)
    assert client.post(f"/deals/{deal}/reserve", headers=buyer).status_code == 400


def test_metrics():
    r = client.get("/admin/metrics", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["deals_created"] > 0
