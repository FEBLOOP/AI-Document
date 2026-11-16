# สรุปงานทั้งหมดของโปรเจกต์ AI Document IOP

อัปเดตล่าสุด: 15 พฤศจิกายน 2026

## 1. ภาพรวมระบบ

ระบบนี้รับเอกสาร PDF แล้วประมวลผลต่อเนื่องตั้งแต่ Phase 03 ถึง Phase 07 ดังนี้

1. **Phase 03 — Document Ingestion และ OCR**
2. **Phase 04 — Document Understanding, Chunking และ Embedding**
3. **Phase 05 — Retrieval และการประเมินรายเกณฑ์ด้วย LLM**
4. **Phase 06 — สรุปผลโดยรักษาข้อมูล Phase 05 แบบ immutable**
5. **Phase 07 — คำนวณคะแนน Maturity และสร้างรายงาน PDF/DOCX**
6. **FastAPI Web UI — อัปโหลด ติดตามสถานะ เลือก Provider/มิติ และดาวน์โหลดผลลัพธ์**
7. **Internal File Manager — ดู ดาวน์โหลด และลบ Generated Outputs อย่างปลอดภัย**

Knowledge Base หลักอยู่ที่:

```text
C:\Users\UsEr\147\AI-Document\data\knowledge_base
```

ไฟล์ที่ใช้สำคัญ ได้แก่:

- `iop_reference.json` — เกณฑ์ประเมินและ scoring guide
- `good_examples_kb.json` — รูปแบบตัวอย่างสำหรับ Phase 06

## 2. การรองรับ LLM หลาย Provider

สร้างชั้นกลางใน `llm/` เพื่อให้ Phase 05 และ Phase 06 เลือก Provider ได้โดยไม่ผูกกับผู้ให้บริการรายเดียว

Provider ที่รองรับ:

- `huggingface` — โหลด Qwen โดยตรงในเครื่อง
- `ollama` — เรียก Ollama/OpenAI-compatible endpoint
- `openai` — OpenAI API
- `anthropic` — Claude API
- `none` — ไม่ใช้ LLM

ไฟล์หลัก:

- `llm/config.py` — อ่าน Environment Variables และเลือก Provider/Model
- `llm/provider.py` — เรียก Provider และแปลงผลเป็น JSON

ตัวแปรหลัก:

```powershell
$env:IOP_LLM_PROVIDER = "huggingface"
$env:IOP_LLM_MODEL = "Qwen/Qwen3-8B"
$env:IOP_LLM_MAX_NEW_TOKENS = "512"
$env:IOP_LLM_LOAD_IN_4BIT = "true"
```

API Key ที่รองรับ:

```powershell
$env:OPENAI_API_KEY = "ใส่ OpenAI API key"
$env:ANTHROPIC_API_KEY = "ใส่ Anthropic API key"
```

หรือใช้ตัวแปรกลาง:

```powershell
$env:IOP_LLM_API_KEY = "ใส่ API key"
```

API Key ไม่ได้ถูกเขียนลง source code หรือไฟล์ output

## 3. Qwen3-8B ผ่าน Hugging Face

- ตั้งโมเดลหลักเป็น `Qwen/Qwen3-8B`
- โหลดด้วย `AutoTokenizer` และ `AutoModelForCausalLM`
- ใช้ `tokenizer.apply_chat_template()` ของ Qwen โดยตรง
- ปิด Thinking ด้วย `enable_thinking=False` เพื่อลด reasoning ที่ยาวและช่วยให้ได้ JSON ครบ
- cache ตัว tokenizer/model ไว้ใน process เพื่อไม่โหลดใหม่ทุก batch
- รองรับ 4-bit quantization ผ่าน `bitsandbytes`
- หากเปิด 4-bit แต่ไม่มี CUDA ระบบจะแจ้งข้อผิดพลาดชัดเจน แทนการโหลดโมเดลเต็มลง RAM

ไฟล์ dependency แยกอยู่ที่:

```text
requirements-huggingface.txt
```

น้ำหนักโมเดลจะดาวน์โหลดครั้งแรกและเก็บใน Hugging Face cache โดยปกติปิดคอมแล้วไม่ต้องดาวน์โหลดใหม่ ยกเว้น cache ถูกลบหรือเปลี่ยน model/revision

## 4. Ollama ของบริษัท

