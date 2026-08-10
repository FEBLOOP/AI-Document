# AI engineer pipeline

```
             Company Document
                   │
                   ▼
            Document Parsing
                   │
                   ▼
            Content Chunking
                   │
                   ▼
         ┌───────────────────┐
         │ Content Classifier │
         └─────────┬─────────┘
                   │
         ┌─────────┼─────────┐
         ▼         ▼         ▼
        D1        D2        D8
         │         │         │
         ▼         ▼         ▼
       1.1       2.1       8.1
       1.2       2.2       8.2
       ...       ...       ...
         │
         ▼
   Assessment Criteria
         │
         ▼
    Evidence Matching
         │
         ▼
      Assessment
         │
         ▼
   Gap / Missing Info
```
### จุดสำคัญ
เราอาจไม่ได้ Classification แค่ระดับ **ไฟล์**

แต่ Classification สามารถเกิดได้ถึงระดับ:

 **Document → Section → Chunk → Dimension → Component → Criterion**

```
Company_Report.pdf
│
├── หน้า 1–5
│ → D1 → 1.1
│
├── หน้า 6–10
│ → D2 → 2.1
│
├── หน้า 11–15
│ → D5 → 5.2
│
└── หน้า 16–20
 → D6 → 6.1
```
ตรงนี้จะทำให้ระบบของเรามีความละเอียดมากขึ้น และสามารถบอกใน Report ได้ว่า

**Criterion 1.1.1 พบ Evidence ที่หน้า 3–4**

# 1. PDF + Text + OCR
ข้อนี้ **ต้องมีแน่นอน** เพราะเอกสารบริษัทอาจมี 2 แบบ

### แบบที่ 1: PDF ที่มี Text อยู่แล้ว
```
PDF
 ↓
PDF Text Extraction
 ↓
Text + Page Number
```
### แบบที่ 2: PDF ที่เป็น Scan / รูปภาพ
```
Scanned PDF
 ↓
Render PDF → Image
 ↓
OCR
 ↓
Text + Bounding Box + Page
```
ดังนั้น Pipeline แรกของเราจะเป็น:

```
            📄 PDF
              │
      ┌───────┴────────┐
      ▼                ▼
 Has Text?          Scanned?
      │                │
      ▼                ▼
PDF Extraction       OCR
      │                │
      └───────┬────────┘
              ▼
      Normalized Text
              +
         Page Number
              +
        Document ID
```
### แนะนำให้เก็บ Page + Location ไว้ด้วย
เพราะตอนสุดท้ายเราต้องบอกได้ว่า:

**พบหลักฐานในหน้า 15–16** 

ไม่ใช่แค่ AI บอกว่า “พบข้อมูล”

# 2. Classification — ใช้ LLM ดีไหม?
**ใช้ LLM ได้ แต่**ไม่แนะนำให้ใช้ LLM ตัวเดียวทำ Classification ทุกครั้ง

เพราะงานของเรามี Reference ชัดเจน:

```
8 Dimensions
 ↓
24 Components
 ↓
Assessment Criteria
```
## Hybrid Classification
```
Company Content
 │
 ▼
Embedding Model
 │
 ▼
ค้นหา IOP ที่ใกล้เคียง
 │
 ▼
Top-K Candidates
 │
 ▼
LLM
 │
 ▼
Final Classification
```
ตัวอย่าง:

บริษัทส่ง:

>  "องค์กรมีการกำหนดวิสัยทัศน์ด้านนวัตกรรมและแผนกลยุทธ์..." 

Embedding จะค้นเจอ:

```
Candidate 1
D1 → 1.1 Vision & Strategy
Similarity = 0.91

Candidate 2
D1 → 1.2 Shared Values
Similarity = 0.73

Candidate 3
D2 → 2.3 Target
Similarity = 0.61
```
แล้ว LLM พิจารณาเฉพาะ Candidate ที่เกี่ยวข้อง:

LLM
 ↓
D1 / Component 1.1

เพราะเราไม่ต้องให้ LLM อ่าน **IOP ทั้งหมดทุกครั้ง**

ทำให้:

