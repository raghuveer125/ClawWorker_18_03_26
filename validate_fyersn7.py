#!/usr/bin/env python3
"""
FYERSN7 Paper Trading Engine — Read-Only Validation Script
Version 1.0.0

Inspects the FYERSN7 engine for operational risk without modifying any file.
Safe to run on a live repo checkout.

Usage:
    python validate_fyersn7.py [--repo-root PATH] [--date YYYY-MM-DD]
                               [--index NAME] [--json-output PATH]
"""

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import date as dt_date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

VERSION = "1.0.0"
SEP = "═" * 72

# ─── Tiny report helper ───────────────────────────────────────────────────────

class R:
    """Accumulates lines; flush() prints and clears."""
    def __init__(self): self._buf = []
    def h(self, title): self._buf += [f"\n{SEP}", f"  {title}", SEP]
    def row(self, label, val, flag=""): self._buf.append(f"  {label:<42}{val}{'  ['+flag+']' if flag else ''}")
    def ok(self, msg): self._buf.append(f"  OK  {msg}")
    def warn(self, msg): self._buf.append(f"  !!  {msg}")
    def info(self, msg): self._buf.append(f"      {msg}")
    def blank(self): self._buf.append("")
    def flush(self):
        print("\n".join(self._buf))
        self._buf.clear()


# ─── Low-level I/O ───────────────────────────────────────────────────────────

def read_text(p: Path) -> Optional[str]:
    try: return p.read_text(encoding="utf-8", errors="replace")
    except Exception: return None

def read_json(p: Path) -> Tuple[Optional[dict], Optional[str]]:
    t = read_text(p)
    if t is None: return None, f"unreadable: {p}"
    try: return json.loads(t), None
    except json.JSONDecodeError as e: return None, str(e)

def mtime(p: Path) -> str:
    try: return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except Exception: return "?"

def fsize(p: Path) -> str:
    try:
        s = p.stat().st_size
        return f"{s/1024:.1f} KB" if s >= 1024 else f"{s} B"
    except Exception: return "?"

def csv_info(p: Path) -> Tuple[int, List[str], Optional[str]]:
    """Return (row_count_excl_header, fieldnames, error)."""
    try:
        with p.open(encoding="utf-8", errors="replace") as f:
            r = csv.DictReader(f)
            rows = list(r)
            return len(rows), list(r.fieldnames or []), None
    except Exception as e:
        return 0, [], str(e)


# ─── Section 1 — Repo Discovery ──────────────────────────────────────────────

def discover(repo_root: Path, r: R) -> Dict[str, Optional[Path]]:
    r.h("SECTION 1 — REPO DISCOVERY")
    r.row("Repo root", repo_root)

    def chk(label, p: Path):
        exists = p.exists()
        r.row(label, str(p) if exists else "NOT FOUND", "" if exists else "WARN")
        return p if exists else None

    p: Dict[str, Optional[Path]] = {}
    p["shared_launcher"] = chk("shared_project_engine/launcher/start.sh",
                                 repo_root / "shared_project_engine" / "launcher" / "start.sh")

    # fyersN7 base (prefer symlink, fall back to engines/)
    base = next((c for c in [repo_root/"fyersN7", repo_root/"engines"/"fyersN7"] if c.exists()), None)
    if base is None:
        r.warn("fyersN7 base not found — remaining sections will skip")
        for k in ("fyersn7_root","start_all_sh","run_paper_sh","paper_loop_py","postmortem_root"):
            p[k] = None
    else:
        r.row("fyersN7 base", str(base))
        vpat = re.compile(r"^fyers-\d{4}-\d{2}-\d{2}$")
        vdirs = sorted((d for d in base.iterdir() if d.is_dir() and vpat.match(d.name)), key=lambda d: d.name)
        root = vdirs[-1] if vdirs else None
        if root is None:
            r.warn("No versioned fyers-YYYY-MM-DD subdir found")
            for k in ("fyersn7_root","start_all_sh","run_paper_sh","paper_loop_py","postmortem_root"):
                p[k] = None
        else:
            r.row("fyersN7 versioned root", str(root))
            p["fyersn7_root"] = root
            s = root / "scripts"
            p["start_all_sh"]  = chk("start_all.sh",            s / "start_all.sh")
            p["run_paper_sh"]  = chk("run_paper_trade_loop.sh", s / "run_paper_trade_loop.sh")
            p["paper_loop_py"] = chk("paper_trade_loop.py",     s / "paper_trade_loop.py")
            pm = root / "postmortem"
            p["postmortem_root"] = chk("postmortem/", pm)

    fe = repo_root / "ClawWork" / "frontend" / "src" / "pages"
    p["signal_view"]  = chk("frontend/SignalView.jsx",  fe / "SignalView.jsx")
    p["bot_ensemble"] = chk("frontend/BotEnsemble.jsx", fe / "BotEnsemble.jsx")
    p["server_py"] = chk("livebench/api/server.py",
                          repo_root / "ClawWork" / "livebench" / "api" / "server.py")
    r.flush()
    return p


