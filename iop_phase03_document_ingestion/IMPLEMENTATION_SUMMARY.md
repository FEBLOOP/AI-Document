# สรุปการดำเนินงาน

## 1. Qwen3-8B และ Hugging Face

- เปลี่ยนโมเดลหลักเป็น `Qwen/Qwen3-8B`
- ใช้ตัวแปรแวดล้อม `IOP_LLM_MODEL` เพื่อกำหนดโมเดล
- เพิ่ม provider `huggingface` เป็นค่าเริ่มต้นผ่าน `IOP_LLM_PROVIDER`
- เพิ่ม provider กลางใน `llm/`
  - `llm/config.py` จัดการค่าตั้งค่า LLM
  - `llm/provider.py` เรียกโมเดลจาก Hugging Face, Ollama หรือ OpenAI-compatible endpoint
- Hugging Face provider โหลด `AutoTokenizer` และ `AutoModelForCausalLM` ของ Qwen โดยตรง
- ใช้ `tokenizer.apply_chat_template(..., enable_thinking=False)` เพื่อให้ผลลัพธ์จาก Phase 05/06 เป็น JSON ที่ parse ได้
- โหลดโมเดลเพียงครั้งเดียวต่อ process

## 2. การใช้ GPU

- ติดตั้ง PyTorch CUDA build: `torch 2.11.0+cu128`
- ตรวจพบ GPU: `NVIDIA GeForce RTX 4060`
- เปิดค่าเริ่มต้น `IOP_LLM_LOAD_IN_4BIT=true`
- เมื่อใช้ CUDA จะโหลด Qwen3-8B แบบ 4-bit เพื่อลดการใช้ VRAM
- Phase 05 บังคับผลประเมินเป็น JSON แบบกระชับ เพื่อให้ Qwen จบคำตอบภายใน 512 tokens บน GPU 8 GB
- หากเปิด 4-bit แต่ PyTorch ไม่พบ CUDA โปรแกรมจะแจ้ง error ชัดเจน แทนการพยายามโหลดโมเดลเต็มบน RAM

## 3. Phase 05 และ Phase 06

- ปรับ CLI ของทั้งสอง Phase ให้รองรับ provider:
  - `huggingface`
  - `ollama`
  - `openai`
  - `none`
- `--provider huggingface` จะเรียก `Qwen/Qwen3-8B` โดยตรง
- ค่า provider และ model ที่ใช้งานจะถูกบันทึกลงใน JSON output

## 4. FastAPI Web UI

เพิ่มเว็บแอปใน `webapp/`:

- `webapp/main.py` — FastAPI routes, upload API, background pipeline และ download endpoint
- `webapp/templates/index.html` — หน้าอัปโหลดและติดตามสถานะ
- `webapp/static/app.css` — รูปแบบหน้าเว็บ

ความสามารถของหน้าเว็บ:

1. อัปโหลดไฟล์ PDF
2. รัน Phase 03 เพื่ออ่านข้อความและ OCR เมื่อจำเป็น
3. รัน Phase 04 เพื่อสร้าง evidence chunks
4. เลือก Qwen3-8B ผ่าน Hugging Face, OpenAI API หรือไม่ใช้ LLM
5. ดูสถานะของงานแบบ polling
6. ดาวน์โหลดผลลัพธ์ Phase 03, Phase 04 และ Phase 05 เป็น JSON

## 5. Dependencies และไฟล์ตั้งค่า

- เพิ่ม `requirements-huggingface.txt`
  - `torch`
  - `transformers`
  - `accelerate`
  - `safetensors`
  - `bitsandbytes`
- เพิ่ม FastAPI dependencies ใน `requirements.txt`
  - `fastapi`
  - `uvicorn[standard]`
  - `python-multipart`
- เพิ่ม `.env.example`
- อัปเดต `config.example.json`, `PHASE05_README.md` และ `PHASE06_README.md`
- เพิ่ม `WEB_README.md` สำหรับวิธีเปิดเว็บ

## 6. การตรวจสอบ

- ดาวน์โหลด Qwen tokenizer จาก Hugging Face และทดสอบ `apply_chat_template` สำเร็จ
- ตรวจพบ CUDA ผ่าน PyTorch สำเร็จ
- ทดสอบ FastAPI หน้าเว็บและ API สำเร็จ
- Focused tests ผ่าน `7 passed`
- ชุดทดสอบทั้งหมดมี 1 test ของ Phase 04 ที่ล้มเหลวเดิม ซึ่งไม่เกี่ยวกับ LLM หรือเว็บ

## 7. วิธีเปิดเว็บ

```powershell
Set-Location "C:\Users\UsEr\147\AI-Document\iop_phase03_document_ingestion"
```

```powershell
& "C:\Users\UsEr\AppData\Local\Programs\Python\Python313\python.exe" -m uvicorn webapp.main:app --reload
```

จากนั้นเปิด `http://127.0.0.1:8000`

## 8. ค่าตั้งค่าหลัก

```powershell
$env:IOP_LLM_PROVIDER = "huggingface"
$env:IOP_LLM_MODEL = "Qwen/Qwen3-8B"
$env:IOP_LLM_LOAD_IN_4BIT = "true"
$env:IOP_LLM_MAX_NEW_TOKENS = "512"
```

น้ำหนักโมเดล Qwen3-8B จะถูกดาวน์โหลดจาก Hugging Face ในครั้งแรกที่เลือกใช้ Qwen เพื่อรัน Phase 05 หรือ Phase 06.