เพิ่ม Provider `ollama` เพื่อเรียกโมเดลที่บริษัทติดตั้งบน Ollama Host โดยไม่ต้องดาวน์โหลดโมเดลเข้า Container หรือเครื่อง Client

ตัวอย่าง Environment Variables:

```powershell
$env:IOP_LLM_PROVIDER = "ollama"
$env:IOP_LLM_BASE_URL = "http://172.17.1.90:11434/v1"
$env:IOP_LLM_MODEL = "qwen3:8b"
```

ต้องตรวจชื่อ model/tag จริงจาก Ollama `/api/tags` ก่อนใช้งาน ในระบบเครือข่ายบริษัทสามารถเรียก Host ผ่าน port `11434` ได้เมื่อ routing/firewall อนุญาต

ข้อสังเกตจากการทดสอบเดิม: Ollama บน Server 4 CPU / RAM 16 GB ทำงานช้าเมื่อ prompt ยาวหรือ batch ใหญ่ โดยเฉพาะโมเดลที่รันด้วย CPU

## 5. OpenAI และ Claude

### OpenAI

- รองรับ `openai` Provider และ JSON response format
- เคยตรวจสอบว่า account มองเห็น `gpt-4.1-mini`
- การเรียก Chat Completion เคยตอบ `429 insufficient_quota` เพราะไม่มีเครดิต จึงต้องเติม API credit ก่อนใช้งานจริง

### Claude

- เพิ่ม `anthropic` Provider
- ใช้ `ANTHROPIC_API_KEY` หรือ `IOP_LLM_API_KEY`
- รองรับ Phase 05 และ Phase 06 ผ่าน CLI/Web
- มี JSON parsing และ error message เมื่อไม่ได้ตั้ง API key

## 6. Phase 03 — OCR และลำดับการอ่านข้อความ

Phase 03 รองรับทั้ง Native PDF Text และ OCR

ความสามารถหลัก:

- อ่าน Native Text ก่อนเพื่อไม่ OCR โดยไม่จำเป็น
- บังคับ OCR ด้วย `--force-ocr`
- รองรับภาษา `tha+eng`
- รองรับ PSM 3 และ PSM 6
- ปรับ DPI และ preprocessing ได้
- เก็บ page, block ID, bounding box, confidence และ extraction method

ได้ปรับการเรียง OCR สำหรับเอกสารหลายคอลัมน์:

1. ถ้า block มีช่วงแกน X ทับกัน ให้ถือว่าอยู่คอลัมน์เดียวกัน
2. อ่านคอลัมน์ซ้ายจากบนลงล่างให้จบก่อน
3. จึงอ่านคอลัมน์ถัดไป
4. block ที่กว้างเกือบทั้งหน้าถูกใช้เป็นตัวแบ่งช่วง ไม่ทำให้คอลัมน์เชื่อมกันผิด
5. รวมบรรทัดเฉพาะเมื่ออยู่ใน OCR paragraph เดียวกันและระยะห่างแนวตั้งใกล้กัน
6. ไม่รวมกล่องคนละส่วนแม้อยู่ระดับ Y ใกล้กัน

ตัวอย่างคำสั่ง:

```powershell
python -m app.main ".\data\input\Presentation_DDC กรมควบคุมโรค.pdf" `
  --force-ocr `
  --ocr-psm 3 `
  --ocr-dpi 400 `
  --ocr-preprocess none `
  --output ".\data\output\ocr_column_test"
```

## 7. Phase 04 — Chunking และ Embedding

Phase 04 ทำหน้าที่:

- แบ่งข้อความ Phase 03 เป็น evidence chunks
- เก็บ provenance กลับไปยัง page/block/bounding box
- สร้าง normalized text
- รองรับ JSON และ JSONL
- สร้าง embedding ได้เมื่อเปิดใช้งาน

Embedding Provider ที่รองรับ:

- `hash` — baseline แบบ offline
- `sentence_transformers` — semantic embedding จริง
- `none` — ไม่สร้าง embedding

ตัวอย่าง Environment Variables:

```powershell
$env:PHASE04_EMBEDDING_PROVIDER = "sentence_transformers"
$env:PHASE04_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
$env:PHASE04_EMBEDDING_DIMENSIONS = "384"
```

ตัวอย่างคำสั่ง:

```powershell
python -m phase04.cli `
  --input ".\data\output\DOC_B568D3CE3B40_newocr.json" `
  --output ".\data\output\DOC_B568D3CE3B40_newocr_phase04_hybrid.json" `
  --embed
```