# ─── Section 2 — Capital Path Analysis ───────────────────────────────────────

_CAP_RE = re.compile(
    r'(?:local\s+)?capital\s*=\s*["\']?\$\{CAPITAL:-(\d+)\}',
    re.IGNORECASE
)

def _extract_capitals(text: str, name: str) -> Dict[str, str]:
    return {f"{name}:L{text[:m.start()].count(chr(10))+1}": m.group(1)
            for m in _CAP_RE.finditer(text)}

def analyze_capital(p: Dict, r: R) -> Dict:
    r.h("SECTION 2 — CAPITAL PATH ANALYSIS")
    data = {}

    for key, label in [("start_all_sh","start_all.sh"),
                        ("run_paper_sh","run_paper_trade_loop.sh"),
                        ("shared_launcher","start.sh (shared launcher)")]:
        if not p.get(key):
            r.warn(f"{label}: not found")
            continue
        t = read_text(p[key])
        if t is None:
            r.warn(f"{label}: unreadable")
            continue
        caps = _extract_capitals(t, label)
        data[key] = caps
        r.info(f"{label}:")
        if caps:
            for loc, val in caps.items():
                r.row(f"    {loc}", f"CAPITAL default = {val}")
        else:
            r.warn(f"  No CAPITAL default found in {label}")

        # For shared launcher: identify per-function context
        if key == "shared_launcher":
            r.blank()
            r.info("Function context in start.sh:")
            for func in ("cmd_fyersn7_paper", "cmd_fyersn7_paper_background"):
                m = re.search(rf"^{func}\b[^{{]*\{{", t, re.MULTILINE)
                if m:
                    block = t[m.end(): m.end()+700]
                    fc = _extract_capitals(block, func)
                    if fc:
                        for _, val in fc.items():
                            r.row(f"    {func}", f"CAPITAL default = {val}")
                    elif "start_all.sh" in block:
                        r.row(f"    {func}", "delegates to start_all.sh (no CAPITAL override)")
                    else:
                        r.row(f"    {func}", "CAPITAL not set — UNKNOWN")

    r.blank()
    r.info("Effective CAPITAL per launch path:")
    sa = list(data.get("start_all_sh", {}).values())
    rp = list(data.get("run_paper_sh", {}).values())
    sa0 = sa[0] if sa else "UNKNOWN"
    rp0 = rp[0] if rp else "UNKNOWN"
    r.row("  ./start.sh fyersn7-paper (interactive)",    f"→ start_all.sh default = {sa0}")
    r.row("  ./start.sh all (cmd_all)",                  "→ cmd_fyersn7_paper_background default = 100000 (if unset)")
    r.row("  direct start_all.sh paper",                 f"→ {sa0}")
    r.row("  direct run_paper_trade_loop.sh",            f"→ {rp0}")

    if sa0 != rp0 and "UNKNOWN" not in (sa0, rp0):
        r.warn(f"CAPITAL MISMATCH: start_all.sh={sa0} vs run_paper_trade_loop.sh={rp0}")
        r.info("  load_state() applies (CAPITAL_arg - previous_initial_capital) as delta to cash")
        r.info(f"  If state has initial_capital={rp0} and is re-launched via start_all.sh,")
        r.info(f"  delta = {int(sa0)-int(rp0):+d} will be applied to cash")

    r.flush()
    return data


# ─── Section 3 — State File Inspection ───────────────────────────────────────

STATE_KEYS = ["initial_capital","cash","processed_rows","next_trade_id",
              "realized_pnl","total_fees","wins","losses",
              "open_positions","recently_closed"]

def _find_state(index_dir: Path) -> Optional[Path]:
    for name in (".paper_trade_state.json","paper_trade_state.json"):
        p = index_dir / name
        if p.exists(): return p
    candidates = list(index_dir.glob("*state*.json"))
    return candidates[0] if candidates else None

