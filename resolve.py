import re

# 1. Resolve phase05/assessment.py (keep main's mapping validation + drop invalid/unselected chunk IDs)
p1 = "iop_phase03_document_ingestion/phase05/assessment.py"
t1 = open(p1, encoding="utf-8").read()
t1 = re.sub(
    r"<<<<<<< HEAD\r?\n.*?>>>>>>> main\r?\n",
    '        print(\n            f"[WARN] Dropping unsupplied evidence_chunk_ids for criterion {criterion_id}: {unknown_ids}",\n            flush=True,\n',
    t1,
    flags=re.S,
)
old_mapped = (
    "            if chunk_id not in selected_ids:\n"
    "                raise ValueError(\n"
    '                    f"LLM mapped strength to an unselected evidence chunk for criterion "\n'
    '                    f"{criterion_id}: {chunk_id}."\n'
    "                )"
)
new_mapped = (
    "            if chunk_id not in selected_ids:\n"
    "                print(\n"
    '                    f"[WARN] Dropping unselected mapped chunk ID for criterion {criterion_id}: {chunk_id}",\n'
    "                    flush=True,\n"
    "                )\n"
    "                continue"
)
t1 = t1.replace(old_mapped, new_mapped)
assert "<<<<<<<" not in t1, "Conflict markers still remain in assessment.py"
open(p1, "w", encoding="utf-8", newline="\n").write(t1)

# 2. Resolve webapp/templates/index.html (keep main's sidebar/multi-file UI + relative /iop/ paths)
p2 = "iop_phase03_document_ingestion/webapp/templates/index.html"
t2 = open(p2, encoding="utf-8").read()
t2 = re.sub(
    r"<<<<<<< HEAD\r?\n.*?=======\r?\n(.*?)>>>>>>> main\r?\n",
    r"\1",
    t2,
    flags=re.S,
)
t2 = re.sub(r"""(['"`])/(api|static)/""", r"\1\2/", t2)
assert "<<<<<<<" not in t2, "Conflict markers still remain in index.html"
open(p2, "w", encoding="utf-8", newline="\n").write(t2)

print("Resolved both files cleanly!")