-  เร็วขึ้น 
-  ค่าใช้จ่ายต่ำลง 
-  ควบคุมง่ายขึ้น 
-  ลด hallucination 
-  สามารถเก็บ similarity score ได้ 
-  ทำระบบ confidence ได้
# 3. Evidence Matching 
 **หัวใจของระบบ**

เราไม่ควรเอาไฟล์บริษัททั้งไฟล์ไปถาม LLM ว่า

>  "ตรงกับ IOP ไหม?" 

เพราะไฟล์อาจมี 100 หน้า

เราจะทำแบบนี้:

```
Company PDF
     ↓
Text Extraction
     ↓
Chunking
     ↓
Embedding
     ↓
Vector Search
     ↓
IOP Criteria
     ↓
หา Evidence ที่เกี่ยวข้อง
```
สมมติเรากำลังตรวจ:

```
IOP Criterion
1.1.1
```
ระบบค้นในเอกสารบริษัท:

```
Top Evidence

Page 12
"คณะผู้บริหารได้ร่วมกันกำหนด..."

Page 13
"วิสัยทัศน์ด้านนวัตกรรมขององค์กร..."

Page 14
"เป้าหมายการเติบโต..."
```
แล้วส่ง **เฉพาะ Evidence ที่เกี่ยวข้อง** ให้ LLM

```
┌─────────────────────────────┐
│ IOP Criterion 1.1.1         │
│                             │
│ Requirement:                │
│ [เกณฑ์จาก IOP]              │
└──────────────┬──────────────┘
               │
               ▼
       Retrieved Evidence
               │
       ┌───────┼───────┐
       ▼       ▼       ▼
     Page12  Page13  Page14
               │
               ▼
             LLM
```
# 4. Assessment
นี่คือขั้นที่ **Qwen/LLM จะมีบทบาทหลัก**

LLM ต้องไม่ได้ตอบแค่:

>  ผ่าน / ไม่ผ่าน 

แต่ต้องตอบเป็น **Structured Output**

ผมเสนอ 4 สถานะ:

### 🟢 CONSISTENT
เนื้อหาที่บริษัทส่งมา **สอดคล้องกับเกณฑ์ IOP อย่างเพียงพอ**

### 🟡 PARTIALLY_CONSISTENT
มีเนื้อหาที่เกี่ยวข้อง แต่ **ข้อมูลยังไม่ครบหรือหลักฐานยังไม่เพียงพอ**

### 🔴 INCONSISTENT
มีเนื้อหา แต่ **ขัด/ไม่สอดคล้องกับสิ่งที่ IOP กำหนด**

### ⚪ MISSING
**ไม่พบข้อมูลที่เกี่ยวข้อง**

## ตัวอย่าง
#### IOP Criterion
```
1.1.1
[เกณฑ์จาก IOP]
```
Company Evidence

```
Page 12:
"ผู้บริหารกำหนดวิสัยทัศน์ขององค์กร..."

Page 13:
"มีเป้าหมายในการเติบโต..."
```
LLM Output

```
{
  "criterion_id": "1.1.1",
  "status": "PARTIALLY_CONSISTENT",

  "evidence": [
    {
      "page": 12,
      "text": "..."
    }
  ],

  "missing": [
    "ไม่พบข้อมูลที่แสดงถึง..."
  ],

  "reason": "...",
  "confidence": 0.86
}
```
### สำคัญ
**LLM ห้ามสร้าง Evidence ขึ้นมาเอง**

ต้องตอบจากข้อความที่เราส่งให้เท่านั้น

ถ้าหาไม่เจอ:

```
status = MISSING
```
ไม่ใช่ให้ AI เดา

# 5. Knowledge Base
**แนะนำให้เก็บด้วย**

แต่ไม่อยากให้เก็บ IOP เป็น Vector DB อย่างเดียว

จะมี **2 ชั้น**

## Layer 1 — Structured Knowledge Base
เก็บโครงสร้าง IOP:

```
IOP
│
├── Dimension
│
├── Component
│
├── Criterion
│
├── Assessment Description
│
└── Supporting Documents
```
ตัวอย่าง:

```
{
  "dimension_id": "D01",
  "dimension": "Innovation Strategy",

  "component_id": "1.1",
  "component": "Vision and Strategy",

  "criterion_id": "1.1.1",

  "description": "...",

  "supporting_documents": [
    "..."
  ]
}
```
อันนี้เก็บใน **Database / JSON** ได้

