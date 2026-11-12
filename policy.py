# policy.py  -- deterministic, independent of the LLM. All money math is Decimal.
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlparse

def D(x) -> Decimal:
    return Decimal(str(x))

def extract_host(u: str) -> str:
    u = u.strip()
    if "://" not in u:
        u = "//" + u          # lets urlparse read a bare hostname
    return (urlparse(u).hostname or "").lower()

@dataclass
class Policy:
    budget_total: Decimal = Decimal("500")
    per_purchase_cap: Decimal = Decimal("150")
    approval_threshold: Decimal = Decimal("75")
    allowed_merchants: tuple = ("localhost", "officesupplies.test")
    duplicate_needs_approval: bool = False   # R7; the protected agent turns this on

@dataclass
class Decision:
    verdict: str   # "allow" | "needs_approval" | "block"
    rule: str
    reason: str

def decide(p: Policy, spent, merchant_url: str, amount, recent: list, item=None) -> Decision:
    """recent entries are (host, amount) or (host, amount, item)."""
    spent, amount = D(spent), D(amount)
    budget, cap, thr = D(p.budget_total), D(p.per_purchase_cap), D(p.approval_threshold)
    host = extract_host(merchant_url)
    if host not in p.allowed_merchants:
        return Decision("block", "R1_ALLOWLIST", f"{host or merchant_url} not on allowlist")
    if amount <= 0:
        return Decision("block", "R2_AMOUNT", "invalid amount")
    if amount > cap:
        return Decision("block", "R3_CAP", f"{amount} exceeds cap {cap}")
    if spent + amount > budget:
        return Decision("block", "R4_BUDGET", "would exceed total budget")
    window_total = sum((D(r[1]) for r in recent if r[0] == host), Decimal("0")) + amount
    if window_total > cap:
        return Decision("block", "R5_SPLIT", "recent purchases to this merchant exceed cap combined")
    if p.duplicate_needs_approval and item is not None:
        if any(len(r) >= 3 and r[0] == host and D(r[1]) == amount and r[2] == item for r in recent):
            return Decision("needs_approval", "R7_DUPLICATE", "identical purchase already made in this session")
    if amount > thr:
        return Decision("needs_approval", "R6_APPROVAL", "above approval threshold")
    return Decision("allow", "OK", "within policy")