## 8. Phase 05 — Retrieval และการประเมิน

### 8.1 Retrieval

รองรับ 3 วิธี:

- `lexical` — จับความเกี่ยวข้องจากคำ
- `embedding` — cosine similarity ของ vector
- `hybrid` — รวม lexical และ semantic score

ค่า `--semantic-weight 0.7` หมายถึงให้น้ำหนัก semantic 70% และ lexical 30%

หากเลือก Hybrid แต่ embedding ใช้งานไม่ได้ ระบบ fallback เป็น lexical พร้อมแสดงข้อความแจ้ง

### 8.2 Batch และการลดเวลา

ปรับ Phase 05 จากการเรียก LLM รายเกณฑ์เป็น mini-batch:

- `--batch-size` กำหนดจำนวนเกณฑ์ต่อ request
- `--dimensions` เลือกเฉพาะ D1–D8 ที่ต้องการ
- `--max-evidence-chars` จำกัดข้อความต่อ evidence item
- `--top-k` กำหนดจำนวน evidence candidate ต่อเกณฑ์
- `--retries` retry เมื่อ JSON ไม่ครบหรือผิด schema
- `--resume` ใช้ checkpoint เดิมและข้ามเกณฑ์ที่ประเมินสำเร็จแล้ว
- เขียน output แบบ atomic หลังจบแต่ละ batch
- แสดง progress และเวลาที่ใช้ต่อ batch

ค่าที่แนะนำสำหรับ Server/CPU ที่ทรัพยากรจำกัด:

```text
top-k = 2 ถึง 5
batch-size = 2
max-evidence-chars = 800 ถึง 1200
retries = 2
```

`top-k` และ `batch-size` คนละความหมาย:

- `top-k 5` = Retrieval ส่งหลักฐานได้สูงสุด 5 chunks ต่อเกณฑ์
- `batch-size 2` = ส่งเกณฑ์ให้ LLM ครั้งละ 2 เกณฑ์

### 8.3 Dynamic Evidence Citations

- จำนวน citation ที่ LLM คืนได้สัมพันธ์กับ `top-k` และจำนวน chunk ที่ส่งจริง
- แต่ละ criterion มี evidence whitelist ของตัวเอง
- ห้ามยืม chunk ID จาก criterion อื่น
- ห้ามสร้างหรือแก้ chunk ID
- duplicate citation ถูกปฏิเสธ
- citation ที่ไม่ได้อยู่ใน evidence ของ criterion ถูกปฏิเสธและเข้า retry

### 8.4 Strength-to-Evidence Mapping

เพิ่ม optional field ต่อ criterion:

```json
{
  "strengths": ["มีนโยบายด้านนวัตกรรม"],
  "evidence_chunk_ids": ["DOC_EXAMPLE_C0001"],
  "strength_evidence_mapping": [
    {
      "strength": "มีนโยบายด้านนวัตกรรม",
      "evidence_chunk_ids": ["DOC_EXAMPLE_C0001"]
    }
  ]
}
```

กฎ validation:

- ข้อความ strength ต้องตรงกับ `strengths` ทุกตัวอักษร
- mapping อ้างได้เฉพาะ chunk ที่อยู่ใน `evidence_chunk_ids` ที่โมเดลเลือกแล้ว
- ไม่เพิ่ม citation ใหม่
- รองรับ strength หนึ่งต่อหลาย chunks และ chunk หนึ่งต่อหลาย strengths
- ถ้าไม่มีหลักฐานสนับสนุนโดยตรงให้ใช้รายการว่าง
- duplicate links ถูกรวมโดยรักษาลำดับเดิม
- unknown strength, malformed mapping และ unselected chunk จะถูกปฏิเสธและ retry

ตัวอย่าง Phase 05 ด้วย Claude เฉพาะ D1:

```powershell
python -m phase05.cli `
  --phase04 ".\data\output\DOC_B568D3CE3B40_newocr_phase04_hybrid.json" `
  --rubric "..\data\knowledge_base\iop_reference.json" `
  --output ".\data\output\DOC_B568D3CE3B40_newocr_phase05_D1_claude.json" `
  --provider anthropic `
  --model claude-sonnet-5 `
  --dimensions D1 `
  --retrieval hybrid `
  --semantic-weight 0.7 `
  --top-k 5 `
  --batch-size 2 `
  --max-evidence-chars 1200 `
  --retries 2
```