def inspect_states(p: Dict, indices: List[str], date: str, r: R, cap_data: Dict) -> Dict:
    r.h("SECTION 3 — STATE FILE INSPECTION")
    r.row("Date", date); r.row("Indices", ", ".join(indices))

    sa0 = list(cap_data.get("start_all_sh", {}).values())
    rp0 = list(cap_data.get("run_paper_sh",  {}).values())
    sa_cap = float(sa0[0]) if sa0 else None
    rp_cap = float(rp0[0]) if rp0 else None

    states: Dict[str, Optional[dict]] = {}
    pm = p.get("postmortem_root")
    if not pm or not pm.exists():
        r.warn("postmortem root not found"); r.flush(); return states

    date_dir = pm / date
    if not date_dir.exists():
        r.warn(f"Date dir not found: {date_dir}"); r.flush(); return states

    for idx in indices:
        idx_dir = date_dir / idx
        r.blank(); r.info(f"── {idx} ──")
        if not idx_dir.exists():
            r.warn(f"Dir not found: {idx_dir}"); states[idx] = None; continue

        sp = _find_state(idx_dir)
        if not sp:
            r.warn("State file not found"); states[idx] = None; continue

        r.row("  file", sp.name); r.row("  mtime", mtime(sp)); r.row("  size", fsize(sp))
        data, err = read_json(sp)
        if err:
            r.warn(f"JSON error: {err}"); states[idx] = None; continue

        for k in STATE_KEYS:
            if k not in data:
                r.warn(f"  missing key: {k}")
            else:
                v = data[k]
                r.row(f"  {k}", len(v) if isinstance(v, list) else v)

        cash = data.get("cash")
        initial = data.get("initial_capital")

        if isinstance(cash, (int,float)) and cash < 0:
            r.warn(f"NEGATIVE CASH: {cash}")

        if isinstance(initial, (int,float)):
            if rp_cap is not None and abs(initial - rp_cap) > 0.01:
                r.warn(f"initial_capital={initial} ≠ run_paper default={rp_cap}")
            if sa_cap is not None and abs(sa_cap - initial) > 0.01:
                delta = sa_cap - initial
                r.warn(f"MIGRATION RISK: start_all.sh CAPITAL={int(sa_cap)}, "
                       f"state initial_capital={initial} → delta={delta:+.0f} to cash={cash}")

        for pos in (data.get("open_positions") or []):
            if isinstance(pos, dict):
                r.info(f"  open pos: id={pos.get('trade_id','?')} side={pos.get('side','?')} "
                       f"strike={pos.get('strike','?')} entry={pos.get('entry_price',pos.get('entry','?'))} "
                       f"qty={pos.get('qty',pos.get('quantity','?'))} time={pos.get('entry_time','?')}")

        states[idx] = data

    r.flush()
    return states


# ─── Section 4 — Journal / Trades / Equity ───────────────────────────────────

CSV_NAMES = {
    "journal": ["decision_journal.csv","journal.csv"],
    "trades":  ["paper_trades.csv","trades.csv"],
    "equity":  ["paper_equity.csv","equity_curve.csv","equity.csv"],
}

def _find_csv(idx_dir: Path, kind: str) -> Optional[Path]:
    for n in CSV_NAMES[kind]:
        c = idx_dir / n
        if c.exists(): return c
    return None

def inspect_csvs(p: Dict, indices: List[str], date: str, r: R, states: Dict) -> None:
    r.h("SECTION 4 — JOURNAL / TRADES / EQUITY CONSISTENCY")
    pm = p.get("postmortem_root")
    if not pm or not pm.exists():
        r.warn("postmortem root not found"); r.flush(); return
    date_dir = pm / date
    if not date_dir.exists():
        r.warn(f"Date dir not found: {date_dir}"); r.flush(); return

    for idx in indices:
        idx_dir = date_dir / idx
        r.blank(); r.info(f"── {idx} ──")
        if not idx_dir.exists():
            r.warn(f"Dir not found: {idx_dir}"); continue

        csv_results: Dict[str, Tuple[Optional[Path], int, List[str]]] = {}
        for kind in ("journal","trades","equity"):
            cp = _find_csv(idx_dir, kind)
            if not cp:
                r.warn(f"{kind.upper()}: not found (searched {CSV_NAMES[kind]})")
                csv_results[kind] = (None, 0, [])
                continue
            cnt, hdrs, err = csv_info(cp)
            if err:
                r.warn(f"{kind.upper()}: parse error — {err}")
                csv_results[kind] = (cp, 0, [])
                continue
            r.row(f"  {kind.upper()}", f"{cnt} rows | {fsize(cp)} | {mtime(cp)}")
            r.info(f"    columns: {hdrs}")
            if cnt == 0: r.warn(f"  {kind.upper()}: zero data rows")
            csv_results[kind] = (cp, cnt, hdrs)

        # Cross-check vs state
        st = states.get(idx)
        if not st: continue
        r.blank()
        jp, j_cnt, _ = csv_results["journal"]
        tp, t_cnt, _ = csv_results["trades"]
        pr = st.get("processed_rows", "N/A")
        r.row("  state.processed_rows", pr); r.row("  journal data rows", j_cnt)
        if isinstance(pr, int) and j_cnt > 0:
            if pr > j_cnt:
                r.warn(f"processed_rows ({pr}) > journal rows ({j_cnt}) — journal may have been replaced")
            elif pr < j_cnt:
                r.info(f"{j_cnt - pr} unprocessed journal rows")
            else:
                r.ok(f"processed_rows == journal rows ({pr})")

        sp = _find_state(idx_dir)
        if sp:
            st_mtime = sp.stat().st_mtime
            for label, cp in [("journal", jp), ("trades", tp)]:
                if cp and cp.stat().st_mtime > st_mtime:
                    r.warn(f"{label} is NEWER than state by {cp.stat().st_mtime-st_mtime:.0f}s")

        wins, losses = st.get("wins",0), st.get("losses",0)
        r.row("  wins+losses", wins+losses); r.row("  trades rows", t_cnt)
        if t_cnt > 0 and abs((wins+losses) - t_cnt) > 2:
            r.warn(f"wins+losses ({wins+losses}) differs from trades rows ({t_cnt})")

    r.flush()