# Layer 2 — Vector Knowledge Base
เอาเนื้อหาของ IOP แต่ละ Criterion ไปทำ Embedding:

```
1.1.1
 ↓
Embedding
 ↓
Vector Database

1.1.2
 ↓
Embedding
 ↓
Vector Database

1.2.1
 ↓
Embedding
 ↓
Vector Database
```
เวลา Company Document เข้ามา:

```
Company Evidence
       ↓
    Embedding
       ↓
Vector Search
       ↓
ค้น IOP Criteria ที่เกี่ยวข้อง
```
# Architecture 
ทั้งหมดจะออกมาแบบนี้:

```
               📄 Company PDF
                     │
                     ▼
        ┌────────────────────────┐
        │ Document Processing    │
        │                        │
        │ PDF Text Extraction    │
        │ OCR for Scanned PDF    │
        └────────────┬───────────┘
                     │
                     ▼
             Text + Page + Layout
                     │
                     ▼
                Chunking
                     │
                     ▼
               Embedding
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
   Classification        Evidence Search
          │                     │
          ▼                     ▼
      D1 – D8              IOP Criteria
          │                     │
          └──────────┬──────────┘
                     ▼
                  LLM
               (Qwen etc.)
                     │
                     ▼
               Assessment
                     │
    ┌────────────────┼────────────────┐
    ▼                ▼                ▼
Consistent        Partial          Missing
    │                │                │
    └────────────────┼────────────────┘
                     ▼
               Gap Detection
                     │
                     ▼
          Structured JSON Result
                     │
                     ▼
              PDF Report
```
# แล้ว Qwen อยู่ตรงไหน?
**ตรง LLM Assessment เป็นหลัก**

เช่น:

```
                    Qwen
                     │
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
Classification   Evidence      Assessment
                Reasoning
```
แต่ **ยังไม่แนะนำให้ Fine-tune Qwen ตั้งแต่แรก**

เราทำ MVP แบบ:

>  **Embedding + Retrieval + Qwen + Structured Output** 

ก่อน

แล้วค่อยทดสอบว่า:

```
Classification Accuracy
Evidence Retrieval Accuracy
Assessment Accuracy
```
ได้เท่าไหร่

ถ้า Prompt + Retrieval ยังไม่พอ → ค่อยพิจารณา Fine-tuning

# Tech Stack 
| งาน | เทคโนโลยีที่เหมาะ |
| ----- | ----- |
| PDF Text | PyMuPDF / pdfplumber |
| Scanned PDF | OCR |
| OCR | PaddleOCR / Tesseract / Cloud OCR |
| Chunking | Custom Python |
| Embedding | Multilingual Embedding Model |
| Vector DB | Chroma / Qdrant |
| Structured KB | PostgreSQL / JSON |
| LLM | <p>**Qwen**</p><p> หรือ LLM ที่เหมาะกับภาษาไทย</p> |
| Assessment | **LLM + Evidence Retrieval** |
| Output | Structured JSON |
| Report | Python + ReportLab |
| Backend | FastAPI |
# อยากให้เราแยก **Knowledge Base กับ Company Document** ออกจากกัน
```
        KNOWLEDGE BASE
               │
          IOP Reference
               │
     ┌─────────┴─────────┐
     ▼                   ▼
SQL/JSON             Vector DB
     │                   │
     └─────────┬─────────┘
               │
               ▼
            AI Engine
               ▲
               │
          Company Docs
               │
           PDF + OCR
```
แบบนี้ดีมากสำหรับโปรเจกต์ เพราะถ้าวันหนึ่ง **IOP มี Version ใหม่** เราสามารถอัปเดต Knowledge Base โดยไม่จำเป็นต้อง Train Model ใหม่ทั้งหมด

Architecture

**Hybrid AI Document Assessment System**

**OCR + Embedding + Retrieval + LLM + Structured Knowledge Base**

![image.png](https://eraser.imgix.net/workspaces/7OllzDt7ytInGTVvlubC/xc20ihk4ebZVnVJyBRQEQ6GhaIK2/image_J_Rfa_bnt07cXOkieHOcp.png?ixlib=js-3.8.0 "image.png")



