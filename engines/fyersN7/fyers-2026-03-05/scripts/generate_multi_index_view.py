#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import html
import sys
from pathlib import Path
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo

# Try to import from centralized config
try:
    _PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))
    from shared_project_engine.indices import ACTIVE_INDICES
    DEFAULT_INDICES = ACTIVE_INDICES
except ImportError:
    DEFAULT_INDICES = ["SENSEX", "NIFTY50", "BANKNIFTY"]


def ist_today() -> str:
    return dt.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")


def read_csv_rows(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    if not path.exists():
        return [], []

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        headers = [h.strip() for h in (reader.fieldnames or []) if h]
        rows: List[Dict[str, str]] = []
        for raw in reader:
            row: Dict[str, str] = {}
            for k, v in raw.items():
                if not k:
                    continue
                row[k.strip()] = (v or "").strip()
            rows.append(row)
    return headers, rows


def dedupe_rows(rows: List[Dict[str, str]], columns: List[str]) -> List[Dict[str, str]]:
    seen = set()
    out: List[Dict[str, str]] = []
    for row in rows:
        key = tuple((c, row.get(c, "").strip()) for c in columns)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def render_table(title: str, columns: List[str], rows: List[Dict[str, str]]) -> str:
    title_safe = html.escape(title)
    if not columns:
        return (
            f"<section><h2>{title_safe}</h2>"
            "<p class='empty'>No data file found.</p></section>"
        )

    thead = "".join(f"<th>{html.escape(c)}</th>" for c in columns)
    body_rows: List[str] = []
    for r in rows:
        tds = "".join(f"<td>{html.escape(r.get(c, ''))}</td>" for c in columns)
        body_rows.append(f"<tr>{tds}</tr>")

    tbody = "\n".join(body_rows) if body_rows else f"<tr><td colspan='{len(columns)}'>No rows</td></tr>"

    return (
        f"<section><h2>{title_safe}</h2>"
        f"<div class='table-wrap'><table><thead><tr>{thead}</tr></thead>"
        f"<tbody>{tbody}</tbody></table></div></section>"
    )


def build_html(
    date_str: str,
    source_file: str,
    index_tables: Dict[str, Dict[str, object]],
    combined_columns: List[str],
    combined_rows: List[Dict[str, str]],
) -> str:
    generated_at = dt.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")

    summary_items: List[str] = []
    for idx, payload in index_tables.items():
        total_rows = payload["row_count"]
        unique_rows = payload["unique_count"]
        dupes = total_rows - unique_rows
        summary_items.append(
            f"<li><b>{html.escape(idx)}</b>: {total_rows} rows, {unique_rows} unique, {dupes} duplicates removed</li>"
        )

    per_index_sections: List[str] = []
    for idx in DEFAULT_INDICES:
        payload = index_tables.get(idx)
        if payload is None:
            continue
        per_index_sections.append(
            render_table(
                f"{idx} ({payload['unique_count']} unique rows)",
                payload["columns"],
                payload["rows"],
            )
        )

    combined_section = render_table(
        f"Combined (all indices, {len(combined_rows)} unique rows)",
        combined_columns,
        combined_rows,
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Multi-Index View - {html.escape(date_str)}</title>
  <style>
    :root {{
      --bg: #f7f8fb;
      --panel: #ffffff;
      --text: #1f2937;
      --muted: #6b7280;
      --border: #d1d5db;
      --header: #111827;
      --accent: #0f766e;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 20px;
      background: linear-gradient(180deg, #eef2ff 0%, var(--bg) 55%);
      color: var(--text);
      font-family: "Avenir Next", "Segoe UI", sans-serif;
    }}
    .shell {{
      max-width: 100%;
      margin: 0 auto;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
      box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
    }}
    h1 {{ margin: 0 0 8px; font-size: 24px; color: var(--header); }}
    .meta {{ margin: 0 0 12px; color: var(--muted); font-size: 14px; }}
    .meta code {{ color: var(--accent); font-weight: 600; }}
    ul {{ margin-top: 6px; }}
    li {{ margin: 2px 0; color: var(--muted); }}
    section {{ margin-top: 20px; }}
    h2 {{ margin: 0 0 10px; font-size: 18px; color: var(--header); }}
    .table-wrap {{
      overflow: auto;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: #fff;
      max-height: 460px;
    }}
    table {{
      border-collapse: collapse;
      width: max-content;
      min-width: 100%;
      font-size: 12px;
    }}
    thead th {{
      position: sticky;
      top: 0;
      background: #e5e7eb;
      z-index: 2;
    }}
    th, td {{
      border: 1px solid var(--border);
      padding: 4px 8px;
      white-space: nowrap;
      text-align: left;
      vertical-align: top;
    }}
    tbody tr:nth-child(even) {{ background: #f9fafb; }}
    .empty {{ color: var(--muted); }}
  </style>
</head>
<body>
  <div class="shell">
    <h1>Multi-Index Journal View</h1>
    <p class="meta">
      Date: <code>{html.escape(date_str)}</code> | Source file: <code>{html.escape(source_file)}</code> |
      Generated: <code>{html.escape(generated_at)}</code>
    </p>
    <ul>
      {''.join(summary_items)}
    </ul>
    {''.join(per_index_sections)}
    {combined_section}
  </div>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate simple HTML view with per-index and combined tables."
    )
    parser.add_argument(
        "--base-dir",
        default="postmortem",
        help="Base folder containing date/index folders (default: postmortem)",
    )
    parser.add_argument(
        "--date",
        default=ist_today(),
        help="Date folder to read, format YYYY-MM-DD (default: IST today)",
    )
    parser.add_argument(
        "--indices",
        default=",".join(DEFAULT_INDICES),
        help="Comma-separated index list (default: SENSEX,NIFTY50,BANKNIFTY)",
    )
    parser.add_argument(
        "--source-file",
        default="decision_journal.csv",
        help="CSV file name inside each index folder (default: decision_journal.csv)",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output HTML file path (default: <base>/<date>/multi_index_view.html)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    base_dir = Path(args.base_dir)
    date_dir = base_dir / args.date
    indices = [i.strip().upper() for i in args.indices.split(",") if i.strip()]

    if not indices:
        indices = list(DEFAULT_INDICES)

    index_tables: Dict[str, Dict[str, object]] = {}
    union_columns: List[str] = []

    for idx in indices:
        csv_path = date_dir / idx / args.source_file
        columns, rows = read_csv_rows(csv_path)
        unique_rows = dedupe_rows(rows, columns) if columns else []

        for c in columns:
            if c not in union_columns:
                union_columns.append(c)

        index_tables[idx] = {
            "columns": columns,
            "rows": unique_rows,
            "row_count": len(rows),
            "unique_count": len(unique_rows),
            "csv_path": str(csv_path),
        }

    combined_columns = ["source_index"] + union_columns
    combined_rows_raw: List[Dict[str, str]] = []

    for idx in indices:
        payload = index_tables.get(idx, {})
        rows = payload.get("rows", [])
        for row in rows:
            combined_row = {"source_index": idx}
            for c in union_columns:
                combined_row[c] = row.get(c, "")
            combined_rows_raw.append(combined_row)

    combined_rows = dedupe_rows(combined_rows_raw, combined_columns)

    html_text = build_html(
        date_str=args.date,
        source_file=args.source_file,
        index_tables=index_tables,
        combined_columns=combined_columns,
        combined_rows=combined_rows,
    )

    if args.output:
        out_path = Path(args.output)
    else:
        out_path = date_dir / "multi_index_view.html"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_text, encoding="utf-8")

    print(f"Generated HTML: {out_path}")
    for idx in indices:
        payload = index_tables.get(idx, {})
        print(
            f"- {idx}: {payload.get('row_count', 0)} rows, "
            f"{payload.get('unique_count', 0)} unique"
        )
    print(f"- Combined: {len(combined_rows)} unique rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