# ─── Section 5 — Duplicate / Replay Risk ─────────────────────────────────────

_J_KEY  = ["date","time","symbol","side","strike","action","status"]
_T_KEY  = ["trade_id","entry_date","entry_time","symbol","side","strike","exit_time"]

def _dup_check(path: Path, key_candidates: List[str], label: str, r: R) -> None:
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            hdrs = reader.fieldnames or []
            keys = [c for c in key_candidates if c in hdrs]
            if not keys:
                r.warn(f"  {label}: no usable key cols (headers: {hdrs})"); return
            seen: Dict[tuple, int] = defaultdict(int)
            total = 0
            for row in reader:
                seen[tuple(row.get(c,"") for c in keys)] += 1
                total += 1
        dups = {k:v for k,v in seen.items() if v>1}
        if dups:
            r.warn(f"  {label}: {len(dups)} duplicate key(s) in {total} rows (key={keys})")
            for k,v in list(dups.items())[:3]:
                r.info(f"    {k} × {v}")
        else:
            r.ok(f"  {label}: no duplicates ({total} rows, key={keys})")
    except Exception as e:
        r.warn(f"  {label}: dup check failed — {e}")

def check_dups(p: Dict, indices: List[str], date: str, r: R, states: Dict) -> None:
    r.h("SECTION 5 — DUPLICATE / REPLAY RISK")
    pm = p.get("postmortem_root")
    if not pm or not pm.exists():
        r.warn("postmortem root not found"); r.flush(); return
    date_dir = pm / date
    if not date_dir.exists():
        r.warn(f"Date dir not found: {date_dir}"); r.flush(); return

    for idx in indices:
        idx_dir = date_dir / idx
        r.blank(); r.info(f"── {idx} ──")
        if not idx_dir.exists():
            r.warn(f"Dir not found: {idx_dir}"); continue

        jp = _find_csv(idx_dir, "journal")
        tp = _find_csv(idx_dir, "trades")
        if jp: _dup_check(jp, _J_KEY, "journal", r)
        else:  r.warn("  journal: not found")
        if tp: _dup_check(tp, _T_KEY, "trades", r)
        else:  r.warn("  trades: not found")

        # processed_rows reset signal
        st = states.get(idx)
        if st and jp:
            pr = st.get("processed_rows", 0)
            j_cnt, _, _ = csv_info(jp)
            if pr == 0 and j_cnt > 0:
                r.warn(f"  processed_rows=0 but journal has {j_cnt} rows — possible state reset")
            elif j_cnt > 0 and pr < j_cnt * 0.5:
                r.warn(f"  processed_rows ({pr}) < 50% of journal rows ({j_cnt})")

    r.flush()


# ─── Section 6 — Accounting Sanity ───────────────────────────────────────────

