#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations


# AL EJECUTAR EL SCRIPT, DEBERAS INGRESAR EL LINK DE LA RONDA
import os
import sys
import re
import json
import threading
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV_PATH = os.path.join(_THIS_DIR, ".env")

CHAIN_ID  = 137
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"

RESET  = "\033[0m"
BOLD   = "\033[1m"
CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BLUE   = "\033[94m"
DIM    = "\033[2m"


def info(msg: str)  -> None: print(f"{CYAN}[i] {msg}{RESET}")
def ok(msg: str)    -> None: print(f"{GREEN}[ok] {msg}{RESET}")
def warn(msg: str)  -> None: print(f"{YELLOW}[!] {msg}{RESET}")
def err(msg: str)   -> None: print(f"{RED}[x] {msg}{RESET}")


def section(title: str) -> None:
    line = "-" * 52
    print(f"\n{BLUE}{BOLD}{line}\n  {title}\n{line}{RESET}")


def hora_cdmx() -> None:
    if ZoneInfo is None:
        return
    try:
        dt = datetime.now(ZoneInfo("America/El_Salvador"))
        print(f"{CYAN}[i] {dt.strftime('%Y-%m-%d %H:%M:%S')} {DIM}({dt.tzname()}){RESET}")
    except Exception:
        pass


def extract_slug(url: str) -> str:
    raw = url.strip()
    if not raw.startswith("http"):
        return raw.split("/")[-1].split("?")[0]
    parts = [p for p in urlparse(raw).path.split("/") if p]
    for i, part in enumerate(parts):
        if part == "event" and i + 1 < len(parts):
            return parts[i + 1]
    if len(parts) == 1:
        return parts[0]
    raise ValueError(f"No se pudo extraer slug de: {url}")


def interval_from_slug(slug: str) -> tuple[str, int]:
    m = re.search(r"btc-updown-(\d+)m-", slug.strip(), flags=re.IGNORECASE)
    if m:
        mins = int(m.group(1))
        return f"{mins}m", mins * 60
    return "?", 0


