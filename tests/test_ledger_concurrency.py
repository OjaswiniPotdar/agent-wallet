# Budget reservation under concurrency, failure and restart. Real threads and real processes.
import multiprocessing as mp
import sqlite3, threading
from decimal import Decimal
import pytest
from approval import ApprovalStore
from executor import execute_purchase, PaymentUnknown
from ledger import Ledger, SqliteLedger
from policy import Policy

SHOP = "http://localhost:8001"
P = Policy()            # $500 budget, $150 cap, approval above $75

def buy(ledger, amount=Decimal("50.00"), pid="pens", pay=None):
    return execute_purchase(ledger=ledger, store=ApprovalStore(), policy=P, url=SHOP, amount=amount, currency="USD",
                            product_id=pid, price_lookup=lambda p: amount, pay=pay or (lambda a, d: "ORDER"))

def hammer(ledger, n=40):
    """n threads each try a $50 purchase with distinct items (so R5/R7 never fire): only 10 fit in $500."""
    out, start = [], threading.Barrier(n)
    def one(i):
        start.wait()
        out.append(execute_purchase(ledger=ledger, store=ApprovalStore(), policy=Policy(per_purchase_cap=Decimal("10000")),
                                    url=f"http://localhost:8001", amount=Decimal("50.00"), currency="USD", product_id=f"item{i}",
                                    price_lookup=lambda p: Decimal("50.00"), pay=lambda a, d: "ORDER").status)
    ts = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    [t.start() for t in ts]; [t.join() for t in ts]
    return out

@pytest.mark.parametrize("make", [lambda tmp: Ledger(), lambda tmp: SqliteLedger(str(tmp / "l.db"))], ids=["memory", "sqlite"])
def test_parallel_purchases_never_exceed_budget(tmp_path, make):
    ledger = make(tmp_path)
    statuses = hammer(ledger)
    assert statuses.count("PAID") == 10 and ledger.spent == Decimal("500.00")
    assert all(s in ("PAID", "BLOCKED") for s in statuses)

def test_failed_payment_releases_budget(tmp_path):
    ledger = SqliteLedger(str(tmp_path / "l.db"))
    def boom(a, d): raise RuntimeError("card declined")
    assert buy(ledger, pay=boom).status == "PAYMENT_FAILED"
    assert ledger.spent == 0 and buy(ledger).status == "PAID" and ledger.spent == Decimal("50.00")

def test_unknown_payment_keeps_reservation_across_restart(tmp_path):
    path = str(tmp_path / "l.db")
    def timeout(a, d): raise PaymentUnknown("read timeout")
    assert buy(SqliteLedger(path), amount=Decimal("60.00"), pay=timeout).status == "PAYMENT_UNKNOWN"
    restarted = SqliteLedger(path)                           # process restarts: the money may have moved
    assert restarted.spent == Decimal("60.00")
    assert [r["state"] for r in restarted.reservations()] == ["reserved"]

def test_budget_survives_restart(tmp_path):
    """Budget $300; R5 split rule switched off via a high cap so this measures R4 only."""
    path, pol = str(tmp_path / "l.db"), Policy(budget_total=Decimal("300"), per_purchase_cap=Decimal("10000"))
    def go(led, pid):
        return execute_purchase(ledger=led, store=ApprovalStore(), policy=pol, url=SHOP, amount=Decimal("70.00"),
                                currency="USD", product_id=pid, price_lookup=lambda p: Decimal("70.00"), pay=lambda a, d: "ORDER")
    l1 = SqliteLedger(path)
    assert [go(l1, f"x{i}").status for i in range(3)] == ["PAID"] * 3
    l2 = SqliteLedger(path)                                   # restart
    assert l2.spent == Decimal("210.00")
    assert go(l2, "y").status == "PAID"                       # 280 <= 300
    r = go(l2, "z")                                           # 350 > 300
    assert r.status == "BLOCKED" and "R4_BUDGET" in r.detail and l2.spent == Decimal("280.00")

def test_accounts_are_separate_budgets(tmp_path):
    path = str(tmp_path / "l.db")
    a, b = SqliteLedger(path, "alice"), SqliteLedger(path, "bob")
    buy(a, amount=Decimal("70.00"))
    assert a.spent == Decimal("70.00") and b.spent == 0

def test_release_undoes_one_reservation_only(tmp_path):
    ledger = SqliteLedger(str(tmp_path / "l.db"))
    for _ in range(2):
        ledger.reserve(Policy(), SHOP, Decimal("20.00"), approved=False, item="pens")
    ledger.release(SHOP, Decimal("20.00"), "pens")
    assert ledger.spent == Decimal("20.00")

def test_reservation_rolls_back_on_error(tmp_path, monkeypatch):
    import ledger as L
    led = SqliteLedger(str(tmp_path / "l.db"))
    monkeypatch.setattr(L, "decide", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    with pytest.raises(ValueError):
        led.reserve(Policy(), SHOP, Decimal("10.00"), approved=False, item="x")
    monkeypatch.undo()
    assert led.spent == 0 and led.reserve(Policy(), SHOP, Decimal("10.00"), approved=False, item="x")[1]   # not locked

def _proc_worker(path, i, q):
    led = SqliteLedger(path)
    _, ok = led.reserve(Policy(per_purchase_cap=Decimal("10000")), SHOP, Decimal("50.00"), approved=False, item=f"p{i}")
    q.put(ok)

def test_separate_processes_share_one_budget(tmp_path):
    path = str(tmp_path / "l.db")
    SqliteLedger(path)
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    procs = [ctx.Process(target=_proc_worker, args=(path, i, q)) for i in range(16)]
    [p.start() for p in procs]; [p.join(60) for p in procs]
    oks = [q.get(timeout=10) for _ in procs]
    assert oks.count(True) == 10 and SqliteLedger(path).spent == Decimal("500.00")