ตัวอย่าง Ollama:

```powershell
python -m phase05.cli `
  --phase04 ".\data\output\DOC_DD3C35531469_phase04.json" `
  --rubric "..\data\knowledge_base\iop_reference.json" `
  --output ".\data\output\DOC_DD3C35531469_phase05_ollama.json" `
  --provider ollama `
  --model qwen3:8b `
  --top-k 2 `
  --batch-size 2 `
  --max-evidence-chars 800 `
  --retries 2
```

## 9. Phase 06 — Summary และ Data Integrity

Phase 06 ทำหน้าที่สรุปผลจาก Phase 05 โดยไม่เปลี่ยนคะแนนหรือหลักฐานต้นทาง

- เก็บ Phase 05 ต่อ criterion ใน `phase05_snapshot`
- สร้าง digest เพื่อตรวจว่า snapshot ไม่ถูกแก้
- citation ในรายงานต้องตรงกับ `evidence_chunk_ids` ของ Phase 05
- เก็บทั้งจำนวน retrieved evidence และ selected evidence
- Summary LLM แก้ได้เฉพาะ prose ไม่สามารถเพิ่มคะแนน citation หรือ strength
- ส่ง `strength_evidence_mapping` ต่อโดยตรวจว่าไม่ถูกเพิ่มหรือแก้
- รองรับไฟล์เก่าที่ไม่มี mapping โดยใช้รายการว่าง

แนะนำให้ใช้ `--provider none` เพราะเป็น deterministic summary และไม่จำเป็นต้องเสีย API token:

```powershell
python -m phase06.cli `
  --phase05 ".\data\output\DOC_B568D3CE3B40_newocr_phase05_D1_claude.json" `
  --good-examples "..\data\knowledge_base\good_examples_kb.json" `
  --output ".\data\output\DOC_B568D3CE3B40_newocr_phase06_D1.json" `
  --provider none
```

## 10. Phase 07 — Scoring, PDF และ DOCX

Phase 07 ทำหน้าที่:

- ตรวจ integrity ของ Phase 06
- คำนวณคะแนนเฉลี่ยรายมิติ
- คำนวณคะแนนรวมองค์กร
- แปลงคะแนนเป็น Maturity Level
- สร้าง scoring JSON เพิ่มได้
- สร้างรายงาน `.pdf` หรือ `.docx`

Maturity Level ที่ใช้งาน:

- `1.0–1.7` — Initial
- `1.8–2.5` — Managed
- `2.6–3.3` — Defined
- `3.4–4.1` — Performed
- `4.2–5.0` — Mature

รองรับการตั้งค่าการปัดเศษผ่าน:

```text
IOP_SCORE_DISPLAY_DECIMALS
IOP_SCORE_CLASSIFICATION_DECIMALS
IOP_SCORE_ROUNDING
```

รายงานยังคงแสดง Strengths section เดิม และตารางหลักฐานเพิ่มคอลัมน์ `จุดแข็งที่เกี่ยวข้อง`

```text
Chunk | หน้า | ข้อความหลักฐาน | จุดแข็งที่เกี่ยวข้อง
```

- หนึ่ง chunk แสดงหลาย strengths ได้
- แถวที่ไม่มี mapping แสดง `—`
- ไฟล์ Phase 06 เก่ายังสร้างรายงานได้โดยไม่เดาความสัมพันธ์

ตัวอย่าง PDF:

```powershell
python -m phase07.cli `
  --phase06 ".\data\output\DOC_B568D3CE3B40_newocr_phase06_D1.json" `
  --output ".\data\output\DOC_B568D3CE3B40_newocr_report.pdf" `
  --scoring-output ".\data\output\DOC_B568D3CE3B40_newocr_phase07_scoring.json"
```

ตัวอย่าง DOCX:

```powershell
python -m phase07.cli `
  --phase06 ".\data\output\DOC_B568D3CE3B40_newocr_phase06_D1.json" `
  --output ".\data\output\DOC_B568D3CE3B40_newocr_report.docx"
```