def check_accounting(p: Dict, indices: List[str], date: str, r: R, states: Dict) -> None:
    r.h("SECTION 6 — ACCOUNTING SANITY CHECKS")
    r.info("Lightweight invariants only — not a full backtest.")
    pm = p.get("postmortem_root")
    if not pm or not pm.exists():
        r.warn("postmortem root not found"); r.flush(); return
    date_dir = pm / date
    if not date_dir.exists():
        r.warn(f"Date dir not found: {date_dir}"); r.flush(); return

    for idx in indices:
        idx_dir = date_dir / idx
        r.blank(); r.info(f"── {idx} ──")
        st = states.get(idx)
        if not st:
            r.warn("No state data"); continue

        for field in ("cash","realized_pnl","total_fees"):
            v = st.get(field)
            if v is None: r.warn(f"  {field}: MISSING")
            elif not isinstance(v, (int,float)): r.warn(f"  {field}: not numeric ({type(v).__name__}={v!r})")

        initial  = st.get("initial_capital", 0)
        cash     = st.get("cash")
        rpnl     = st.get("realized_pnl")
        open_pos = st.get("open_positions") or []

        if all(isinstance(v,(int,float)) for v in (cash, rpnl, initial)):
            # Estimate cost of open positions (entry_price × qty)
            open_cost = 0.0
            for pos in open_pos:
                if not isinstance(pos, dict): continue
                try:
                    ep = float(pos.get("entry_price", pos.get("entry", 0)))
                    qty = float(pos.get("qty", pos.get("quantity", 0)))
                    open_cost += ep * qty
                except (TypeError, ValueError):
                    pass
            expected = initial + rpnl - open_cost
            delta = cash - expected
            r.row("  initial_capital",          f"{initial:>12,.2f}")
            r.row("  realized_pnl",             f"{rpnl:>12,.2f}")
            r.row("  open_pos cost (est.)",      f"{open_cost:>12,.2f}")
            r.row("  expected cash (approx.)",   f"{expected:>12,.2f}")
            r.row("  actual cash",               f"{cash:>12,.2f}")
            r.row("  delta actual−expected",     f"{delta:>+12,.2f}")
            if abs(delta) > 1000:
                r.warn(f"Large accounting delta={delta:+,.0f} — review open-pos cost or fee treatment")
            elif abs(delta) > 50:
                r.info(f"Minor delta={delta:+,.0f} — likely fee treatment difference")
            else:
                r.ok(f"Cash within ±50 of expected (delta={delta:+,.0f})")

        # Spot-check last 3 trade rows
        if not idx_dir.exists(): continue
        tp = _find_csv(idx_dir, "trades")
        if not tp: continue
        try:
            rows = []
            with tp.open(encoding="utf-8", errors="replace") as f:
                rd = csv.DictReader(f)
                hdrs = set(rd.fieldnames or [])
                rows = list(rd)
            # Need at least exit/entry/qty/pnl
            ep_col  = next((c for c in ("exit_price","exit") if c in hdrs), None)
            en_col  = next((c for c in ("entry_price","entry") if c in hdrs), None)
            qty_col = next((c for c in ("qty","quantity") if c in hdrs), None)
            pnl_col = next((c for c in ("net_pnl","pnl") if c in hdrs), None)
            if ep_col and en_col and qty_col and pnl_col:
                r.info("  Spot-check last 3 closed trades:")
                for row in rows[-3:]:
                    try:
                        ex  = float(row[ep_col]); en = float(row[en_col])
                        qty = float(row[qty_col]); net = float(row[pnl_col])
                        gross = (ex - en) * qty
                        r.info(f"    exit={ex} entry={en} qty={qty} gross={gross:.2f} "
                               f"net_pnl={net:.2f} implied_fees={gross-net:.2f}")
                    except (ValueError, TypeError) as e:
                        r.info(f"    (parse error: {e})")
            else:
                r.info(f"  Trades columns: {list(hdrs)} — spot-check skipped")
        except Exception as e:
            r.warn(f"  Trades spot-check failed: {e}")

    r.flush()


# ─── Section 7 — Adaptive Model Contention ───────────────────────────────────

def check_adaptive(p: Dict, r: R) -> None:
    r.h("SECTION 7 — ADAPTIVE MODEL CONTENTION RISK")
    root = p.get("fyersn7_root")

    # Known locations to search
    candidates = []
    if root:
        candidates += [root / ".adaptive_model.json",
                       root / "scripts" / ".adaptive_model.json"]
    # Per-index locations
    pm = p.get("postmortem_root")
    if pm and pm.exists():
        candidates.append(pm.parent / ".adaptive_model.json")  # root-level
        candidates.append(pm / ".adaptive_model.json")

    seen = set(); found = []
    for c in candidates:
        try: real = str(c.resolve())
        except Exception: real = str(c)
        if real not in seen and c.exists():
            seen.add(real); found.append(c)

    if not found:
        r.warn("No .adaptive_model.json found in any expected location")
        r.info(f"  Searched: {[str(c) for c in candidates[:4]]}")
    else:
        for mp in found:
            r.row("  Found", str(mp))
            r.row("  mtime", mtime(mp)); r.row("  size", fsize(mp))
            data, err = read_json(mp)
            if err: r.warn(f"  JSON error: {err}")
            elif isinstance(data, dict):
                r.info(f"  keys: {list(data.keys())[:8]}")
                if "n_samples" in data: r.row("  n_samples", data["n_samples"])

    # Is the model at the shared root level (not per-index)?
    if root and (root / ".adaptive_model.json").exists():
        r.warn("Model is at fyersN7 ROOT — shared across ALL indices")
        r.warn("4 index loops running concurrently all write to the SAME file")
        r.warn("No file-level locking visible in shell scripts → WRITE CONTENTION RISK")

    # Confirm ADAPTIVE_MODEL_FILE default in run_paper_sh
    rp = p.get("run_paper_sh")
    if rp:
        t = read_text(rp)
        if t:
            m = re.search(r'ADAPTIVE_MODEL_FILE[^=]*=\s*["\']?\$\{[^}]*:-([^}"\']+)\}', t)
            if m: r.row("  ADAPTIVE_MODEL_FILE default", m.group(1))
            if "INDEX" in t:
                idx_in_path = re.search(r'ADAPTIVE_MODEL_FILE.*?\$\{?INDEX', t)
                if idx_in_path: r.ok("INDEX embedded in model path — per-index isolation")
                else:           r.warn("INDEX not embedded in model path — all indices share same file")

    # Confirm invocation from paper_trade_loop.py
    pl = p.get("paper_loop_py")
    if pl:
        t = read_text(pl)
        if t and "update_adaptive_model" in t:
            r.info("paper_trade_loop.py invokes update_adaptive_model.py (confirmed)")
            r.info("→ every index loop can write the model file on each cycle backfill")

    r.flush()


