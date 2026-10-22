import sys
from pathlib import Path

from pypdf import PdfReader

CANDIDATES = [
    r"C:\Users\ROWTECH\Downloads\DietRx Final SRS Requirement ##73.pdf",
    r"C:\Users\ROWTECH\Downloads\SRS Final REPORT DietRx.pdf",
    r"C:\Users\ROWTECH\Downloads\Software_SRS_Final REport.pdf",
    r"C:\Users\ROWTECH\Downloads\DietRx_Final_SRS_Report_Revised.pdf",
    r"C:\Users\ROWTECH\Downloads\DietRx_Requirement_Specifications_Final_Corrected.pdf",
    r"C:\Users\ROWTECH\Downloads\DietRx 73 merged.pdf",
]

out_dir = Path(r"d:\software lab\dietRx\s48_pdf")
out_dir.mkdir(exist_ok=True)

for src in CANDIDATES:
    p = Path(src)
    if not p.exists():
        print("MISSING", src)
        continue
    try:
        reader = PdfReader(str(p))
        pages = []
        for i, page in enumerate(reader.pages):
            try:
                txt = page.extract_text() or ""
            except Exception as exc:  # noqa: BLE001
                txt = f"<<extract error {exc}>>"
            pages.append(f"\n===== PAGE {i + 1} =====\n{txt}")
        body = "".join(pages)
        stem = p.stem.replace(" ", "_").replace("#", "")
        out = out_dir / f"{stem}.txt"
        out.write_text(body, encoding="utf-8")
        print(f"OK {p.name}: pages={len(reader.pages)} chars={len(body)} -> {out.name}")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {p.name}: {exc}")