หากต้องการ mapping ที่มีข้อมูล ต้องรัน Phase 05 ใหม่โดยไม่ resume ผลเก่า แล้วสร้าง Phase 06/07 ใหม่

## 11. FastAPI Web UI

หน้าเว็บรองรับ:

- อัปโหลด PDF
- เลือก Provider: Hugging Face, Ollama, OpenAI, Claude หรือไม่ใช้ LLM
- กรอก Model/Tag
- เลือกเฉพาะมิติ D1–D8
- เลือกรูปแบบรายงาน PDF, DOCX หรือทั้งสอง
- ติดตาม progress Phase 03–07
- แสดง error จาก subprocess/LLM บนหน้าเว็บ
- ดาวน์โหลด Phase 03, 04, 05, 06, scoring JSON และรายงาน

เปิดเว็บ:

```powershell
Set-Location "C:\Users\UsEr\147\AI-Document\iop_phase03_document_ingestion"
```

```powershell
& ".\.venv\Scripts\Activate.ps1"
python -m uvicorn webapp.main:app --reload
```

เปิด Browser ที่:

```text
http://127.0.0.1:8000
```

ออกจาก virtual environment:

```powershell
deactivate
```

## 12. Internal File Manager

เพิ่ม Sidebar ทางซ้ายของหน้า FastAPI โดยไม่เปลี่ยน workflow การประเมินด้านขวา

ความสามารถ:

- Desktop sidebar และ Mobile drawer
- ค้นหาด้วย Document ID, ชื่อไฟล์ และชื่อไฟล์ต้นฉบับ
- แบ่ง Source / Phase 03 / 04 / 05 / 06 / 07
- รองรับหลาย run, หลาย model และ JSON/JSONL/PDF/DOCX
- แสดงจำนวนไฟล์และพื้นที่ Managed Storage
- ดาวน์โหลดผ่าน opaque file ID โดยไม่เปิดเผย server path
- ลบรายไฟล์พร้อม confirmation
- Bulk preview และลบ Generated Outputs ทั้ง Document
- ไม่ลบ Source PDF
- ป้องกัน path traversal, symlink escape, unsafe extension และ active job
- confirmation token ของ bulk delete ใช้ครั้งเดียวและมีอายุ 5 นาที
- บันทึก audit log โดยไม่บันทึก token/path/ข้อความเอกสาร

Managed roots ค่าเริ่มต้น:

```text
Generated outputs: data/output
Source PDFs: data/input/uploads
```

Repository ไม่มีระบบ SSO/Login ที่ยืนยันได้ จึงปิด DELETE เป็นค่าเริ่มต้นแบบ fail-closed ต้องตั้ง Shared Access Token ก่อน:

```powershell
$env:IOP_FILE_MANAGER_ACCESS_TOKEN = "ใส่รหัสสุ่มที่ยาวและเดายาก"
$env:IOP_FILE_MANAGER_ID_SECRET = "ใส่รหัสสุ่มอีกค่าหนึ่ง"
python -m uvicorn webapp.main:app --reload
```

เมื่อนำขึ้น Server ควรใช้ HTTPS และวางหลัง SSO/VPN หรือ authenticated reverse proxy

รายละเอียดเพิ่มเติมอยู่ที่ `docs/file_manager.md`

## 13. Dependencies และ Virtual Environment

Dependency หลักอยู่ใน:

- `requirements.txt`
- `requirements-huggingface.txt`
- `requirements-embeddings.txt`

สร้างและเปิด virtual environment:

```powershell
Set-Location "C:\Users\UsEr\147\AI-Document\iop_phase03_document_ingestion"
py -3.13 -m venv .venv
& ".\.venv\Scripts\Activate.ps1"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

ติดตั้ง Hugging Face/Qwen เพิ่มเมื่อใช้งาน:

```powershell
python -m pip install -r requirements-huggingface.txt
```

ติดตั้ง Semantic Embedding เพิ่มเมื่อใช้งาน:

```powershell
python -m pip install -r requirements-embeddings.txt
```

แพ็กเกจ `PyMuPDF` ต้องติดตั้งใน Python/venv ตัวเดียวกับที่ใช้รัน Uvicorn หรือ CLI มิฉะนั้นจะพบ `ModuleNotFoundError: No module named 'pymupdf'`

## 14. ไฟล์สำคัญที่เพิ่มหรือแก้

### LLM

- `llm/config.py`
- `llm/provider.py`
- `.env.example`

### OCR และ Ingestion

- `app/main.py`
- `app/ingest.py`
- `app/ocr.py`
- `app/pdf_text.py`

### Phase 04–07

- `phase04/pipeline.py`
- `phase04/embeddings.py`
- `phase05/assessment.py`
- `phase05/retrieval.py`
- `phase05/cli.py`
- `phase06/summary.py`
- `phase06/validation.py`
- `phase07/scoring.py`
- `phase07/report.py`
- `phase07/docx_report.py`
- `phase07/evidence_mapping.py`

### Web และ File Manager

- `webapp/main.py`
- `webapp/file_manager.py`
- `webapp/templates/index.html`
- `webapp/static/app.css`
- `docs/file_manager.md`

### Tests

- `test_phase05.py`
- `tests/test_phase05_strength_mapping.py`
- `tests/test_phase06.py`
- `tests/test_phase07.py`
- `tests/test_phase07_scoring.py`
- `tests/test_file_manager.py`
- `tests/test_file_manager_api.py`

## 15. ผลการทดสอบล่าสุด

### Strength-to-Evidence Mapping และรายงาน

```text
21 passed
```

ครอบคลุม:

- one-to-one, one-to-many และ many-to-many mapping
- unknown strength และ unselected chunk
- duplicate normalization
- batch, retry และ resume
- Phase 06 snapshot integrity
- PDF/DOCX คอลัมน์ใหม่
- backward compatibility

### Full Test Suite

```text
94 passed, 1 failed, 1 skipped
```

Failure ที่เหลือ:

- `tests/test_phase04.py::test_chunks_keep_phase03_provenance_and_embedding`
- เกี่ยวข้องกับการรวม OCR ภาษาไทยคำว่า `การจัดการ`
- ไม่ได้เกิดจาก LLM, scoring, report หรือ File Manager

Skipped test:

- symlink security test ของ File Manager บน Windows เนื่องจากบัญชีทดสอบไม่มีสิทธิ์สร้าง symlink

Visual QA:

- PDF fixture 2 หน้าถูก render และตรวจแล้ว ไม่พบข้อความทับ ล้น หรือตัด
- DOCX ผ่าน structural tests เรื่อง 4 คอลัมน์ ความกว้าง การ wrap และข้อความ `—`
- ยังไม่ได้ visual render DOCX ใน environment นี้ เพราะ bundled runtime ไม่มี `soffice.exe`

## 16. ข้อจำกัดและสิ่งที่ควรทำต่อ

1. Strength-to-Evidence Mapping เป็นความสัมพันธ์ที่ LLM สร้าง ระบบตรวจ membership และ ID ได้ แต่ไม่สามารถพิสูจน์ความถูกต้องเชิงความหมายทั้งหมด จึงควรให้ผู้ประเมินตรวจซ้ำ
2. Ollama/Qwen บน CPU 4 Core อาจใช้เวลานาน ควรลด batch, top-k และ evidence chars หรือใช้ GPU
3. การเพิ่ม context window ไม่ได้ทำให้เร็วขึ้นและอาจใช้ RAM มากขึ้น
4. OpenAI API ต้องมีเครดิต ส่วน Claude ต้องตั้ง API key ที่ใช้งานได้
5. Job state ของ FastAPI และ active-job guard ยังอยู่ใน memory ของ Uvicorn process หากใช้หลาย worker ควรย้ายไป shared database/job store
6. File Manager viewing/download ยังใช้ access policy เดิมของเว็บ ควรวางระบบจริงหลัง HTTPS และ organizational authentication
7. ควรแก้ regression ของ Phase 04 OCR normalization ที่ยังเหลือ 1 test

## 17. Workflow สรุป

```text
PDF
  ↓
Phase 03: Native Text / OCR / Layout Ordering
  ↓
Phase 04: Chunking + Metadata + Optional Embeddings
  ↓
Phase 05: Lexical/Embedding/Hybrid Retrieval
          + LLM Assessment
          + Scores / Reasons / Citations / Strength Mapping
  ↓
Phase 06: Immutable Snapshot + Summary + Integrity Validation
  ↓
Phase 07: Dimension/Overall Scores + Maturity Level
          + PDF/DOCX Report
  ↓
FastAPI Download + Internal File Manager
```