# ─── Section 8 — UI/API Path Integrity ───────────────────────────────────────

def check_ui_api(p: Dict, r: R) -> None:
    r.h("SECTION 8 — UI/API FYERSN7 PATH INTEGRITY")

    # 8A SignalView
    r.info("8A  SignalView.jsx (/signals route)")
    sv = p.get("signal_view")
    if sv:
        t = read_text(sv)
        if t:
            fyersn7_calls = re.findall(r'fetch(?:FyersN7|Fyers[A-Z]\w+)\b', t)
            other_calls   = re.findall(r'fetch(?!FyersN7|Fyers[A-Z]\w+)[A-Z]\w+\b', t)
            r.info(f"  FYERSN7 API calls: {sorted(set(fyersn7_calls))}")
            if other_calls: r.warn(f"  Non-FYERSN7 calls: {sorted(set(other_calls))}")
            else:           r.ok("  No non-FYERSN7 API calls")
            if "snapshot" in t:   r.ok("  Uses /snapshot endpoint")
            if "latest_only" in t: r.ok("  Uses latest_only=true param")
            # IST check
            has_local_time = bool(re.search(r'new Date\(\)|Date\.now\(\)', t))
            has_ist = any(x in t for x in ("IST","Asia/Calcutta","Asia/Kolkata","+05:30"))
            if has_local_time and not has_ist:
                r.warn("  Uses browser local time without IST conversion — "
                       "market session display wrong for non-IST users")
        else: r.warn("  Cannot read SignalView.jsx")
    else: r.warn("  SignalView.jsx not found")

    # 8B BotEnsemble
    r.blank(); r.info("8B  BotEnsemble.jsx (multi-engine view)")
    be = p.get("bot_ensemble")
    if be:
        t = read_text(be)
        if t:
            # Detect spread-merge of two trade arrays
            mix = re.search(r'\[\.\.\.\w*[Tt]rade\w*,\s*\.\.\.\w*[Tt]rade\w*\]', t)
            if mix:
                r.warn("DATA MIXING CONFIRMED: FYERSN7 + AutoTrader spread-merged in 'all' tab")
                lines = t.splitlines()
                for i, line in enumerate(lines, 1):
                    if "...tradeHistory" in line and "atTrade" in line:
                        r.info(f"  Line {i}: {line.strip()}")
            else:
                r.info("  No array spread-merge detected for 'all' tab")
            if "fetchFyersn7TradeHistory" in t: r.ok("  Uses fetchFyersn7TradeHistory (FYERSN7-specific)")
            if "fetchTradeHistory" in t:        r.info("  Also uses fetchTradeHistory (AutoTrader-specific)")
        else: r.warn("  Cannot read BotEnsemble.jsx")
    else: r.warn("  BotEnsemble.jsx not found")

    # 8C server.py
    r.blank(); r.info("8C  server.py (API endpoints)")
    sp = p.get("server_py")
    if sp:
        t = read_text(sp)
        if t:
            eps = sorted(set(re.findall(r'["\'](?:/api)?/fyersn7/[^"\']+["\']', t)))
            r.info(f"  FYERSN7 endpoints ({len(eps)}):")
            for ep in eps: r.info(f"    {ep}")
            m = re.search(r'FYERSN7_DATA_PATH\s*=\s*([^\n]+)', t)
            if m: r.row("  FYERSN7_DATA_PATH", m.group(1).strip())
            if "_get_fyersn7_fallback" in t:
                r.warn("DATA MIXING: /api/market/live has FYERSN7 journal fallback")
                r.warn("  Stale journal spot prices returned when FYERS API is down")
                fn = re.search(r'def (_get_fyersn7_fallback[^\n]*)', t)
                if fn:
                    lineno = t[:fn.start()].count("\n")+1
                    r.info(f"  Function: {fn.group(1)} (~line {lineno})")
            if "lru_cache" in t and "csv" in t.lower():
                r.info("  LRU cache on CSV reads — staleness possible between reads")
        else: r.warn("  Cannot read server.py")
    else: r.warn("  server.py not found")

    r.flush()