def fetch_event(slug: str) -> dict[str, Any]:
    r = requests.get(f"{GAMMA_API}/events", params={"slug": slug}, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise ValueError(f"Sin evento para slug: {slug}")
    return data[0]


def _parse_json(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


def _f(x: Any) -> Optional[float]:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def get_tokens(event: dict) -> tuple[list[str], list[str]]:
    for market in (event.get("markets") or []):
        if market.get("closed") or not market.get("active", True):
            continue
        tokens = market.get("tokens") or []
        if isinstance(tokens, list) and tokens:
            labels, ids = [], []
            for t in tokens[:2]:
                if isinstance(t, dict):
                    labels.append(str(t.get("outcome", "?")))
                    ids.append(str(t.get("token_id") or t.get("tokenId") or ""))
            if len(ids) >= 2:
                return labels, ids
        clob     = _parse_json(market.get("clobTokenIds", []))
        outcomes = _parse_json(market.get("outcomes", []))
        if isinstance(clob, list) and len(clob) >= 2:
            labels = [str(outcomes[i]) if isinstance(outcomes, list) and i < len(outcomes) else f"[{i}]" for i in range(2)]
            return labels, [str(clob[0]), str(clob[1])]
    raise ValueError("No se encontro mercado binario con tokens.")


def get_bid(token_id: str) -> Optional[float]:
    try:
        r = requests.get(f"{CLOB_HOST}/price", params={"token_id": token_id, "side": "sell"}, timeout=15)
        if r.status_code != 200:
            return None
        return _f(r.json().get("price"))
    except Exception:
        return None


def get_ask(token_id: str) -> Optional[float]:
    try:
        r = requests.get(f"{CLOB_HOST}/price", params={"token_id": token_id, "side": "buy"}, timeout=15)
        if r.status_code != 200:
            return None
        return _f(r.json().get("price"))
    except Exception:
        return None


def load_env(slug: str) -> tuple[dict, dict]:
    if load_dotenv is None:
        raise RuntimeError("pip install python-dotenv")
    if not os.path.exists(_ENV_PATH):
        raise FileNotFoundError(f"No se encontro .env en: {_ENV_PATH}")
    load_dotenv(dotenv_path=_ENV_PATH, override=True)

    name  = os.getenv("WALLET_1_NAME", "Wallet 1")
    pk    = (os.getenv("WALLET_1_PRIVATE_KEY",   "") or "").strip()
    proxy = (os.getenv("WALLET_1_PROXY_ADDRESS", "") or "").strip()
    sig   = int(os.getenv("WALLET_1_SIGNATURE_TYPE", "1") or "1")

    if pk.startswith("0x") and proxy.startswith("0x") and len(pk) == 42 and len(proxy) == 66:
        pk, proxy = proxy, pk

    if not pk or len(pk) != 66:
        raise ValueError(f"WALLET_1_PRIVATE_KEY invalida (len={len(pk)}, necesita 66 chars)")
    if not proxy or len(proxy) != 42:
        raise ValueError(f"WALLET_1_PROXY_ADDRESS invalida (len={len(proxy)}, necesita 42 chars)")

    wallet = {"name": name, "private_key": pk, "proxy": proxy, "sig_type": sig}

    label, _sec = interval_from_slug(slug)
    use_15 = label == "15m"

    def _getf(base: str, default: float) -> float:
        if use_15:
            ov = os.getenv(f"{base}_15M")
            if ov is not None and str(ov).strip() != "":
                return float(ov)
        return float(os.getenv(base, str(default)) or default)

    cfg = {
        "trigger_up_bid":   _getf("TRIGGER_UP_BID",   0.51),
        "trigger_down_bid": _getf("TRIGGER_DOWN_BID",  0.50),
        "limit_up":         _getf("LIMIT_UP_PRICE",    0.50),
        "limit_down":       _getf("LIMIT_DOWN_PRICE",  0.48),
        "shares_up":        _getf("SHARES_UP",         5.0),
        "shares_down":      _getf("SHARES_DOWN",       5.0),
    }
    return wallet, cfg


def build_client(wallet: dict, sig_type: int):
    from py_clob_client.client import ClobClient
    temp  = ClobClient(host=CLOB_HOST, chain_id=CHAIN_ID, key=wallet["private_key"], signature_type=sig_type, funder=wallet["proxy"])
    creds = temp.create_or_derive_api_creds()
    return ClobClient(host=CLOB_HOST, chain_id=CHAIN_ID, key=wallet["private_key"], creds=creds, signature_type=sig_type, funder=wallet["proxy"])


def place_limit(wallet: dict, token_id: str, limit_price: float, shares: float, label: str, results: dict) -> None:
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY

    candidates = [wallet["sig_type"], 2 if wallet["sig_type"] == 1 else 1]
    last_exc: Optional[Exception] = None

    for st in candidates:
        try:
            client = build_client(wallet, st)
            signed = client.create_order(OrderArgs(token_id=token_id, price=limit_price, size=shares, side=BUY))
            resp   = client.post_order(signed, OrderType.GTC)
            results[label] = {"ok": True, "id": resp.get("orderID") or resp.get("order_id") or "?", "status": resp.get("status", "submitted")}
            return
        except Exception as e:
            last_exc = e
            if "invalid signature" in str(e).lower():
                continue
            break

    results[label] = {"ok": False, "error": str(last_exc)}


def execute_hedge_parallel(wallet: dict, cfg: dict, token_ids: list[str], labels: list[str]) -> None:
    section("EJECUTANDO HEDGE")

    idx_up   = next((i for i, l in enumerate(labels) if "up"   in l.lower()), 0)
    idx_down = next((i for i, l in enumerate(labels) if "down" in l.lower()), 1)

    results: dict = {}

    t_up   = threading.Thread(target=place_limit, args=(wallet, token_ids[idx_up],   cfg["limit_up"],   cfg["shares_up"],   labels[idx_up],   results))
    t_down = threading.Thread(target=place_limit, args=(wallet, token_ids[idx_down], cfg["limit_down"], cfg["shares_down"], labels[idx_down], results))

    print(f"  {BOLD}{labels[idx_up]:<8}{RESET}  Limit {cfg['limit_up']*100:.0f}c  x {cfg['shares_up']:.0f} shares")
    print(f"  {BOLD}{labels[idx_down]:<8}{RESET}  Limit {cfg['limit_down']*100:.0f}c  x {cfg['shares_down']:.0f} shares")
    print()

    t_up.start()
    t_down.start()
    t_up.join()
    t_down.join()

    for label, r in results.items():
        if r["ok"]:
            ok(f"  {label:<8} ID: {CYAN}{r['id']}{RESET}  Estado: {GREEN}{r['status']}{RESET}")
        else:
            err(f"  {label:<8} Error: {r['error']}")

    total_cost = cfg["limit_up"] + cfg["limit_down"]
    margen     = 1.0 - total_cost
    col        = GREEN if margen > 0 else RED
    print(f"\n  {col}{BOLD}Margen garantizado: {margen*100:+.1f}c por share{RESET}")


def run(url: str) -> None:
    hora_cdmx()

    slug = extract_slug(url)
    iv_label, iv_sec = interval_from_slug(slug)
    info(f"Slug: {slug}")
    if iv_label != "?":
        info(f"Mercado: BTC Up/Down {iv_label}  (ronda ~{iv_sec // 60} min / {iv_sec}s)")
    else:
        info("Mercado: intervalo no reconocido en slug (usa btc-updown-5m-... o btc-updown-15m-...)")
    info(f"URL:  https://polymarket.com/event/{slug}")

    try:
        event = fetch_event(slug)
    except Exception as e:
        err(f"Gamma API: {e}")
        return

    ok(f"Evento: {event.get('title', slug)}")

    try:
        labels, token_ids = get_tokens(event)
    except ValueError as e:
        err(str(e))
        return

    idx_up   = next((i for i, l in enumerate(labels) if "up"   in l.lower()), 0)
    idx_down = next((i for i, l in enumerate(labels) if "down" in l.lower()), 1)

    section("LIBRO DE ORDENES")

    bid_up   = get_bid(token_ids[idx_up])
    bid_down = get_bid(token_ids[idx_down])
    ask_up   = get_ask(token_ids[idx_up])
    ask_down = get_ask(token_ids[idx_down])

    def fmt(v: Optional[float]) -> str:
        return f"{v*100:.1f}c" if v is not None else "--"

    print(f"  {'Outcome':<10}  {'BID (venta)':<14}  {'ASK (compra)'}")
    print(f"  {'-'*10}  {'-'*14}  {'-'*12}")
    print(f"  {BOLD}{GREEN}{labels[idx_up]:<10}{RESET}  {fmt(bid_up):<14}  {fmt(ask_up)}")
    print(f"  {BOLD}{RED}{labels[idx_down]:<10}{RESET}  {fmt(bid_down):<14}  {fmt(ask_down)}")

    try:
        wallet, cfg = load_env(slug)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        err(str(e))
        return

    section("VERIFICANDO CONDICION")
    print(f"  Trigger  Up  bid >= {cfg['trigger_up_bid']*100:.0f}c  ->  actual: {fmt(bid_up)}")
    print(f"  Trigger  Down bid >= {cfg['trigger_down_bid']*100:.0f}c  ->  actual: {fmt(bid_down)}")

    if bid_up is None or bid_down is None:
        warn("No se pudieron obtener bids del libro. Sin apuesta.")
        return

    if bid_up >= cfg["trigger_up_bid"] and bid_down >= cfg["trigger_down_bid"]:
        ok(f"Condicion CUMPLIDA")
        execute_hedge_parallel(wallet, cfg, token_ids, labels)
    else:
        warn("Condicion NO cumplida. Sin apuesta.")
        if bid_up < cfg["trigger_up_bid"]:
            warn(f"  Up bid {fmt(bid_up)} < {cfg['trigger_up_bid']*100:.0f}c requerido")
        if bid_down < cfg["trigger_down_bid"]:
            warn(f"  Down bid {fmt(bid_down)} < {cfg['trigger_down_bid']*100:.0f}c requerido")


def main() -> None:
    argv = [a for a in sys.argv[1:] if a.strip()]
    raw  = " ".join(argv).strip()

    if not raw:
        raw = input("Pega el link o slug del evento:\n> ").strip()

    if not raw:
        err("Sin entrada.")
        sys.exit(1)

    run(raw)


if __name__ == "__main__":
    main()
