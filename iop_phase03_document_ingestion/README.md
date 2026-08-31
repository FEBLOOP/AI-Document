# IOP Phase 03 — Document Ingestion

## เป้าหมาย
PDF บริษัท -> Native Text หรือ OCR -> Normalized JSON พร้อม page/evidence metadata

## Flow
Company PDF -> Native Text Extraction -> ตรวจ text coverage -> ถ้าไม่พอ OCR -> Normalized Document JSON -> Phase 04 Chunking

## ติดตั้ง
pip install -r requirements.txt

ต้องติดตั้ง Tesseract OCR เพิ่ม และมี language data `tha` + `eng` สำหรับเอกสารภาษาไทย/อังกฤษ

## Run
python -m app.main data/input/company.pdf

บังคับ OCR:
python -m app.main data/input/company.pdf --force-ocr

OCR ค่าเริ่มต้นคือ 300 DPI, PSM 3 และ preprocessing แบบ `auto` (grayscale, contrast และ denoise)
สำหรับเอกสารที่เป็นฟอร์มหรือย่อหน้ารูปแบบสม่ำเสมอ ใช้ PSM 6:
python -m app.main data/input/company.pdf --force-ocr --ocr-psm 6

ปิด preprocessing หรือใช้ภาพขาวดำได้ด้วย `--ocr-preprocess none` หรือ `--ocr-preprocess binary`

## Output
`data/output/DOC_<SHA256_PREFIX>.json`

เก็บ `document_id`, SHA-256, page, extraction_method, OCR flag, text, block_id, source, confidence และ bounding box เพื่อให้ Phase Assessment ย้อนกลับไปหา Evidence ได้ OCR blocks มาจากตำแหน่ง block/line จริงของ Tesseract ไม่ใช่การรวมทั้งหน้า

## Design decisions
- ไม่ OCR ทุกไฟล์: อ่าน native text ก่อน เพื่อลดเวลาและลด OCR error
- ถ้า text usable น้อยกว่า 60% ของหน้า จะใช้ OCR
- Phase 03 ยังไม่จำแนก D1-D8 และยังไม่ให้คะแนน

## Next
Phase 04: Chunking + Section Detection + Metadata สำหรับ D1-D8 Classifier