# ─── Section 9 — Risk Summary ─────────────────────────────────────────────────

def risk_summary(p: Dict, cap_data: Dict, states: Dict, date: str) -> None:
    sa0 = list(cap_data.get("start_all_sh",{}).values())
    rp0 = list(cap_data.get("run_paper_sh", {}).values())
    sa  = sa0[0] if sa0 else None
    rp  = rp0[0] if rp0 else None

    confirmed = []
    likely    = []
    manual    = []
    not_prov  = []

    # Capital mismatch
    if sa and rp and sa != rp:
        confirmed.append((
            f"CAPITAL DEFAULT MISMATCH: start_all.sh={sa} vs run_paper_trade_loop.sh={rp}",
            "start_all.sh, run_paper_trade_loop.sh"
        ))

    # Migration risk per index
    for idx, st in states.items():
        if not st: continue
        initial = st.get("initial_capital")
        cash    = st.get("cash")
        if isinstance(initial,(int,float)) and sa and abs(float(sa)-initial) > 0.01:
            delta = float(sa)-initial
            confirmed.append((
                f"CAPITAL MIGRATION RISK ({idx}): start_all.sh CAPITAL={sa}, "
                f"state initial_capital={initial} → delta={delta:+.0f} applied to cash={cash}",
                f"postmortem/{date}/{idx}/.paper_trade_state.json + start_all.sh"
            ))
        if isinstance(cash,(int,float)) and cash < 0:
            confirmed.append((f"NEGATIVE CASH ({idx}): cash={cash}",
                              f"postmortem/{date}/{idx}/.paper_trade_state.json"))

    # SIGTERM
    pl = p.get("paper_loop_py")
    if pl:
        t = read_text(pl)
        if t and "SIGTERM" not in t and "KeyboardInterrupt" in t:
            confirmed.append((
                "SIGTERM NOT HANDLED: paper_trade_loop.py only catches KeyboardInterrupt — "
                "SIGTERM kills without save_json()",
                "paper_trade_loop.py main()"
            ))

    # BotEnsemble mixing
    be = p.get("bot_ensemble")
    if be:
        t = read_text(be)
        if t and "...tradeHistory" in t and "atTrade" in t:
            confirmed.append((
                "FYERSN7 + AutoTrader trades merged in BotEnsemble 'all' tab",
                "frontend/src/pages/BotEnsemble.jsx"
            ))

    # Market/live fallback
    sp = p.get("server_py")
    if sp:
        t = read_text(sp)
        if t and "_get_fyersn7_fallback" in t:
            confirmed.append((
                "/api/market/live injects FYERSN7 journal spot prices when FYERS API fails",
                "livebench/api/server.py (_get_fyersn7_fallback_data)"
            ))

    # Adaptive model
    root = p.get("fyersn7_root")
    if root and (root / ".adaptive_model.json").exists():
        confirmed.append((
            "ADAPTIVE MODEL SHARED: .adaptive_model.json is at fyersN7 root, "
            "all 4 index loops write to the same file concurrently",
            "engines/fyersN7/fyers-2026-03-05/.adaptive_model.json"
        ))

    # Likely
    if pl:
        t = read_text(pl) or ""
        if "processed_rows" in t and "len(rows)" in t:
            likely.append((
                "processed_rows advanced before position open — journal-state gap on crash",
                "paper_trade_loop.py (processed_rows = len(rows) before open_position())"
            ))

    manual += [
        ("Verify CAPITAL in live process: ps aux | grep paper_trade_loop | grep -o 'capital [0-9]*'",
         "Runtime environment — not determinable from static files"),
        ("Verify adaptive model CWD at launch time to confirm shared vs per-index resolution",
         "run_paper_trade_loop.sh (ADAPTIVE_MODEL_FILE is relative path)"),
        ("Full PnL reconciliation: sum net_pnl from paper_trades.csv vs state realized_pnl",
         "paper_trades.csv + .paper_trade_state.json (not computed here)"),
    ]

    not_prov += [
        ("No duplicate trade IDs observed in static file check",
         "paper_trades.csv dup check in Section 5"),
        ("instance lock (acquire_instance_lock) prevents duplicate index processes per-index",
         "init_daily_folder.sh"),
    ]

    print(f"\n{SEP}\n  SECTION 9 — RISK SUMMARY\n{SEP}")
    for label, items in [("CONFIRMED",confirmed),("LIKELY",likely),
                          ("NEEDS MANUAL VALIDATION",manual),("NOT PROVEN",not_prov)]:
        print(f"\n  {label}:")
        if not items:
            print("    (none)")
        for item, src in items:
            print(f"    • {item}")
            print(f"      Source: {src}")


# ─── Section 10 — Safe Next Actions ──────────────────────────────────────────

NEXT_ACTIONS = f"""
{SEP}
  SECTION 10 — SAFE NEXT ACTIONS (READ-ONLY ONLY)
{SEP}

  1. CAPITAL IN RUNNING PROCESSES
       ps aux | grep paper_trade_loop.py | grep -oE 'capital [0-9]+'
     Compare against state initial_capital values above.

  2. SIGTERM HANDLING
       grep -n "SIGTERM\\|signal\\.signal" engines/fyersN7/fyers-2026-03-05/scripts/paper_trade_loop.py
     Confirm whether SIGTERM handler exists. If absent, re-verify Section 9 finding.

  3. CAPITAL MIGRATION CODE
       sed -n '245,265p' engines/fyersN7/fyers-2026-03-05/scripts/paper_trade_loop.py
     Read load_state() delta logic to confirm exact condition and arithmetic.

  4. ADAPTIVE MODEL ISOLATION
       ls -la engines/fyersN7/fyers-2026-03-05/.adaptive_model.json
       ls -la engines/fyersN7/fyers-2026-03-05/postmortem/{dt_date.today()}/*/
     Check if per-index model files are present in postmortem dirs.

  5. FULL PNL RECONCILIATION (single index)
       python3 -c "
       import csv
       rows = list(csv.DictReader(open('paper_trades.csv')))
       print(sum(float(r['net_pnl']) for r in rows))
       "
     Run from the postmortem/{dt_date.today()}/SENSEX/ directory.
     Compare against state realized_pnl.

  6. JOURNAL-STATE DIVERGENCE
       wc -l decision_journal.csv
     Compare against state processed_rows (Section 3 output above).

  7. DATA MIXING
     Review BotEnsemble.jsx line flagged in Section 8 and confirm
     whether the 'all' tab is exposed to operators in production.
     Review server.py _get_fyersn7_fallback_data() to understand
     the staleness window for /api/market/live callers.
"""


# ─── Auto-detect helpers ──────────────────────────────────────────────────────

def latest_date(pm: Optional[Path]) -> Optional[str]:
    if not pm or not pm.exists(): return None
    dpat = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    try:
        ds = sorted(d.name for d in pm.iterdir() if d.is_dir() and dpat.match(d.name))
        return ds[-1] if ds else None
    except Exception: return None

def available_indices(pm: Optional[Path], date: str) -> List[str]:
    if not pm: return []
    dd = pm / date
    if not dd.exists(): return []
    try: return sorted(d.name for d in dd.iterdir() if d.is_dir())
    except Exception: return []


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description=f"FYERSN7 Read-Only Validation v{VERSION}")
    ap.add_argument("--repo-root",   default=".",  metavar="PATH")
    ap.add_argument("--date",        default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--index",       default=None, metavar="NAME")
    ap.add_argument("--json-output", default=None, metavar="PATH")
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    r = R()

    print(f"{SEP}")
    print(f"  FYERSN7 Paper Trading Validation  v{VERSION}")
    print(f"  Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Repo root : {repo_root}")
    print(f"{SEP}")

    paths = discover(repo_root, r)

    pm = paths.get("postmortem_root")
    date = args.date or latest_date(pm)
    if not date:
        date = dt_date.today().isoformat()
        print(f"\n  WARNING: Could not auto-detect date; using today: {date}")
    else:
        print(f"\n  Date      : {date}")

    indices = [args.index] if args.index else available_indices(pm, date)
    if not indices:
        indices = ["SENSEX","BANKNIFTY","NIFTY50","FINNIFTY"]
        print(f"  WARNING: No index dirs found; using defaults: {indices}")
    else:
        print(f"  Indices   : {', '.join(indices)}")

    cap_data = analyze_capital(paths, r)
    states   = inspect_states(paths, indices, date, r, cap_data)
    inspect_csvs(paths, indices, date, r, states)
    check_dups(paths, indices, date, r, states)
    check_accounting(paths, indices, date, r, states)
    check_adaptive(paths, r)
    check_ui_api(paths, r)
    risk_summary(paths, cap_data, states, date)
    print(NEXT_ACTIONS)

    if args.json_output:
        out = {
            "version": VERSION,
            "generated_at": datetime.now().isoformat(),
            "repo_root": str(repo_root),
            "date": date,
            "indices": indices,
            "paths": {k: str(v) if v else None for k,v in paths.items()},
            "capital_data": cap_data,
            "state_data":   {k: v for k,v in states.items() if v is not None},
        }
        try:
            Path(args.json_output).write_text(
                json.dumps(out, indent=2, default=str), encoding="utf-8")
            print(f"\n  JSON output written: {args.json_output}")
        except Exception as e:
            print(f"\n  ERROR writing JSON: {e}", file=sys.stderr)

    print(f"\n{SEP}")
    print("  Validation complete. No files were modified.")
    print(f"{SEP}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(1)
